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
from django.utils.translation import gettext_lazy as _
from waffle import get_waffle_flag_model

from events.models import Event, EventOfficial, OfficialSignup
from events.services.attendance import effective_members
from events.services.referees import event_window
from members.models import Member
from notifications.services import notify_members
from teams.models import OfficialLevel, Team

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
    at all: a home game with at least one club-managed (for officials) team.
    A federation-managed team's home games are entirely out of scope."""
    return event.is_home_game and event.teams.filter(official_management=Team.OfficialManagement.CLUB).exists()


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

    return (
        Member.objects.filter(official_profile__level_id__in=qualifying_level_ids, official_profile__valid_until__gte=today)
        .exclude(pk__in=assigned_ids)
        .distinct()
    )


def conflicting_events(member, event):
    """Other events in this club overlapping `event`'s time window where
    `member` is part of the expected audience -- informational only, never
    blocks an assignment. Deliberately not imported from events.services.
    referees (whose body is identical): each service module stays readable
    without a cross-import for what's really just shared scheduling logic,
    not a referee-specific rule."""
    start, end = event_window(event)
    candidates = Event.objects.filter(club=event.club).exclude(pk=event.pk).filter(start__lt=end)

    conflicts = []
    for candidate in candidates:
        _candidate_start, candidate_end = event_window(candidate)
        if candidate_end > start and effective_members(candidate).filter(pk=member.pk).exists():
            conflicts.append(candidate)
    return conflicts


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

    return EventOfficial.objects.create(event=event, member=member, assigned_by=assigned_by)


@transaction.atomic
def add_external_official(event, name, *, assigned_by):
    """Log a non-member official (e.g. federation-appointed) by name only.
    Same capacity/home-game rules as assign_official."""
    name = name.strip()
    if not name:
        raise OfficialAssignmentError(_("A name is required for an external official."))

    event = _lock_and_check_capacity(event)

    return EventOfficial.objects.create(event=event, external_name=name, assigned_by=assigned_by)


def remove_official(official):
    official.delete()


def set_official_fee(official, *, fee=None, km=None, km_rate=None):
    """Update one official assignment's payment details. Any argument left
    as None clears that field rather than leaving it unchanged, matching how
    the edit form always submits all three."""
    official.fee = fee if fee is not None else Decimal("0.00")
    official.km = km
    official.km_rate = km_rate
    official.save(update_fields=["fee", "km", "km_rate"])
    return official


def sync_official_invites(event):
    """Invites every currently-eligible, not-yet-invited official to `event`
    -- wired from the same signal points as sync_referee_invites
    (events/signals.py's post_save(Event) and the Event.teams m2m).
    Idempotent, same reasoning as sync_referee_invites.

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
    body = _("%(event)s needs a match official -- can you take it?") % {"event": event.title}
    notify_members(new_members, club=event.club, title=_("Official needed"), body=body, source=event)
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
    """Declines `signup`. If they'd already accepted, also removes their
    self-service EventOfficial row (assigned_by is None -- never touches an
    admin-made assignment)."""
    EventOfficial.objects.filter(event=signup.event, member=signup.member, assigned_by__isnull=True).delete()
    signup.status = OfficialSignup.Status.DECLINED
    signup.responded_at = timezone.now()
    signup.save(update_fields=["status", "responded_at"])
    return signup
