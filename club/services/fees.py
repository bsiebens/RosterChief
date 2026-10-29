"""Recording money received against a membership's fee.

Mirrors billing.services.dues.record_payment for a different kind of money: a
member's own club fee, not the club's platform subscription. amount_paid is kept in
step here, never recomputed by re-aggregating FeePayment on every read.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F
from django.utils import timezone

from club.models import ClubMembership, DuesInvoice, FeePayment


def effective_fee_amount(membership, when=None):
    """What's actually owed, accounting for an early-payment discount
    (membership.early_payment_deadline/early_payment_discount, set by
    registration.services.pricing when a matched Product.early_bird_discount_*
    condition can't be confirmed until the fee is paid). ``when`` defaults to
    today; record_payment/sync_fee_status pass the triggering payment's own
    paid_at date instead, so a payment made before the deadline is judged
    against it even if recorded/backdated later."""
    when = when or timezone.localdate()
    if membership.early_payment_deadline is not None and when <= membership.early_payment_deadline:
        return max(membership.fee_amount - membership.early_payment_discount, Decimal("0.00"))
    return membership.fee_amount


def net_balance(membership, when=None):
    """The real, unfloored difference between what's owed and what's been
    paid -- negative once a credit (e.g. a negative per-line price on the
    Registrations review screen, or simply overpayment) outweighs the fee.
    remaining_balance below is the "how much is still owed" figure used
    everywhere collection/reminder logic cares about (never negative -- a
    family is never chased for negative money); this is for a display that
    needs the real signed number instead, e.g. the registration status
    page's own per-person balance line, so a credit doesn't just silently
    read the same as "fully settled"."""
    return effective_fee_amount(membership, when) - membership.amount_paid


def remaining_balance(membership, when=None):
    return max(net_balance(membership, when), Decimal("0.00"))


def early_payment_offer(membership, when=None):
    """Whether an early-payment discount is still live for ``membership`` --
    ``None`` once its deadline has passed (or none was ever set), otherwise a
    dict with ``deadline``, ``discount`` and ``discounted_total`` (fee_amount
    minus the discount, floored at 0, same arithmetic as effective_fee_amount
    -- this is the single place both read that condition from, so the mobile
    Payments & dues page and the registration status page can never disagree
    about whether the offer is still on)."""
    when = when or timezone.localdate()
    if membership.early_payment_deadline is None or when > membership.early_payment_deadline:
        return None
    return {
        "deadline": membership.early_payment_deadline,
        "discount": membership.early_payment_discount,
        "discounted_total": max(membership.fee_amount - membership.early_payment_discount, Decimal("0.00")),
    }


def open_dues_rows(club, people, season, *, include_zero=False):
    """Every season-dues row still owed by ``people`` in ``season`` -- shared by
    mobile's Home dues card and its Payments & dues screen so the two never
    drift out of sync on what counts as "still open". WAIVED and CANCELLED
    memberships are excluded regardless -- a cancelled membership has no
    active claim on the family any more, whatever balance it was left
    carrying.

    A fully-settled (zero) balance is also excluded by default -- Home's own
    dues card and the "Payments & dues" nav badge both want "still owed",
    not a parade of already-paid people. ``include_zero=True`` keeps them
    instead: mobile.views.PaymentsView passes it when building a combined
    registration receipt, where every sibling that registration covers has
    to show its own line items regardless of whether *that one person's* own
    balance happens to already be zero (already paid, or priced at 0/net
    negative after a credit) -- dropping them silently broke the "do these
    numbers add up to the total" check a family naturally tries against a
    receipt."""
    if season is None or not people:
        return []

    memberships = ClubMembership.objects.filter(club=club, member__in=people, season=season).exclude(fee_status=ClubMembership.FeeStatus.WAIVED).exclude(status=ClubMembership.StatusChoices.CANCELLED).select_related("dues_invoice", "member")
    rows = []
    for membership in memberships:
        balance = remaining_balance(membership)
        if balance > 0 or include_zero:
            rows.append({"membership": membership, "balance": balance, "invoice": getattr(membership, "dues_invoice", None), "early_payment": early_payment_offer(membership)})
    return rows


def record_payment(membership, *, amount, method=FeePayment.Method.BANK_TRANSFER, reference="", note="", recorded_by=None):
    """Record money received against one membership's fee. Several payments may
    land on one membership -- a family paying in two installments must not read as
    unpaid. Updates amount_paid and re-syncs fee_status to match; membership.status
    is untouched -- see sync_fee_status."""
    payment = FeePayment.objects.create(membership=membership, amount=amount, method=method, reference=reference, note=note, recorded_by=recorded_by)

    membership.amount_paid = F("amount_paid") + amount
    membership.save(update_fields=["amount_paid"])
    membership.refresh_from_db(fields=["amount_paid"])
    sync_fee_status(membership, when=payment.paid_at.date())

    return payment


def mark_as_paid(membership, *, recorded_by=None):
    """The "settle this one" action behind both the per-row and bulk buttons. If
    there's a real remaining balance, records it as a payment (auditable, shows up
    in history); if fee_amount was never priced (remaining is 0), just flips the
    flags directly -- there's no real transaction to log."""
    remaining = remaining_balance(membership)
    if remaining > 0:
        record_payment(membership, amount=remaining, method=FeePayment.Method.OTHER, note="Marked as paid", recorded_by=recorded_by)
    else:
        sync_fee_status(membership, force_paid=True)


def mark_many_as_paid(club, membership_ids, *, recorded_by=None):
    """Settle every membership of ``club`` among ``membership_ids`` via ``mark_as_paid``, atomically.
    Returns how many were updated (ids of other clubs are ignored)."""
    count = 0
    with transaction.atomic():
        for membership in ClubMembership.objects.filter(pk__in=membership_ids, club=club):
            mark_as_paid(membership, recorded_by=recorded_by)
            count += 1
    return count


def season_memberships(club, season, *, fee_status="not_paid", status="all", team_id="", q=""):
    """The season's member-kind memberships for the fee list, by last name then first name.

    ``kind=MEMBER`` only: a guardian holds no membership and owes no fee. ``fee_status``
    ``not_paid`` (default) is "still owes something" -- unpaid or partially paid; waived counts as
    paid, and a cancelled membership is excluded regardless of its stale fee_status (nothing is owed
    on it any more). ``all`` or a single fee status otherwise. ``q`` also matches family members'
    surnames and the registration's invoice number or contact.
    """
    if season is None:
        return ClubMembership.objects.none()
    memberships = ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER).select_related("member", "season").order_by("member__last_name", "member__first_name")

    if fee_status == "not_paid":
        memberships = memberships.exclude(fee_status__in=[ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.WAIVED]).exclude(status=ClubMembership.StatusChoices.CANCELLED)
    elif fee_status and fee_status != "all":
        memberships = memberships.filter(fee_status=fee_status)
    if status and status != "all":
        memberships = memberships.filter(status=status)
    if team_id:
        memberships = memberships.filter(member__team_memberships__team_id=team_id, member__team_memberships__team__club=club, member__team_memberships__season=season)

    term = (q or "").strip()
    if term:
        memberships = (
            memberships.filter(member__first_name__icontains=term)
            | memberships.filter(member__last_name__icontains=term)
            | memberships.filter(member__email__icontains=term)
            | memberships.filter(member__user__email__icontains=term)
            | memberships.filter(member__family_memberships__family__club=club, member__family_memberships__family__name__icontains=term)
            | memberships.filter(member__family_memberships__family__club=club, member__family_memberships__family__memberships__member__last_name__icontains=term)
            | memberships.filter(registration_details__batch__invoice_number__icontains=term)
            | memberships.filter(registration_details__batch__contact_first_name__icontains=term)
            | memberships.filter(registration_details__batch__contact_last_name__icontains=term)
            | memberships.filter(registration_details__batch__contact_email__icontains=term)
        )
    return memberships.distinct()


def sync_fee_status(membership, *, force_paid=False, when=None):
    if membership.fee_status == ClubMembership.FeeStatus.WAIVED:
        return  # manual, independent of payments -- this never overrides it

    effective_amount = effective_fee_amount(membership, when)
    if force_paid or (effective_amount > 0 and membership.amount_paid >= effective_amount):
        new_status = ClubMembership.FeeStatus.PAID
    elif membership.amount_paid > 0:
        new_status = ClubMembership.FeeStatus.PARTIALLY_PAID
    else:
        new_status = ClubMembership.FeeStatus.UNPAID

    # fee_status only -- membership.status is never touched here. Paying in full
    # used to also flip status straight to ACTIVE on its own; now that's exclusively
    # club.services.onboarding.approve_one/approve_all_clean's call, so a paid-up
    # membership still waits on that deliberate admin step. See OnboardingRequirement's
    # docstring (club/models.py) for why.
    membership.fee_status = new_status
    membership.save(update_fields=["fee_status"])


def fee_kpis(club, season) -> dict:
    """The fee list's headline numbers for ``season`` (the *current* season on the web screen).

    Cancelled memberships are excluded: nothing is genuinely owed (or settled) on one any more, so
    it shouldn't inflate Unpaid/Registered/Paid rate -- same reasoning as ``season_memberships``'
    ``not_paid`` filter. ``overdue_invoices`` is what "Send reminders" is about to cover: dues
    invoices plus confirmed registration invoices past their due date.
    """
    # Local imports: registration depends on club, not the other way round.
    from club.services.invoicing import invoices_due_for_reminder
    from registration.services.invoicing import registration_invoices_due_for_reminder

    counts = {}
    if season is not None:
        counts = {
            row["fee_status"]: row["count"] for row in ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER).exclude(status=ClubMembership.StatusChoices.CANCELLED).values("fee_status").annotate(count=Count("id"))
        }
    paid = counts.get(ClubMembership.FeeStatus.PAID, 0)
    partial = counts.get(ClubMembership.FeeStatus.PARTIALLY_PAID, 0)
    unpaid = counts.get(ClubMembership.FeeStatus.UNPAID, 0)
    waived = counts.get(ClubMembership.FeeStatus.WAIVED, 0)
    total = paid + partial + unpaid + waived
    overdue = (invoices_due_for_reminder(club).count() + len(registration_invoices_due_for_reminder(club))) if season is not None else 0
    return {
        "total": total,
        "paid": paid,
        "partial": partial,
        "unpaid": unpaid,
        "waived": waived,
        "paid_rate": round(100 * (paid + waived) / total) if total else None,
        "overdue_invoices": overdue,
    }


def annotate_fee_rows(club, memberships) -> list:
    """Attach what the fee list and its PDF show to each membership (a list is returned).

    Everything is fetched in a handful of queries and attached in Python, never resolved per row
    (``Family.guardians``/``.children`` would ignore the club/season/filter scoping). Sets
    ``member.family_memberships_display``, ``remaining_balance_display``, ``dues_invoice`` (or
    None), ``registration_batch`` / ``registration_invoice_batch`` (the latter only when there is
    no dues invoice: that is the more deliberate record of the two), ``registration_invoice_is_overdue``
    and ``email_rows`` (own email first, then each guardian's labelled by role).
    """
    from django.utils import timezone

    from members.models import FamilyMembership
    from members.services.family import family_contacts
    from registration.models import RegistrationDetails

    memberships = list(memberships)
    members = [membership.member for membership in memberships]
    # family__club=club: Member is global -- without this, a member also in another club would show that club's family here.
    family_memberships_by_member_id = {}
    for fm in FamilyMembership.objects.filter(member__in=members, family__club=club).select_related("family"):
        family_memberships_by_member_id.setdefault(fm.member_id, []).append(fm)
    invoices_by_membership_id = {invoice.membership_id: invoice for invoice in DuesInvoice.objects.filter(membership__in=memberships)}
    registration_batches_by_membership_id = {}
    for details in RegistrationDetails.objects.filter(membership__in=memberships, batch__invoice_sent_at__isnull=False).select_related("batch"):
        registration_batches_by_membership_id.setdefault(details.membership_id, details.batch)

    today = timezone.localdate()
    for membership in memberships:
        membership.member.family_memberships_display = family_memberships_by_member_id.get(membership.member_id, [])
        membership.remaining_balance_display = remaining_balance(membership)
        # Plain local lookups, not membership.dues_invoice: assigning None to a reverse-OneToOne
        # descriptor doesn't make a later read return None (it hits the DB and raises).
        dues_invoice = invoices_by_membership_id.get(membership.pk)
        membership.dues_invoice = dues_invoice
        registration_batch = registration_batches_by_membership_id.get(membership.pk)
        membership.registration_batch = registration_batch
        membership.registration_invoice_batch = None if dues_invoice else registration_batch
        membership.registration_invoice_is_overdue = bool(registration_batch and registration_batch.invoice_due_date and registration_batch.invoice_due_date < today and membership.fee_status != ClubMembership.FeeStatus.PAID)
        own_email = membership.member.contact_email
        email_rows = [{"email": own_email, "label": None}] if own_email else []
        for contact in family_contacts(membership.member, membership.club):
            if contact["email"].lower() == (own_email or "").lower():
                continue
            email_rows.append({"email": contact["email"], "label": contact["role_label"]})
        membership.email_rows = email_rows
    return memberships
