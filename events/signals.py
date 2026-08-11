"""Signal wiring that keeps attendance in sync with the event audience.

Registered from ``EventsConfig.ready``. Triggers:

* editing an event (its ``start``, or its ``teams`` / ``groups`` /
  ``invited_members`` / ``excluded_members``) re-syncs that event;
* adding or removing a member from a team roster re-syncs that team's future
  events;
* adding or removing a member from a group re-syncs that group's future
  events;
* a club membership going active/inactive re-syncs every future club_wide
  event for that club.
"""

from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership
from events.models import Event, EventSeries
from events.services import sync_event_attendances
from members.models import Group, GroupMembership
from teams.models import Team, TeamMembership

M2M_SYNC_ACTIONS = {"post_add", "post_remove", "post_clear"}


@receiver(m2m_changed, sender=Event.teams.through)
@receiver(m2m_changed, sender=EventSeries.teams.through)
def validate_teams_same_club(sender, instance, action, reverse, pk_set, **kwargs):
    # Reject teams from another club before they're attached (forward adds only;
    # the reverse direction — team.scheduled_events.add(...) — is not a used path).
    if action != "pre_add" or reverse:
        return
    if Team.objects.filter(pk__in=pk_set).exclude(club_id=instance.club_id).exists():
        raise ValidationError(_("Teams must belong to the same club as the event."))


@receiver(m2m_changed, sender=Event.groups.through)
@receiver(m2m_changed, sender=EventSeries.groups.through)
def validate_groups_same_club(sender, instance, action, reverse, pk_set, **kwargs):
    if action != "pre_add" or reverse:
        return
    if Group.objects.filter(pk__in=pk_set).exclude(club_id=instance.club_id).exists():
        raise ValidationError(_("Groups must belong to the same club as the event."))


@receiver(post_save, sender=Event)
def sync_on_event_save(sender, instance, **kwargs):
    sync_event_attendances(instance)


@receiver(m2m_changed, sender=Event.teams.through)
@receiver(m2m_changed, sender=Event.groups.through)
@receiver(m2m_changed, sender=Event.invited_members.through)
@receiver(m2m_changed, sender=Event.excluded_members.through)
def sync_on_audience_change(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action not in M2M_SYNC_ACTIONS:
        return

    if isinstance(instance, Event):
        sync_event_attendances(instance)
    else:
        # Reverse relation edited (e.g. member.invited_to_events.add(event)).
        for event in Event.objects.filter(pk__in=pk_set or []):
            sync_event_attendances(event)


@receiver(post_save, sender=TeamMembership)
@receiver(post_delete, sender=TeamMembership)
def sync_on_roster_change(sender, instance, **kwargs):
    now = timezone.now()
    for event in Event.objects.filter(teams=instance.team_id, start__gte=now).distinct():
        sync_event_attendances(event)


@receiver(post_save, sender=GroupMembership)
@receiver(post_delete, sender=GroupMembership)
def sync_on_group_membership_change(sender, instance, **kwargs):
    now = timezone.now()
    for event in Event.objects.filter(groups=instance.group_id, start__gte=now).distinct():
        sync_event_attendances(event)


@receiver(post_save, sender=ClubMembership)
@receiver(post_delete, sender=ClubMembership)
def sync_on_club_membership_change(sender, instance, **kwargs):
    now = timezone.now()
    for event in Event.objects.filter(club_wide=True, club_id=instance.club_id, start__gte=now).distinct():
        sync_event_attendances(event)
