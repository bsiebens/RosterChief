"""Keep attendance in sync with the event audience. Registered from ``EventsConfig.ready``.

Triggers (only upcoming events are ever touched, history is never rewritten):

* saving an event, or changing its ``teams`` / ``groups`` / ``invited_members`` / ``excluded_members`` (from either side
  of the relation), re-syncs that event;
* adding or removing a roster spot (``teams.TeamMembership``) re-syncs that member on the team's upcoming events;
* adding or removing a group member re-syncs that member on the group's upcoming events;
* a club membership being created, deleted or changing status re-syncs the upcoming club-wide events (in the Celery
  task ``events.sync_club_wide_events``, queued after the commit);
* saving a club-wide event or changing its invited/excluded members queues ``events.sync_event_attendances`` instead of
  syncing inline (its audience is the whole club);
* deleting a team or group re-syncs the upcoming events it was on.

A fresh roster spot or group membership also sends ONE inbox notification (with push) summarising the upcoming events
that just landed on the member's calendar. Membership rows removed because their member is being deleted are skipped:
that member's attendance rows go with them.

Roster, group and other m2m syncs stay inline (they touch one member, or one team's events). Every receiver works through ``events.services.attendance.sync_attendances`` (bulk, a fixed number of queries); none of
them saves an Event or a membership row, so they cannot trigger each other.
"""

from django.db import transaction
from django.db.models import QuerySet
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone

from events.models import Event
from events.services import (
    attachments,  # noqa: F401  (connects the attachment file-cleanup receiver)
    attendance,
)
from members.models import ClubMembership, Group, GroupMembership, Member
from teams.models import Team, TeamMembership

M2M_SYNC_ACTIONS = {"post_add", "post_remove", "post_clear"}
EVENT_AUDIENCE_FIELDS = {"start", "season", "club_wide"}


def _origin_is(origin, *models) -> bool:
    """Whether a delete was started by deleting an instance (or queryset) of one of ``models`` (a cascade)."""
    if isinstance(origin, QuerySet):
        return issubclass(origin.model, models)
    return isinstance(origin, models)


def _enqueue(task_name: str, *args) -> None:
    """Queue an events task after the commit (it then sees the saved state; a rollback queues nothing). The tenant-aware
    Celery app runs it in the club schema it was queued from."""
    from events import tasks

    task = getattr(tasks, task_name)
    transaction.on_commit(lambda: task.delay(*args))


def _sync_event(event) -> None:
    """Sync one event: inline for team/group events (small), in a task for a club-wide one (the whole club)."""
    if event.club_wide:
        if event.start >= timezone.now():
            _enqueue("sync_event_attendances", str(event.pk))
    else:
        attendance.sync_event_attendances(event)


def _skip_save(kwargs, relevant: set) -> bool:
    if kwargs.get("raw"):
        return True
    update_fields = kwargs.get("update_fields")
    return update_fields is not None and not (set(update_fields) & relevant)


# --- events --------------------------------------------------------------------------------------------------------


@receiver(post_save, sender=Event, dispatch_uid="events.attendance.event_saved")
def sync_on_event_save(sender, instance, **kwargs):
    if _skip_save(kwargs, EVENT_AUDIENCE_FIELDS):
        return
    _sync_event(instance)  # no-op for past events


@receiver(m2m_changed, sender=Event.teams.through, dispatch_uid="events.attendance.teams_changed")
@receiver(m2m_changed, sender=Event.groups.through, dispatch_uid="events.attendance.groups_changed")
@receiver(m2m_changed, sender=Event.invited_members.through, dispatch_uid="events.attendance.invited_changed")
@receiver(m2m_changed, sender=Event.excluded_members.through, dispatch_uid="events.attendance.excluded_changed")
def sync_on_audience_change(sender, instance, action, reverse, model, pk_set, **kwargs):
    if not reverse:
        if action in M2M_SYNC_ACTIONS:
            _sync_event(instance)
        return

    # Reverse side edited, e.g. team.scheduled_events.add(event) or member.invited_to_events.clear().
    # post_clear carries no pk_set, so remember the events on pre_clear.
    stash = f"_attendance_clear_{sender._meta.db_table}"
    if action == "pre_clear":
        through_field = sender._meta.get_field(instance._meta.model_name).attname
        setattr(instance, stash, set(sender.objects.filter(**{through_field: instance.pk}).values_list("event_id", flat=True)))
        return
    if action not in M2M_SYNC_ACTIONS:
        return
    event_ids = getattr(instance, stash, set()) if action == "post_clear" else (pk_set or set())
    if not event_ids:
        return
    events = attendance.future_events(pk__in=event_ids)
    if isinstance(instance, Member):
        attendance.sync_attendances(events, member_ids=[instance.pk])
    else:
        attendance.sync_attendances(events)


# --- rosters, groups, club memberships ----------------------------------------------------------------------------


def _sync_member(member, events, *, created: bool, via: str):
    added = attendance.sync_attendances(events, member_ids=[member.pk])
    if created and added:
        added_ids = {event_id for event_id, _member_id in added}
        attendance.notify_newly_invited(member, sorted((e for e in events if e.pk in added_ids), key=lambda e: e.start), via=via)


@receiver(post_save, sender=TeamMembership, dispatch_uid="events.attendance.roster_saved")
def sync_on_roster_save(sender, instance, created, **kwargs):
    if _skip_save(kwargs, {"team", "team_id", "season", "season_id", "member", "member_id"}):
        return
    _sync_member(instance.member, attendance.future_events(teams=instance.team_id), created=created, via=instance.team.name)


@receiver(post_delete, sender=TeamMembership, dispatch_uid="events.attendance.roster_deleted")
def sync_on_roster_delete(sender, instance, origin=None, **kwargs):
    if _origin_is(origin, Member, Team):  # the member's rows cascade / the team's own receiver re-syncs
        return
    attendance.sync_attendances(attendance.future_events(teams=instance.team_id), member_ids=[instance.member_id])


@receiver(post_save, sender=GroupMembership, dispatch_uid="events.attendance.group_member_saved")
def sync_on_group_membership_save(sender, instance, created, **kwargs):
    if _skip_save(kwargs, {"group", "group_id", "member", "member_id"}):
        return
    _sync_member(instance.member, attendance.future_events(groups=instance.group_id), created=created, via=instance.group.name)


@receiver(post_delete, sender=GroupMembership, dispatch_uid="events.attendance.group_member_deleted")
def sync_on_group_membership_delete(sender, instance, origin=None, **kwargs):
    if _origin_is(origin, Member, Group):
        return
    attendance.sync_attendances(attendance.future_events(groups=instance.group_id), member_ids=[instance.member_id])


@receiver(post_save, sender=ClubMembership, dispatch_uid="events.attendance.club_membership_saved")
@receiver(post_delete, sender=ClubMembership, dispatch_uid="events.attendance.club_membership_deleted")
def sync_on_club_membership_change(sender, instance, origin=None, **kwargs):
    # Fee bookkeeping saves with update_fields that leave status alone: nothing changes for the audience then.
    # Club-wide events can be many and large, so they are re-synced in a task, after the commit.
    if _skip_save(kwargs, {"status", "season", "season_id", "member", "member_id"}) or _origin_is(origin, Member):
        return
    _enqueue("sync_club_wide_events")


# --- a team or group deleted ---------------------------------------------------------------------------------------
# Its m2m rows on events cascade without m2m_changed: remember the upcoming events first, re-sync them afterwards.


@receiver(pre_delete, sender=Team, dispatch_uid="events.attendance.team_pre_delete")
@receiver(pre_delete, sender=Group, dispatch_uid="events.attendance.group_pre_delete")
def remember_events_before_delete(sender, instance, **kwargs):
    lookup = "teams" if sender is Team else "groups"
    instance._attendance_event_ids = [e.pk for e in attendance.future_events(**{lookup: instance.pk})]


@receiver(post_delete, sender=Team, dispatch_uid="events.attendance.team_deleted")
@receiver(post_delete, sender=Group, dispatch_uid="events.attendance.group_deleted")
def sync_after_delete(sender, instance, **kwargs):
    event_ids = getattr(instance, "_attendance_event_ids", None)
    if event_ids:
        attendance.sync_attendances(attendance.future_events(pk__in=event_ids))
