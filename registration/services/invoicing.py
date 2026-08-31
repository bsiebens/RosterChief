"""A registration's own invoice: one PDF per registration batch, covering
every entry in it -- not one per membership. club.services.invoicing.
DuesInvoice is strictly a OneToOneField to a single ClubMembership (staff
sends one per member's own season fee), which can't represent "everyone a
parent registered in one go, on one document" the way this needs to.

Billing is its own review step, and comes *before* onboarding review now:
submit_registration prices everything and creates each ClubMembership
immediately (unchanged, see RegistrationBatch's own docstring), but nothing
here is confirmed -- and nothing financial reaches the family, and the
registration doesn't even reach the Sign-up queue yet -- until staff reviews
it on the management Registrations screen and calls confirm_and_send_invoice
(registration.services.notifications), which stamps RegistrationBatch.
invoice_sent_at (and, for any entry with a team/position chosen there, also
places them -- see that function's own docstring). That flag is read
everywhere something would otherwise show a family money it hasn't been
billed yet, or a registration that hasn't been reviewed yet:
registration.views.RegistrationStatusView, registration.views.
RegistrationInvoiceView, mobile.views.PaymentsView/RegistrationInvoicePdfView,
management.views.SignupDashboardView. registrations_awaiting_confirmation/
membership_ids_awaiting_confirmation below are how those call sites find out
what's still pending review.

Reuses each entry's own price/discount_amount -- already the source of truth
for what was charged at submission time, though staff can edit both (and
exclude a line, or apply RegistrationBatch.manual_discount_amount) on the
Registrations review screen before confirming. subtotal/discount_amount/
total are NOT read straight off RegistrationBatch's own same-named fields:
those are set once, at submission, over every entry there ever was, and a
since-excluded or -cancelled entry (club.services.cancellation.
cancel_membership) must disappear from what's actually owed today -- see
active_batch_entries/batch_totals' own recompute.

batch_invoice_pdf is not cached to disk, unlike club.services.invoicing.
invoice_pdf/shop.services.invoices.render_invoice_pdf: it shows each
membership's early-payment offer (club.services.fees.early_payment_offer),
which is time-sensitive -- whether it's still live changes on its own the
day the deadline passes, with nothing that would trigger a cache bust. A
batch invoice is a rarely-downloaded document (one family, occasionally), so
re-rendering every time costs little and can never go stale. render_pdf/its
error type are this module's own copy, not imported from club/shop's
versions -- see club.services.invoicing.render_pdf's own docstring for why
an app's two-line PDF renderer isn't worth coupling to another app's.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership
from club.services.fees import early_payment_offer, remaining_balance, sync_fee_status
from club.services.invoicing import resolve_document_address

from ..models import RegistrationBatch, RegistrationDetails


class RegistrationInvoicePDFError(Exception):
    """Raised when WeasyPrint's native libraries aren't available."""


def render_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise RegistrationInvoicePDFError(_("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).")) from error

    return HTML(string=html).write_pdf()


def active_batch_entries(batch):
    """This batch's own entries, minus any tied to a since-cancelled
    membership or a line staff has excluded from billing (RegistrationDetails.
    excluded_from_invoice, set on the Registrations review screen) -- what's
    owed for it has changed since submission, so nothing that reads this
    (the invoice PDF below, and registrations_awaiting_confirmation/
    management.views.InvoiceListView's own registration rows) should still
    count a cancelled or excluded person's charge."""
    return list(
        batch.entries.exclude(membership__status=ClubMembership.StatusChoices.CANCELLED)
        .exclude(excluded_from_invoice=True)
        .select_related("membership__member", "product_variant__product", "requested_team", "resulting_team_membership__team", "resulting_staff_assignment__team", "resulting_staff_assignment__position")
        .order_by("membership__member__last_name", "membership__member__first_name")
    )


def batch_totals(entries, manual_discount_amount=Decimal("0")):
    """subtotal/discount_amount/total recomputed from ``entries`` (normally
    active_batch_entries(batch)'s own result) rather than trusted from
    RegistrationBatch's own same-named fields, which are set once, at
    submission, over every entry there ever was -- see active_batch_entries'
    own docstring. ``discount_amount`` is the per-entry (multi-registrant)
    portion only, matching what it always meant here -- the invoice template
    shows the batch's own manual_discount_amount as its own separate line
    rather than folding it in, so ``total`` alone subtracts both (floored at
    0: a manual discount bigger than what's left owed is a plausible
    fat-finger, not a debt to the family)."""
    subtotal = sum((entry.price for entry in entries), Decimal("0"))
    discount_amount = sum((entry.discount_amount for entry in entries), Decimal("0"))
    total = max(subtotal - discount_amount - manual_discount_amount, Decimal("0"))
    return subtotal, discount_amount, total


def reconcile_membership_fees(batch, before_by_entry_id):
    """Keeps each involved ClubMembership.fee_amount in sync with what the
    Registrations review screen just changed -- a per-line price edit or
    exclusion (an exact delta, not a from-scratch recompute: fee_amount is
    an accumulator that can carry a contribution from more than one
    registration event for the same membership, see submit_registration's
    own "adds to what's already owed" case, so recomputing it from just
    this batch's own entries would risk wiping out an unrelated one's
    share) plus the confirmation's own manual_discount_amount, split
    proportionally across whichever memberships still have an active entry
    in this batch by their own current share of what's left (the last one
    absorbs any rounding remainder so the split is always exact).

    Without this, a price edited (or a discount applied) on this screen
    would only ever be reflected in this batch's own PDF/queue total --
    Dues & billing, mobile balances, and reminders all read ClubMembership.
    fee_amount directly and would keep citing the old, higher amount.

    ``before_by_entry_id`` maps each RegistrationDetails id to what it
    contributed before this save -- 0 if it was already excluded, else its
    old price -- captured from the queryset *before* the formset bound to
    the request overwrote it. Call this once, right after formset.save()
    and after batch.manual_discount_amount has its new value saved: it
    applies a delta, not an absolute value, so calling it twice for the
    same save would double-apply both changes."""
    entries = list(batch.entries.exclude(membership__status=ClubMembership.StatusChoices.CANCELLED).select_related("membership"))

    deltas = {}
    for entry in entries:
        before = before_by_entry_id.get(entry.pk, Decimal("0"))
        after = Decimal("0") if entry.excluded_from_invoice else entry.price
        delta = after - before
        if delta:
            deltas[entry.membership_id] = deltas.get(entry.membership_id, Decimal("0")) + delta

    contributions = {}
    for entry in entries:
        if entry.excluded_from_invoice:
            continue
        contributions[entry.membership_id] = contributions.get(entry.membership_id, Decimal("0")) + entry.price

    manual_discount = batch.manual_discount_amount or Decimal("0")
    total_contribution = sum(contributions.values())
    discount_shares = {}
    if manual_discount > 0 and total_contribution > 0:
        remaining = manual_discount
        membership_ids = list(contributions.keys())
        for index, membership_id in enumerate(membership_ids):
            if index == len(membership_ids) - 1:
                share = remaining
            else:
                share = (manual_discount * contributions[membership_id] / total_contribution).quantize(Decimal("0.01"))
                remaining -= share
            discount_shares[membership_id] = share

    affected_ids = set(deltas) | set(discount_shares)
    if not affected_ids:
        return
    memberships = {membership.pk: membership for membership in ClubMembership.objects.filter(pk__in=affected_ids)}
    for membership_id in affected_ids:
        membership = memberships[membership_id]
        membership.fee_amount = max(membership.fee_amount + deltas.get(membership_id, Decimal("0")) - discount_shares.get(membership_id, Decimal("0")), Decimal("0"))
        membership.save(update_fields=["fee_amount"])
        sync_fee_status(membership)


def batch_early_payment_offer(batch, entries, total):
    """One combined "pay by X to save Y" figure for the whole registration,
    not one per person -- computed against ``total`` (already net of the
    multi-registrant discount *and* manual_discount_amount, see
    batch_totals), so a manual discount applied at confirmation is still
    reflected here instead of silently ignored.

    Aggregated from each distinct membership's own still-live
    early_payment_offer (club.services.fees) -- their own deadline/discount
    come from whichever product each of them registered under, which can
    genuinely differ. Returns None if nobody in the batch has a live offer
    at all. When deadlines differ across people, the earliest one is shown:
    paying after it means at least one person's own discount has already
    lapsed."""
    memberships = {}
    for entry in entries:
        memberships.setdefault(entry.membership_id, entry.membership)
    offers = [offer for membership in memberships.values() if (offer := early_payment_offer(membership)) is not None]
    if not offers:
        return None
    discount = sum((offer["discount"] for offer in offers), Decimal("0"))
    deadline = min(offer["deadline"] for offer in offers)
    return {"deadline": deadline, "discount": discount, "discounted_total": max(total - discount, Decimal("0"))}


def batch_invoice_pdf(batch) -> bytes:
    document_address = resolve_document_address(batch.club)
    entries = active_batch_entries(batch)
    subtotal, discount_amount, total = batch_totals(entries, batch.manual_discount_amount)
    early_payment = batch_early_payment_offer(batch, entries, total)

    html = render_to_string(
        "registration/batch_invoice_pdf.html",
        {
            "club": batch.club,
            "batch": batch,
            "entries": entries,
            "subtotal": subtotal,
            "discount_amount": discount_amount,
            "total": total,
            "manual_discount_amount": batch.manual_discount_amount,
            "manual_discount_note": batch.manual_discount_note,
            "document_address": document_address,
            "early_payment": early_payment,
        },
    )
    return render_pdf(html)


def _search_registration_details(details, search):
    """Same shape as management.views.InvoiceListView's own
    _search_by_member_or_family, duplicated rather than imported -- that one
    lives on a view class in a higher-level app this one must not depend on."""
    return (
        details.filter(membership__member__first_name__icontains=search)
        | details.filter(membership__member__last_name__icontains=search)
        | details.filter(membership__member__family_memberships__family__name__icontains=search)
        | details.filter(membership__member__family_memberships__family__memberships__member__last_name__icontains=search)
    ).distinct()


def registrations_awaiting_confirmation(club, search=""):
    """One row per RegistrationBatch not yet confirmed (invoice_sent_at is
    still unset) that has at least one active, billable entry -- the
    Registrations review queue's own data source. A batch whose every entry
    is cancelled or already excluded doesn't appear: there's nothing left to
    confirm. Returns a list of {"batch", "entries", "total"} dicts, newest
    first."""
    details = (
        RegistrationDetails.objects.filter(batch__club=club, batch__invoice_sent_at__isnull=True)
        .exclude(membership__status=ClubMembership.StatusChoices.CANCELLED)
        .exclude(excluded_from_invoice=True)
        .select_related("membership__member", "batch", "product_variant__product")
    )
    if search:
        details = _search_registration_details(details, search)

    by_batch = {}
    for detail in details:
        by_batch.setdefault(detail.batch_id, {"batch": detail.batch, "entries": []})["entries"].append(detail)

    rows = []
    for grouped in by_batch.values():
        entries = grouped["entries"]
        _subtotal, _discount_amount, total = batch_totals(entries, grouped["batch"].manual_discount_amount)
        rows.append({"batch": grouped["batch"], "entries": entries, "total": total})
    rows.sort(key=lambda row: row["batch"].created, reverse=True)
    return rows


def confirm_invoice(batch, *, due_in_days):
    """Allocates batch.invoice_number (retrying on a numbering collision,
    same shape as club.models.DuesInvoice.save's own retry loop) and stamps
    invoice_sent_at/invoice_due_date -- the state half of "confirm and send
    an invoice". Pure state mutation, no email -- see registration.services.
    notifications.confirm_and_send_invoice for the one entry point that does
    both (it lives there, not here, so this module never has to import the
    notifications module that itself needs batch_invoice_pdf from here)."""
    for attempt in range(5):
        batch.invoice_number = batch.generate_invoice_number()
        try:
            with transaction.atomic():
                batch.invoice_sent_at = timezone.now()
                batch.invoice_due_date = timezone.localdate() + timedelta(days=due_in_days)
                batch.save(update_fields=["invoice_number", "invoice_sent_at", "invoice_due_date"])
            return
        except IntegrityError:
            batch.invoice_number = ""
            if attempt == 4:
                raise


def registration_invoices_due_for_reminder(club, today=None):
    """Every confirmed, still-outstanding registration invoice whose due
    date has passed -- same shape as club.services.invoicing.
    invoices_due_for_reminder, but "outstanding" is read off the sum of
    remaining_balance() across the batch's own active memberships rather
    than a single membership's fee_status, since one invoice can cover
    several people."""
    today = today or timezone.now().date()
    batches = RegistrationBatch.objects.filter(club=club, invoice_sent_at__isnull=False, invoice_due_date__lt=today)
    due = []
    for batch in batches:
        memberships = {}
        for entry in active_batch_entries(batch):
            memberships.setdefault(entry.membership_id, entry.membership)
        if any(remaining_balance(membership) > 0 for membership in memberships.values()):
            due.append(batch)
    return due


def membership_ids_awaiting_confirmation(club):
    """Every ClubMembership id with at least one RegistrationDetails entry
    still tied to an unconfirmed batch -- used by mobile.views.PaymentsView/
    HomeView to hold back a balance nobody's reviewed yet, and by management.
    views.SignupDashboardView to hold the whole registration back from the
    Sign-up queue until it's been billed. A membership with no registration
    entries at all (manually created) is never in this set, so non-
    registration dues/sign-ups are unaffected."""
    return set(RegistrationDetails.objects.filter(membership__club=club, batch__invoice_sent_at__isnull=True).values_list("membership_id", flat=True))
