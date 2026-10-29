"""The Registrations review step as services, shared by the management view and the management API.

A not-yet-confirmed RegistrationBatch is reviewed line by line (price, exclusion, team/number/position),
given an optional overall discount and then confirmed: the invoice is numbered and emailed and every
entry with a team is placed. Cancelling the batch and marking a confirmed one paid live here too, so
management.views.RegistrationInvoice*View and api.panel.routers.registrations call one implementation.
Form validation stays with the callers (management.forms builds the line formset); these functions take
the already-validated formset.
"""

from decimal import Decimal

from django.db import transaction

from club.models import ClubMembership
from club.services.cancellation import cancel_membership
from club.services.fees import mark_as_paid
from registration.models import RegistrationDetails
from registration.services.invoicing import active_batch_entries, reconcile_membership_fees
from registration.services.notifications import confirm_and_send_invoice
from teams.models import StaffAssignment
from teams.services import place_member_on_team


def review_entries(batch):
    """The lines of the review screen: every entry of the batch except those of a cancelled membership
    (excluded ones stay, so staff can re-include them)."""
    return (
        RegistrationDetails.objects.filter(batch=batch)
        .exclude(membership__status=ClubMembership.StatusChoices.CANCELLED)
        .select_related("membership__member", "product_variant__product", "requested_team", "requested_position")
        .order_by("membership__member__last_name", "membership__member__first_name")
    )


def batch_memberships(entries) -> dict:
    """The distinct memberships behind ``entries``, keyed by id."""
    memberships = {}
    for entry in entries:
        memberships.setdefault(entry.membership_id, entry.membership)
    return memberships


def place_confirmed_entries(batch):
    """Auto-places every active entry that got a team confirmed on the review -- a player onto the team
    (teams.services.place_member_on_team, idempotent), a volunteer as staff. An entry without a team is
    skipped. Stamps the result back onto resulting_team_membership / resulting_staff_assignment so a later
    cancellation can clean up exactly this placement."""
    for entry in active_batch_entries(batch):
        if entry.requested_team_id is None:
            continue
        member = entry.membership.member
        if entry.entry_kind == RegistrationDetails.EntryKind.PLAYER:
            entry.resulting_team_membership = place_member_on_team(member, entry.requested_team, batch.season, jersey_number=entry.requested_jersey_number)
            entry.save(update_fields=["resulting_team_membership"])
        else:
            staff_assignment, _created = StaffAssignment.objects.get_or_create(team=entry.requested_team, member=member, season=batch.season, defaults={"position": entry.requested_position})
            entry.resulting_staff_assignment = staff_assignment
            entry.save(update_fields=["resulting_staff_assignment"])


def price_snapshot(entries) -> dict:
    """What each line contributes before an edit: ``{entry id: price (0 when excluded)}``. Take it *before*
    a bound formset overwrites the instances; ``confirm_review`` needs it to reconcile the fees."""
    return {entry.pk: (Decimal("0") if entry.excluded_from_invoice else entry.price) for entry in entries}


def confirm_review(batch, formset, *, before_by_entry_id, manual_discount_amount, manual_discount_note, due_in_days, request=None) -> bool | None:
    """Saves the validated line formset and the discount, keeps ClubMembership.fee_amount in step, then
    confirms and emails the invoice and places the entries. Returns None when every entry is cancelled or
    excluded (nothing is confirmed then), else whether the email went out -- confirmation and placement
    happen either way."""
    formset.save()
    batch.manual_discount_amount = manual_discount_amount or Decimal("0")
    batch.manual_discount_note = manual_discount_note
    batch.save(update_fields=["manual_discount_amount", "manual_discount_note"])
    reconcile_membership_fees(batch, before_by_entry_id)

    if not active_batch_entries(batch):
        return None

    sent = confirm_and_send_invoice(batch, due_in_days=due_in_days, request=request)
    place_confirmed_entries(batch)
    return sent


def cancel_batch(batch) -> int:
    """Withdraws a whole (unconfirmed) registration: cancels every membership it covers. Returns how many."""
    membership_ids = batch.entries.values_list("membership", flat=True).distinct()
    cancelled = 0
    for membership in ClubMembership.objects.filter(pk__in=membership_ids):
        cancel_membership(membership)
        cancelled += 1
    return cancelled


def mark_batch_paid(batch, *, recorded_by) -> int:
    """Marks every membership a confirmed registration still covers as paid. Returns how many."""
    memberships = batch_memberships(active_batch_entries(batch))
    with transaction.atomic():
        for membership in memberships.values():
            mark_as_paid(membership, recorded_by=recorded_by)
    return len(memberships)
