"""Assigning match officials to home games -- the officials counterpart to
events.services.referees, mirrored function-for-function (see that module's
own docstring for the reasoning behind every rule below, all unchanged here
except reading Team.official_management/teams.OfficialLevel/OfficialProfile
instead of the referee equivalents). Kept as a fully separate module/table
set rather than a "kind" flag threaded through the referee one, so the
"officials" waffle flag can gate this entire surface with nothing to filter
by kind anywhere else.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from waffle import get_waffle_flag_model

from club.services.access import event_season
from events.models import Event, EventOfficial, OfficialSignup
from events.services.attendance import effective_members
from events.services.referees import event_window
from members.models import Member
from notifications.models import NotificationCategory
from notifications.services import notify_members
from teams.models import OfficialLevel, StaffAssignment, Team

#: Name of the waffle Flag gating the whole officials surface. Views/nav go
#: through the request-scoped flag_is_active(request, OFFICIALS_FLAG) the
#: same way shop/formbuilder already do (see management.context_processors.
#: feature_sections); this module's own sync_official_invites is the one
#: code path that runs with no request at all (event save/team-change
#: signals), so it checks the flag directly against the event's club.
OFFICIALS_FLAG = "officials"


class OfficialAssignmentError(Exception):
    """A match official could not be assigned to a game."""


def officials_enabled_for(club) -> bool:
    """Whether the "officials" flag is on for `club` -- for code that only
    has a club, no request (sync_official_invites below)."""
    flag = get_waffle_flag_model().objects.filter(name=OFFICIALS_FLAG).first()
    return flag is not None and flag.is_active_for_club(club)


def needs_official_management(event) -> bool:
    """Whether this event is one the club should be arranging officials for
    at all: a home game or tournament (Event.is_home_fixture) with at least
    one club-managed (for officials) team. A federation-managed team's home
    fixtures are entirely out of scope."""
    return event.is_home_fixture and event.teams.filter(official_management=Team.OfficialManagement.CLUB).exists()


def eligible_officials(event):
    """Members who could act as an official for `event`: their
    OfficialProfile has a level qualifying for one of its club-managed
    teams (directly, or via whatever that level inherits from), and is
    currently valid, minus whoever is already assigned. Empty unless
    needs_official_management."""
    if not needs_official_management(event):
        return Member.objects.none()

    team_ids = set(event.teams.filter(official_management=Team.OfficialManagement.CLUB).values_list("id", flat=True))
    qualifying_level_ids = [level.pk for level in OfficialLevel.objects.filter(club=event.club) if level.eligible_team_ids() & team_ids]
    assigned_ids = event.officials.values_list("member_id", flat=True)
    today = timezone.localdate()

    return Member.objects.filter(official_profiles__club=event.club, official_profiles__level_id__in=qualifying_level_ids, official_profiles__valid_until__gte=today).exclude(pk__in=assigned_ids).distinct()


def conflicting_events_for_members(members, event):
    """Same overlap-and-audience check as conflicting_events, batched across
    every member in `members` at once: each overlapping candidate's
    effective_members() is resolved once and shared across every member,
    instead of once per (member, candidate) pair. Deliberately not imported
    from events.services.referees (whose body is identical): each service
    module stays readable without a cross-import for what's really just
    shared scheduling logic, not a referee-specific rule. Returns
    ``{member.pk: [conflicting events]}``."""
    start, end = event_window(event)
    overlapping = [candidate for candidate in Event.objects.filter(club=event.club).exclude(pk=event.pk).filter(start__lt=end) if event_window(candidate)[1] > start]
    member_ids_by_candidate = {candidate.pk: set(effective_members(candidate).values_list("id", flat=True)) for candidate in overlapping}
    return {member.pk: [candidate for candidate in overlapping if member.pk in member_ids_by_candidate[candidate.pk]] for member in members}


def conflicting_events(member, event):
    """Other events in this club overlapping `event`'s time window where
    `member` is part of the expected audience -- informational only, never
    blocks an assignment. Single-member convenience wrapper around
    conflicting_events_for_members -- checking a whole candidate list should
    call that directly instead of this in a loop."""
    return conflicting_events_for_members([member], event)[member.pk]


def _lock_and_check_capacity(event):
    """Row-locks `event` and raises OfficialAssignmentError if it's not a
    club-managed home game or is already at Event.max_officials."""
    event = Event.objects.select_for_update().get(pk=event.pk)

    if not needs_official_management(event):
        raise OfficialAssignmentError(_("Officials can only be assigned to home games for club-managed teams."))

    if event.officials.count() >= event.max_officials:
        raise OfficialAssignmentError(_("This game already has its maximum of %(max)s official(s).") % {"max": event.max_officials})

    return event


@transaction.atomic
def assign_official(event, member, *, assigned_by):
    """Assign `member` as an official for `event`. Raises
    OfficialAssignmentError if it's not a club-managed home game, the game
    is already at Event.max_officials, or `member` is already assigned."""
    event = _lock_and_check_capacity(event)

    if event.officials.filter(member=member).exists():
        raise OfficialAssignmentError(_("%(member)s is already assigned to this game.") % {"member": member})

    official = EventOfficial.objects.create(event=event, member=member, assigned_by=assigned_by)
    # Same as referees.assign_referee: an assignment settles the member's
    # invite, so they don't show as "no response yet" and the game appears
    # among their own duties in the app.
    OfficialSignup.objects.update_or_create(event=event, member=member, defaults={"status": OfficialSignup.Status.ACCEPTED, "responded_at": timezone.now()})
    if assigned_by is not None:
        # A staff pick from the management app -- tell them. Self-service accepts pass
        # assigned_by=None: they just did it themselves.
        start = timezone.localtime(event.start)

        def body():
            return _("You have been assigned as an official for %(event)s on %(when)s.") % {"event": event.title, "when": date_format(start, "D d M, H:i")}

        notify_members([member], club=event.club, title=_("Official assignment"), body=body, source=event, category=NotificationCategory.CREW)
    return official


@transaction.atomic
def add_external_official(event, name, *, assigned_by):
    """Log a non-member official (e.g. federation-appointed) by name only.
    Same capacity/home-game rules as assign_official."""
    name = name.strip()
    if not name:
        raise OfficialAssignmentError(_("A name is required for an external official."))

    event = _lock_and_check_capacity(event)

    return EventOfficial.objects.create(event=event, external_name=name, assigned_by=assigned_by)


@transaction.atomic
def remove_official(official):
    """Staff taking an official off a game (management app) -- the officials counterpart
    to referees.remove_referee: their sign-up is set to declined and they are told."""
    event, member = official.event, official.member
    official.delete()
    if member is None:
        return
    OfficialSignup.objects.filter(event=event, member=member).update(status=OfficialSignup.Status.DECLINED, responded_at=timezone.now())

    def body():
        return _("You have been removed as an official for %(event)s.") % {"event": event.title}

    notify_members([member], club=event.club, title=_("Official assignment removed"), body=body, source=event, category=NotificationCategory.CREW)


def set_official_fee(official, *, fee=None, km=None, km_rate=None, no_fee=False):
    """Update one official assignment's payment details. Any argument left
    as None clears that field rather than leaving it unchanged, matching how
    the edit form always submits all three."""
    if no_fee:
        # Unpaid: nothing is owed, so whatever was typed into the (hidden) fee line is dropped.
        fee, km, km_rate = None, None, None
    official.fee = fee if fee is not None else Decimal("0.00")
    official.km = km
    official.km_rate = km_rate
    official.no_fee = no_fee
    official.save(update_fields=["fee", "km", "km_rate", "no_fee"])
    return official


def sync_official_invites(event, *, notify=True):
    """Invites every currently-eligible, not-yet-invited official to `event`
    -- wired from the same signal points as sync_referee_invites
    (events/signals.py's post_save(Event) and the Event.teams m2m).
    Idempotent, same reasoning as sync_referee_invites (``notify=False`` likewise).

    Unlike every other function here, this one runs unconditionally on every
    event save/team change regardless of whether any officials view was ever
    visited -- so it's the one place that has to check officials_enabled_for
    itself, rather than relying on a view/nav layer that a background signal
    never goes through."""
    if not officials_enabled_for(event.club) or not needs_official_management(event):
        return []

    already_invited_ids = set(OfficialSignup.objects.filter(event=event).values_list("member_id", flat=True))
    new_members = [member for member in eligible_officials(event) if member.pk not in already_invited_ids]
    if not new_members:
        return []

    OfficialSignup.objects.bulk_create([OfficialSignup(event=event, member=member) for member in new_members])
    if notify:

        def body():
            return _("%(event)s needs a match official -- can you take it?") % {"event": event.title}

        notify_members(new_members, club=event.club, title=_("Official needed"), body=body, source=event, category=NotificationCategory.CREW)
    return new_members


@transaction.atomic
def accept_official_signup(signup):
    """Confirms `signup`'s member as an actual official for the game --
    routed through the same assign_official every admin assignment uses
    (assigned_by=None marks it self-service)."""
    assign_official(signup.event, signup.member, assigned_by=None)
    signup.status = OfficialSignup.Status.ACCEPTED
    signup.responded_at = timezone.now()
    signup.save(update_fields=["status", "responded_at"])
    return signup


@transaction.atomic
def decline_official_signup(signup):
    """Declines `signup`. If they were assigned -- by accepting themselves or
    by staff (assign_official marks the signup ACCEPTED either way) -- the
    EventOfficial row goes too, so the assignment and the member's own
    answer never disagree -- and, in that case only, tells the game's own
    manager(s), same reasoning as decline_referee_signup's own retraction
    notice (deliberately not shared code -- see this module's own
    docstring)."""
    retracted = EventOfficial.objects.filter(event=signup.event, member=signup.member).delete()[0] > 0
    signup.status = OfficialSignup.Status.DECLINED
    signup.responded_at = timezone.now()
    signup.save(update_fields=["status", "responded_at"])

    if retracted:
        manager_ids = StaffAssignment.objects.filter(team__in=signup.event.teams.all(), position__management_position=True, season=event_season(signup.event)).values_list("member_id", flat=True).distinct()
        managers = Member.objects.filter(pk__in=manager_ids)
        if managers:

            def body():
                return _("%(member)s can no longer act as an official for %(event)s after all -- you may need to find a replacement.") % {"member": signup.member.get_full_name(), "event": signup.event.title}

            notify_members(managers, club=signup.event.club, title=_("Official dropped out"), body=body, source=signup.event, category=NotificationCategory.CREW)

    return signup
