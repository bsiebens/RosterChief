"""Roster changes keep team chats in step (see ``chat.services.roster``): a change to a team's players or staff re-syncs the chats with a
rule for that team once the transaction commits. Family changes (parents) are picked up by the nightly sync."""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from teams.models import StaffAssignment, TeamMembership

log = logging.getLogger(__name__)


def _resync(team_id) -> None:
    from chat.models import AudienceRule
    from chat.services import roster

    if not AudienceRule.objects.filter(team_id=team_id).exists():  # cheap for the overwhelming majority of roster changes
        return
    try:
        roster.sync_team(AudienceRule.objects.filter(team_id=team_id).first().team)
    except Exception:
        log.exception("chat.roster_sync_failed")


@receiver([post_save, post_delete], sender=TeamMembership)
@receiver([post_save, post_delete], sender=StaffAssignment)
def roster_changed(sender, instance, **kwargs):
    team_id = instance.team_id
    transaction.on_commit(lambda: _resync(team_id))
