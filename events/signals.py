"""Signal wiring that keeps attendance in sync with the event audience.

Registered from ``EventsConfig.ready``. Two triggers:

* editing an event (its ``start``, or its ``teams`` / ``invited_members`` /
  ``excluded_members``) re-syncs that event;
* adding or removing a member from a team roster re-syncs that team's future
  events.
"""

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from events.models import Event
from events.services import sync_event_attendances
from teams.models import TeamMembership

M2M_SYNC_ACTIONS = {"post_add", "post_remove", "post_clear"}


@receiver(post_save, sender=Event)
def sync_on_event_save(sender, instance, **kwargs):
    sync_event_attendances(instance)


@receiver(m2m_changed, sender=Event.teams.through)
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
