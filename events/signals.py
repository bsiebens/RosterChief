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

Joining a team/group specifically (not a plain field edit, not a removal)
also sends one summary notification (events.services.attendance.
notify_newly_invited) for however many upcoming events that just put on the
member's calendar -- one notification, not one per event, even if a whole
recurring series is already scheduled.
"""

from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership
from events.models import Attendance, Event, EventSeries
from events.services import notify_newly_invited, sync_event_attendances
from events.services.officials import sync_official_invites
from events.services.referees import sync_referee_invites
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
    sync_referee_invites(instance)
    sync_official_invites(instance)


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


@receiver(m2m_changed, sender=Event.teams.through)
def sync_referee_invites_on_teams_change(sender, instance, action, reverse, **kwargs):
    # Referee eligibility only depends on `teams` (needs_referee_management),
    # not groups/invited/excluded_members -- a separate, narrower receiver
    # rather than folding into sync_on_audience_change above, which is about
    # attendance specifically. reverse (team.scheduled_events.add(...)) isn't
    # a used path -- see validate_teams_same_club's own comment.
    if action not in M2M_SYNC_ACTIONS or reverse or not isinstance(instance, Event):
        return
    sync_referee_invites(instance)
    sync_official_invites(instance)


@receiver(post_save, sender=TeamMembership)
@receiver(post_delete, sender=TeamMembership)
def sync_on_roster_change(sender, instance, **kwargs):
    now = timezone.now()
    events = list(Event.objects.filter(teams=instance.team_id, start__gte=now).distinct())

    # Only a fresh addition to the roster has anything new to tell the member
    # about -- a plain field edit (jersey number, position) re-saves the same
    # row (kwargs["created"] is False), and a removal has nothing new to add.
    # post_delete carries no "created" key at all, hence the default.
    newly_invited = []
    if kwargs.get("created", False):
        already_invited_ids = set(Attendance.objects.filter(member=instance.member, event__in=events).values_list("event_id", flat=True))
        newly_invited = [event for event in events if event.pk not in already_invited_ids]

    for event in events:
        sync_event_attendances(event)

    if newly_invited:
        notify_newly_invited(instance.member, club=instance.team.club, events=newly_invited)


@receiver(post_save, sender=GroupMembership)
@receiver(post_delete, sender=GroupMembership)
def sync_on_group_membership_change(sender, instance, **kwargs):
    now = timezone.now()
    events = list(Event.objects.filter(groups=instance.group_id, start__gte=now).distinct())

    newly_invited = []
    if kwargs.get("created", False):
        already_invited_ids = set(Attendance.objects.filter(member=instance.member, event__in=events).values_list("event_id", flat=True))
        newly_invited = [event for event in events if event.pk not in already_invited_ids]

    for event in events:
        sync_event_attendances(event)

    if newly_invited:
        notify_newly_invited(instance.member, club=instance.group.club, events=newly_invited)


@receiver(post_save, sender=ClubMembership)
@receiver(post_delete, sender=ClubMembership)
def sync_on_club_membership_change(sender, instance, **kwargs):
    now = timezone.now()
    for event in Event.objects.filter(club_wide=True, club_id=instance.club_id, start__gte=now).distinct():
        sync_event_attendances(event)
