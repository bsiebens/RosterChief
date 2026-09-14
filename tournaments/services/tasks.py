"""Claiming a slot on a tournament volunteer shift -- a straight mirror of
``events.services.tasks.claim_task``, capacity-checked the same
select-for-update way (see that module's own docstring: two people
confirming at the same moment can't both squeeze past the last slot).
"""

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
