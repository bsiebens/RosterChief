"""The club's invoice list: shop order, membership dues and registration invoices as one row shape.

Different models with no shared table, so each kind is fetched (search/type-filtered in the
database, one query per kind), normalised into the same row and merged newest first in Python --
fine at one club's scale. Shared by ``management.views.InvoiceListView`` and the API.

A row is a dict: ``kind`` (``order`` / ``dues`` / ``registration``), ``kind_label``, ``number``,
``billed_to`` (a Member or None) and ``billed_to_name`` (registration only), ``amount``, ``date``,
``status_label``, ``status_css`` and ``ref_id`` -- the pk of the order, membership or registration
batch the row opens (callers build their own detail/download links from it).
"""

from datetime import date

from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership, DuesInvoice
from registration.models import RegistrationBatch
from registration.services.invoicing import active_batch_entries, batch_totals
from shop.models import Invoice, Order

KINDS = ("order", "dues", "registration")


def order_invoice_status(order):
    """Money, not fulfilment: the order's own page has the fulfilment badge."""
    if order.payment_status == Order.PaymentStatus.PAID:
        return order.get_payment_status_display(), "badge-success"
    if order.payment_status == Order.PaymentStatus.PARTIALLY_PAID:
        return order.get_payment_status_display(), "badge-warning"
    if order.payment_status == Order.PaymentStatus.REFUNDED:
        return order.get_payment_status_display(), "badge-error"
    return order.get_payment_status_display(), "badge-neutral"


def dues_invoice_status(invoice):
    if invoice.is_paid:
        return _("Paid"), "badge-success"
    if invoice.is_overdue:
        return _("Overdue"), "badge-error"
    return _("Sent"), "badge-neutral"


def registration_payment_code(memberships) -> str:
    """Machine code of a registration's payment state: ``paid`` (everyone paid or waived), ``partial`` (mixed or
    partially paid) or ``unpaid``. The label next to it is ``registration_invoice_status``."""
    statuses = {membership.fee_status for membership in memberships}
    if statuses <= {ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.WAIVED}:
        return "paid"
    if len(statuses) > 1 or ClubMembership.FeeStatus.PARTIALLY_PAID in statuses:
        return "partial"
    return "unpaid"


def registration_invoice_status(memberships):
    """``(label, css class)`` for the registration invoice rows."""
    code = registration_payment_code(memberships)
    if code == "paid":
        return _("Paid"), "badge-success"
    if code == "partial":
        return _("Partially paid"), "badge-warning"
    return _("Unpaid"), "badge-error"


def _search_by_member_or_family(queryset, prefix, search, club):
    """Also matches by family: "Smith" finds every invoice of a family named Smith or including
    anyone surnamed Smith. ``club`` scopes the family match itself -- the member behind ``prefix``
    is global, so without it a match could come from another club's family."""
    return (
        queryset.filter(**{f"{prefix}__first_name__icontains": search})
        | queryset.filter(**{f"{prefix}__last_name__icontains": search})
        | queryset.filter(**{f"{prefix}__family_memberships__family__club": club, f"{prefix}__family_memberships__family__name__icontains": search})
        | queryset.filter(**{f"{prefix}__family_memberships__family__club": club, f"{prefix}__family_memberships__family__memberships__member__last_name__icontains": search})
    ).distinct()


def order_rows(club, search):
    invoices = Invoice.objects.filter(club=club).select_related("order__purchaser")
    if search:
        invoices = _search_by_member_or_family(invoices, "order__purchaser", search, club)
    rows = []
    for invoice in invoices:
        status_label, status_css = order_invoice_status(invoice.order)
        rows.append(
            {
                "kind": "order",
                "kind_label": _("Order"),
                "number": invoice.number,
                "billed_to": invoice.order.purchaser,
                "amount": invoice.order.total,
                "date": invoice.issued_at.date() if invoice.issued_at else None,
                "status_label": status_label,
                "status_css": status_css,
                "ref_id": invoice.order.pk,
            }
        )
    return rows


def dues_rows(club, search):
    # A since-cancelled membership's invoice stays on record but nothing is owed on it any more.
    invoices = DuesInvoice.objects.filter(club=club, sent_at__isnull=False).exclude(membership__status=ClubMembership.StatusChoices.CANCELLED).select_related("membership__member")
    if search:
        invoices = _search_by_member_or_family(invoices, "membership__member", search, club)
    rows = []
    for invoice in invoices:
        status_label, status_css = dues_invoice_status(invoice)
        rows.append(
            {
                "kind": "dues",
                "kind_label": _("Dues"),
                "number": invoice.number,
                "billed_to": invoice.membership.member,
                "amount": invoice.amount,
                "date": invoice.sent_at.date() if invoice.sent_at else None,
                "status_label": status_label,
                "status_css": status_css,
                "ref_id": invoice.membership.pk,
            }
        )
    return rows


def registration_rows(club, search):
    """One row per *confirmed* registration invoice (``invoice_sent_at`` set), not per membership.
    A still-unconfirmed registration is on the review queue instead."""
    batches = RegistrationBatch.objects.filter(club=club, invoice_sent_at__isnull=False).select_related("contact_member").order_by("-invoice_sent_at")
    if search:
        batches = (
            batches.filter(contact_first_name__icontains=search)
            | batches.filter(contact_last_name__icontains=search)
            | batches.filter(entries__membership__member__first_name__icontains=search)
            | batches.filter(entries__membership__member__last_name__icontains=search)
        ).distinct()
    rows = []
    for batch in batches:
        entries = active_batch_entries(batch)
        if not entries:
            continue  # everyone in it has since been cancelled: nothing left owed
        memberships = {}
        for entry in entries:
            memberships.setdefault(entry.membership_id, entry.membership)
        _subtotal, _discount_amount, total = batch_totals(entries, batch.manual_discount_amount)
        status_label, status_css = registration_invoice_status(memberships.values())
        rows.append(
            {
                "kind": "registration",
                "kind_label": _("Registration"),
                "number": batch.invoice_number,
                # The Member the submission resolved; when that's gone the row still shows the typed contact.
                "billed_to": batch.contact_member,
                "billed_to_name": f"{batch.contact_first_name} {batch.contact_last_name}".strip() or batch.contact_email,
                "amount": total,
                "date": batch.invoice_sent_at.date(),
                "status_label": status_label,
                "status_css": status_css,
                "ref_id": batch.pk,
            }
        )
    return rows


def invoice_rows(club, *, kind="all", search="", include_financial=True) -> list[dict]:
    """Every invoice row of ``club`` matching ``kind`` (``all`` or one of ``KINDS``) and ``search``,
    newest first. ``include_financial=False`` (a shop manager who isn't a club admin) leaves out the
    dues and registration rows, whatever ``kind`` says."""
    search = (search or "").strip()
    rows = []
    if kind in ("all", "order"):
        rows.extend(order_rows(club, search))
    if include_financial and kind in ("all", "dues"):
        rows.extend(dues_rows(club, search))
    if include_financial and kind in ("all", "registration"):
        rows.extend(registration_rows(club, search))
    rows.sort(key=lambda row: row["date"] or date.min, reverse=True)
    return rows
