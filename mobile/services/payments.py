"""The "Payments & dues" overview for a set of managed people -- shared by
mobile.views.PaymentsView and the app API's ``GET /payments``.

A membership billed through a *confirmed* registration is grouped with every other
managed person that registration covers: one balance, one early-payment offer and one
invoice, matching the one invoice that was sent. A membership with no confirmed
registration behind it (created by hand) stands alone.
"""

from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from club.services.access import current_season
from club.services.fees import open_dues_rows
from registration.services.invoicing import active_batch_entries, batch_early_payment_offer, batch_totals, membership_ids_awaiting_confirmation


def payments_overview(club, people) -> list[dict]:
    """``[{"kind": "standalone", "row": dues_row} | {"kind": "registration", "group":
    {"batch", "rows", "total_balance", "early_payment", "member_names"}}]``. Each dues row
    (club.services.fees.open_dues_rows) also carries its RegistrationDetails as
    ``entries``. Balances nobody has reviewed yet are held back, as on Home."""
    season = current_season(club)
    awaiting_confirmation = membership_ids_awaiting_confirmation(club)
    # include_zero=True: a sibling already settled still shows its own lines inside a
    # combined registration receipt, or the lines wouldn't add up to the total. A
    # standalone membership has no total to reconcile, so it's filtered to balance > 0.
    dues_rows = [row for row in open_dues_rows(club, people, season, include_zero=True) if row["membership"].pk not in awaiting_confirmation]

    registration_groups = {}
    standalone_rows = []
    for row in dues_rows:
        entries = list(row["membership"].registration_details.select_related("product_variant__product", "requested_team", "batch"))
        row["entries"] = entries
        # Almost always one batch: a person registered twice in a season (a second team
        # added later) spans two. Only a confirmed batch (invoice sent) groups the row.
        batch = next((entry.batch for entry in entries if entry.batch.invoice_sent_at is not None), None)
        if batch is None:
            if row["balance"] > 0:
                standalone_rows.append(row)
            continue
        group = registration_groups.setdefault(batch.pk, {"batch": batch, "rows": [], "total_balance": Decimal("0")})
        group["rows"].append(row)
        group["total_balance"] += row["balance"]

    display_rows = [{"kind": "standalone", "row": row} for row in standalone_rows]
    for group in registration_groups.values():
        entries = active_batch_entries(group["batch"])
        _subtotal, _discount_amount, total = batch_totals(entries, group["batch"].manual_discount_amount)
        group["early_payment"] = batch_early_payment_offer(group["batch"], entries, total)
        group["member_names"] = ", ".join(row["membership"].member.first_name for row in group["rows"])
        display_rows.append({"kind": "registration", "group": group})
    return display_rows


def payment_cards(display_rows, *, me=None, show_names=False) -> list[dict]:
    """The overview shaped for the member web app's Payments screen (the same shape the native
    app gets): one card per item with per-person blocks of lines. ``show_names`` is whether the
    account manages anyone besides itself (a block carries a name when it does, or when the card
    covers several people)."""
    cards = []
    for display in display_rows:
        if display["kind"] == "registration":
            group = display["group"]
            batch = group["batch"]
            rows = group["rows"]
            card = {
                "kind": "registration",
                "batch": batch,
                "due_date": batch.invoice_due_date,
                "total": group["total_balance"],
                "manual_discount": batch.manual_discount_amount if batch.manual_discount_amount > 0 else None,
                "manual_discount_note": batch.manual_discount_note,
                "early_payment": group["early_payment"],
                "invoice": None,
            }
        else:
            row = display["row"]
            rows = [row]
            invoice = row["invoice"]
            card = {"kind": "membership", "batch": None, "due_date": invoice.due_date if invoice else None, "total": row["balance"], "manual_discount": None, "manual_discount_note": "", "early_payment": row["early_payment"], "invoice": invoice}
        card["members"] = [_member_block(row, me=me, show_name=show_names or len(rows) > 1) for row in rows]
        cards.append(card)
    return cards


def _member_block(row, *, me, show_name) -> dict:
    member = row["membership"].member
    lines = []
    for entry in row["entries"]:
        label = f"{entry.product_variant.product.name} — {entry.product_variant.name}" if entry.product_variant_id else str(entry.get_entry_kind_display())
        lines.append({"label": label, "team": str(entry.requested_team) if entry.requested_team_id else "", "price": entry.price, "discount": entry.discount_amount if entry.discount_amount > 0 else None})
    if not lines:
        # A membership billed by hand has no registration entries: its whole balance is one line.
        lines.append({"label": _("Season dues"), "team": "", "price": row["balance"], "discount": None})
    return {"member": member, "is_me": me is not None and member.pk == me.pk, "show_name": show_name, "lines": lines, "balance": row["balance"]}
