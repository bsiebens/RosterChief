"""Claiming a slot on an event task ("bring fruit for half-time").

Capacity (EventTask.needed_quantity) is a hard ceiling, enforced here the
same row-locked way events.services.referees/officials enforce
Event.max_referees/max_officials -- two people confirming at the same
moment can't both squeeze past the last slot.
"""

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from events.models import EventTask, EventTaskClaim


class TaskClaimError(Exception):
    """A task slot could not be claimed."""


@transaction.atomic
def claim_task(task, member):
    """Claim one slot of `task` for `member`. Raises TaskClaimError if every
    slot is already taken, or `member` has already claimed one."""
    task = EventTask.objects.select_for_update().get(pk=task.pk)

    if task.claims.filter(member=member).exists():
        raise TaskClaimError(_("%(member)s has already claimed this.") % {"member": member})

    if task.claims.count() >= task.needed_quantity:
        raise TaskClaimError(_("This is already fully covered."))

    return EventTaskClaim.objects.create(task=task, member=member)


def unclaim_task(claim):
    claim.delete()
