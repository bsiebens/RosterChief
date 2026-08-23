"""Picking players for a Lineup (plain yes/no, no lines/slots) and publishing
it -- coach mode's C3 screen (mobile/coach_views.py's CoachLineupView).
Mirrors events.services.attendance's shape: small, focused functions the
view writes through rather than touching the models directly.
"""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.services.access import event_season
from events.models import Attendance, LineupSelection
from members.models import Member
from notifications.services import notify_members
from teams.models import StaffAssignment, TeamMembership

#: Attendance statuses that mean "not actually available" -- these members
#: stay exactly as they are on publish, never flipped to NOT_SELECTED, and
#: aren't offered as pickable for the line-up.
UNAVAILABLE_STATUSES = [Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.EXCUSED, Attendance.AttendanceStatus.NO_RESPONSE]


def toggle_selection(lineup, member) -> bool:
    """Flips ``member``'s yes/no pick for ``lineup`` -- deletes their row if
    they were in, creates one if they weren't. Returns the new state (True =
    now selected)."""
    deleted, _details = LineupSelection.objects.filter(lineup=lineup, member=member).delete()
    if deleted:
        return False
    LineupSelection.objects.create(lineup=lineup, member=member)
    return True


def publish_lineup(lineup):
    """Marks the lineup published and writes it into the game record via
    Attendance -- the reason AttendanceStatus.SELECTED/NOT_SELECTED exist,
    previously unused anywhere. Every selected member becomes SELECTED; every
    other member with an Attendance row for this event who was actually
    available (not out/silent, see UNAVAILABLE_STATUSES) becomes
    NOT_SELECTED.

    Also doubles as "publish changes" for an already-published lineup a
    coach keeps editing (CoachLineupView's own "Save line-up" only writes
    LineupSelection -- Attendance.status, and any notification, only ever
    updates here). Notifies by comparing against who was SELECTED *before*
    this run, not by re-notifying everyone currently selected -- a player
    unaffected by the edit shouldn't get a second "you're in" ping, and one
    dropped since the last publish should hear about that specifically.
    Clears scheduled_publish_at regardless of whether this run came from a
    coach tapping Publish directly or from the scheduled sweep (events.tasks.
    publish_scheduled_lineups) catching a due one -- once published, there's
    nothing left pending either way."""
    previously_selected_ids = set(Attendance.objects.filter(event=lineup.event, status=Attendance.AttendanceStatus.SELECTED).values_list("member_id", flat=True))

    lineup.published_at = timezone.now()
    lineup.scheduled_publish_at = None
    lineup.save(update_fields=["published_at", "scheduled_publish_at"])

    selected_member_ids = set(LineupSelection.objects.filter(lineup=lineup).values_list("member_id", flat=True))

    Attendance.objects.filter(event=lineup.event, member_id__in=selected_member_ids).update(status=Attendance.AttendanceStatus.SELECTED)
    Attendance.objects.filter(event=lineup.event).exclude(member_id__in=selected_member_ids).exclude(status__in=UNAVAILABLE_STATUSES).update(status=Attendance.AttendanceStatus.NOT_SELECTED)

    newly_selected = Member.objects.filter(pk__in=selected_member_ids - previously_selected_ids)
    if newly_selected:
        body = _("You're in the line-up for %(event)s.") % {"event": lineup.event.title}
        notify_members(newly_selected, club=lineup.event.club, title=_("Line-up published"), body=body, source=lineup.event)

    newly_dropped = Member.objects.filter(pk__in=previously_selected_ids - selected_member_ids)
    if newly_dropped:
        body = _("The line-up for %(event)s has changed -- you're not in it this time.") % {"event": lineup.event.title}
        notify_members(newly_dropped, club=lineup.event.club, title=_("Line-up updated"), body=body, source=lineup.event)

    return lineup


def schedule_lineup_publish(lineup, when):
    """Sets ``lineup`` to publish itself automatically at ``when`` (an aware
    datetime in the future) instead of waiting for a manual Publish tap --
    events.tasks.publish_scheduled_lineups is the periodic sweep that
    actually calls publish_lineup once that time arrives. Overwrites any
    previous schedule rather than erroring -- picking a new time is the
    whole point of letting a coach come back and adjust it."""
    lineup.scheduled_publish_at = when
    lineup.save(update_fields=["scheduled_publish_at"])
    return lineup


def cancel_scheduled_publish(lineup):
    lineup.scheduled_publish_at = None
    lineup.save(update_fields=["scheduled_publish_at"])
    return lineup


def selected_members_by_position(lineup):
    """Every LineupSelection for ``lineup``, grouped by the member's roster
    position for the lineup's own team/season ("category") -- the read side
    behind the published, member-facing Line-up card (mobile/views.py's
    EventDetailView). A selected member with no TeamMembership on this team/
    season (a guest call-up) lands in a catch-all "No position set" bucket
    rather than being dropped -- mirrors CoachLineupView's own grouping."""
    season = event_season(lineup.event)
    member_ids = list(LineupSelection.objects.filter(lineup=lineup).values_list("member_id", flat=True))
    memberships_by_member = {}
    if season is not None:
        memberships_by_member = {tm.member_id: tm for tm in TeamMembership.objects.filter(team=lineup.team, season=season, member_id__in=member_ids).select_related("position")}
    members_by_id = {member.pk: member for member in Member.objects.filter(pk__in=member_ids)}

    buckets = {}
    for member_id in member_ids:
        member = members_by_id.get(member_id)
        if member is None:
            continue
        position = memberships_by_member[member_id].position if member_id in memberships_by_member else None
        key = position.pk if position else None
        bucket = buckets.setdefault(key, {"label": position.name if position else _("No position set"), "ordering": position.ordering if position else 9999, "members": []})
        bucket["members"].append(member)

    categories = sorted(buckets.values(), key=lambda bucket: (bucket["ordering"], bucket["label"]))
    for bucket in categories:
        bucket["members"].sort(key=lambda member: (member.last_name, member.first_name))
    return categories


def notify_dropout(event, member, note):
    """A player who was SELECTED in a published line-up reporting, after the
    fact, that they can no longer make it (mobile.views.EventDetailView.post's
    "dropout" status) -- unlike an ordinary pre-deadline Out, the roster is
    already locked in, so this goes straight to whoever can still act on it:
    every current-season manager of the event's own teams (management
    position, not every staffer -- a physio can't swap a line-up slot)."""
    manager_ids = StaffAssignment.objects.filter(team__in=event.teams.all(), position__management_position=True, season=event_season(event)).values_list("member_id", flat=True).distinct()
    managers = Member.objects.filter(pk__in=manager_ids)
    if managers:
        body = _("%(member)s can no longer make it to %(event)s: “%(note)s”") % {"member": member.get_full_name(), "event": event.title, "note": note}
        notify_members(managers, club=event.club, title=_("Line-up dropout"), body=body, source=event)
