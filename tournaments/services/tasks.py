"""Claiming a slot on a tournament volunteer shift -- a straight mirror of
``events.services.tasks.claim_task``, capacity-checked the same
select-for-update way (see that module's own docstring: two people
confirming at the same moment can't both squeeze past the last slot).
"""

from datetime import timedelta

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from ..models import TournamentTask, TournamentTaskClaim


class TaskClaimError(Exception):
    """A task slot could not be claimed."""


@transaction.atomic
def claim_task(task: TournamentTask, member) -> TournamentTaskClaim:
    """Claim one slot of `task` for `member`. Raises TaskClaimError if every
    slot is already taken, or `member` has already claimed one."""
    task = TournamentTask.objects.select_for_update().get(pk=task.pk)

    if task.claims.filter(member=member).exists():
        raise TaskClaimError(_("%(member)s has already claimed this.") % {"member": member})

    if task.claims.count() >= task.needed_quantity:
        raise TaskClaimError(_("This is already fully covered."))

    return TournamentTaskClaim.objects.create(task=task, member=member)


def unclaim_task(claim: TournamentTaskClaim):
    claim.delete()


def generate_task_shifts(tournament, *, title: str, location_label: str, starts_at, ends_at, shift_minutes: int, needed_quantity: int, created_by=None) -> list[TournamentTask]:
    """Bulk-creates one ``TournamentTask`` per ``shift_minutes``-sized slice
    between ``starts_at`` and ``ends_at`` -- e.g. 09:00-12:00 in 30-minute
    slices makes 6 tasks, each inheriting the same title/location/
    needed_quantity. The alternative to filling in the same "Gate duty,
    09:00-09:30", "Gate duty, 09:30-10:00", ... form over and over by hand,
    which is exactly what staff were doing before this existed. A final
    slice that would run past ``ends_at`` is dropped rather than shortened
    -- every generated shift is the same length, none get silently
    truncated."""
    tasks = []
    cursor = starts_at
    delta = timedelta(minutes=shift_minutes)
    while cursor + delta <= ends_at:
        tasks.append(TournamentTask(tournament=tournament, title=title, location_label=location_label, starts_at=cursor, ends_at=cursor + delta, needed_quantity=needed_quantity, created_by=created_by))
        cursor += delta
    return TournamentTask.objects.bulk_create(tasks)
