"""Assigning referees to home games.

Eligibility comes from teams.RefereeProfile -- a per-member level + validity
(unrelated to members.Group), with the *level* (teams.RefereeLevel) owning
which teams it qualifies for. A profile with no level, or one whose validity
has lapsed, is not eligible for anything, and a team explicitly marked as not allowed
for a profile is subtracted from what its level allows -- see
RefereeProfile.eligible_teams, the single definition this module reads through. Availability conflicts (the
member is expected elsewhere at an overlapping time) are surfaced as a
warning only -- never a hard block, since a human may know the two
commitments don't actually clash (enough travel time, one is optional).
Capacity (Event.max_referees) *is* a hard ceiling, enforced here for both
staff-assignment and any future self-service sign-up.

A team can opt out of all of this entirely (Team.referee_management =
FEDERATION): its home games never need club-arranged referees, so they're
excluded from eligibility, assignment, and every referee-facing screen --
see needs_referee_management().

Not every referee is a club member -- add_external_referee logs one by name
only (still counts against Event.max_referees, still capacity-checked), for
e.g. a federation-appointed referee the club still needs to pay.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from club.services.access import event_season
from events.models import ASSUMED_EVENT_DURATION, Event, EventReferee, RefereeSignup
from events.services.attendance import effective_members
from members.models import Member
from notifications.services import notify_members
from teams.models import RefereeLevel, RefereeProfile, StaffAssignment, Team


class RefereeAssignmentError(Exception):
    """A referee could not be assigned to a game."""


def event_window(event):
    """(start, end) for overlap purposes -- events.end may be blank."""
    return event.start, event.end or (event.start + ASSUMED_EVENT_DURATION)


def needs_referee_management(event) -> bool:
    """Whether this event is one the club should be arranging referees for at
    all: a home game or tournament (Event.is_home_fixture) with at least one
    club-managed team. A federation-managed team's home fixtures are entirely
    out of scope for the referee tools -- the federation appoints referees
    for those, not the club."""
    return event.is_home_fixture and event.teams.filter(referee_management=Team.RefereeManagement.CLUB).exists()


def eligible_referees(event):
    """Members who could referee `event`: their RefereeProfile is currently valid and
    allows one of its club-managed teams -- what the level qualifies for (directly, or via
    whatever that level inherits from) minus the teams explicitly marked as not allowed
    for that person -- minus whoever is already assigned. Empty unless
    needs_referee_management.

    Levels resolved to ids first (RefereeLevel.eligible_team_ids can't be
    expressed as a single ORM lookup once inheritance is transitive), and the
    profile check done per profile for the same reason plus the exclusions -- a
    club has a handful of levels and a few dozen referees, so this stays cheap."""
    if not needs_referee_management(event):
        return Member.objects.none()

    team_ids = set(event.teams.filter(referee_management=Team.RefereeManagement.CLUB).values_list("id", flat=True))
    team_ids_by_level = RefereeLevel.eligible_team_ids_by_level(event.club)
    profiles = RefereeProfile.objects.filter(club=event.club, level__isnull=False, valid_until__gte=timezone.localdate()).prefetch_related("excluded_teams")
    member_ids = [profile.member_id for profile in profiles if profile.eligible_team_ids(team_ids_by_level.get(profile.level_id, set())) & team_ids]
    assigned_ids = event.referees.values_list("member_id", flat=True)

    return Member.objects.filter(pk__in=member_ids).exclude(pk__in=assigned_ids).distinct()


def retract_ineligible_invites(profile):
    """After a profile's exclusions (or level) changed: withdraw the *open* invites
    (INVITED, upcoming events) for games the person may no longer referee, so they
    stop seeing them in the app. Accepted sign-ups and real assignments are left alone --
    those are a person's commitments, for the club's managers to sort out. Returns how
    many invites were withdrawn."""
    allowed = profile.eligible_team_ids() if profile.level_id else set()
    withdrawn = 0
    invites = RefereeSignup.objects.filter(member=profile.member, event__club=profile.club, event__start__gt=timezone.now(), status=RefereeSignup.Status.INVITED).select_related("event")
    for invite in invites:
        club_teams = set(invite.event.teams.filter(referee_management=Team.RefereeManagement.CLUB).values_list("id", flat=True))
        if not (allowed & club_teams):
            invite.delete()
            withdrawn += 1
    return withdrawn


def conflicting_events_for_members(members, event):
    """Same overlap-and-audience check as conflicting_events, batched across
    every member in `members` at once: each overlapping candidate's
    effective_members() is resolved once and shared across every member,
    instead of once per (member, candidate) pair -- the shape a whole
    eligible-candidates list needs its conflicts checked in (event detail,
    the referee dashboard), where the old per-member loop meant one query
    per candidate for *each* of dozens of candidates. Returns
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
    """Row-locks `event` and raises RefereeAssignmentError if it's not a
    club-managed home game or is already at Event.max_referees. Shared by
    assign_referee/add_external_referee so two admins acting at the same
    moment can't both squeeze past the ceiling."""
    event = Event.objects.select_for_update().get(pk=event.pk)

    if not needs_referee_management(event):
        raise RefereeAssignmentError(_("Referees can only be assigned to home games for club-managed teams."))

    if event.referees.count() >= event.max_referees:
        raise RefereeAssignmentError(_("This game already has its maximum of %(max)s referee(s).") % {"max": event.max_referees})

    return event


@transaction.atomic
def assign_referee(event, member, *, assigned_by):
    """Assign `member` to referee `event`. Raises RefereeAssignmentError if
    it's not a club-managed home game, the game is already at
    Event.max_referees, or `member` is already assigned."""
    event = _lock_and_check_capacity(event)

    if event.referees.filter(member=member).exists():
        raise RefereeAssignmentError(_("%(member)s is already assigned to this game.") % {"member": member})

    referee = EventReferee.objects.create(event=event, member=member, assigned_by=assigned_by)
    # An assignment settles the member's invite either way -- a staff pick is
    # as good as a yes. Without this a directly assigned referee kept showing
    # as "invited, no response yet", and one never invited had no signup row
    # at all, so the game never showed up among their own duties in the app.
    RefereeSignup.objects.update_or_create(event=event, member=member, defaults={"status": RefereeSignup.Status.ACCEPTED, "responded_at": timezone.now()})
    if assigned_by is not None:
        # A staff pick, made in the management app: the person didn't take this themselves, so
        # tell them. (Self-service accepts pass assigned_by=None -- they just did it.)
        start = timezone.localtime(event.start)

        def body():
            return _("You have been assigned as a referee for %(event)s on %(when)s.") % {"event": event.title, "when": date_format(start, "D d M, H:i")}

        notify_members([member], club=event.club, title=_("Referee assignment"), body=body, source=event)
    return referee


@transaction.atomic
def add_external_referee(event, name, *, assigned_by):
    """Log a non-member referee (e.g. federation-appointed) by name only.
    Same capacity/home-game rules as assign_referee -- an external slot still
    counts against Event.max_referees."""
    name = name.strip()
    if not name:
        raise RefereeAssignmentError(_("A name is required for an external referee."))

    event = _lock_and_check_capacity(event)

    return EventReferee.objects.create(event=event, external_name=name, assigned_by=assigned_by)


@transaction.atomic
def remove_referee(referee):
    """Staff taking a referee off a game (management app). The person's own sign-up is
    set to declined, so the game stops showing as a confirmed duty in their app (and isn't
    re-offered by the automatic invite sync), and they are told. An external referee has
    no account behind them: only the assignment goes."""
    event, member = referee.event, referee.member
    referee.delete()
    if member is None:
        return
    RefereeSignup.objects.filter(event=event, member=member).update(status=RefereeSignup.Status.DECLINED, responded_at=timezone.now())

    def body():
        return _("You have been removed as a referee for %(event)s.") % {"event": event.title}

    notify_members([member], club=event.club, title=_("Referee assignment removed"), body=body, source=event)


def set_referee_fee(referee, *, fee=None, km=None, km_rate=None, no_fee=False):
    """Update one referee assignment's payment details -- what the club owes
    for this game, split into a flat fee and a mileage component. Any
    argument left as None clears that field rather than leaving it
    unchanged, matching how the edit form always submits all three."""
    if no_fee:
        # Unpaid: nothing is owed, so whatever was typed into the (hidden) fee line is dropped.
        fee, km, km_rate = None, None, None
    referee.fee = fee if fee is not None else Decimal("0.00")
    referee.km = km
    referee.km_rate = km_rate
    referee.no_fee = no_fee
    referee.save(update_fields=["fee", "km", "km_rate", "no_fee"])
    return referee


def sync_referee_invites(event, *, notify=True):
    """Invites every currently-eligible, not-yet-invited referee to `event` --
    the referee-facing counterpart to events.sync_event_attendances, wired
    from the same signal points (events/signals.py's post_save(Event) and
    the Event.teams m2m). Idempotent: only creates rows for members with no
    RefereeSignup at all yet for this event, so an unrelated edit (or a
    second team added later) never re-notifies someone who already
    responded -- or re-invites someone who already said no. ``notify=False`` creates the
    rows without the "Referee needed" notification (events.services.crew_invites sends one
    summary per person instead of one per game)."""
    if not needs_referee_management(event):
        return []

    already_invited_ids = set(RefereeSignup.objects.filter(event=event).values_list("member_id", flat=True))
    new_members = [member for member in eligible_referees(event) if member.pk not in already_invited_ids]
    if not new_members:
        return []

    RefereeSignup.objects.bulk_create([RefereeSignup(event=event, member=member) for member in new_members])
    if notify:

        def body():
            return _("%(event)s needs a referee -- can you take it?") % {"event": event.title}

        notify_members(new_members, club=event.club, title=_("Referee needed"), body=body, source=event)
    return new_members


@transaction.atomic
def accept_referee_signup(signup):
    """Confirms `signup`'s member as an actual referee for the game --
    routed through the exact same assign_referee every admin assignment
    uses (assigned_by=None marks it self-service), so capacity is enforced
    once, in one place, and the desktop referee-management screen sees the
    new assignment with no separate sync step. Raises RefereeAssignmentError
    unchanged if the game's already full -- `signup` is left INVITED so the
    referee can retry if a slot frees up."""
    assign_referee(signup.event, signup.member, assigned_by=None)
    signup.status = RefereeSignup.Status.ACCEPTED
    signup.responded_at = timezone.now()
    signup.save(update_fields=["status", "responded_at"])
    return signup


@transaction.atomic
def decline_referee_signup(signup):
    """Declines `signup`. If they were assigned -- by accepting themselves or
    by staff (assign_referee marks the signup ACCEPTED either way) -- the
    EventReferee row goes too, so the assignment and the member's own answer
    never disagree and the desktop screen stops counting someone who can't
    make it. In that case only, the game's own manager(s) are told
    (management position, same audience as lineup.notify_dropout): unlike
    declining an invite nobody was counting on yet, dropping out of an
    assignment leaves a hole someone now has to fill."""
    retracted = EventReferee.objects.filter(event=signup.event, member=signup.member).delete()[0] > 0
    signup.status = RefereeSignup.Status.DECLINED
    signup.responded_at = timezone.now()
    signup.save(update_fields=["status", "responded_at"])

    if retracted:
        manager_ids = StaffAssignment.objects.filter(team__in=signup.event.teams.all(), position__management_position=True, season=event_season(signup.event)).values_list("member_id", flat=True).distinct()
        managers = Member.objects.filter(pk__in=manager_ids)
        if managers:

            def body():
                return _("%(member)s can no longer referee %(event)s after all -- you may need to find a replacement.") % {"member": signup.member.get_full_name(), "event": signup.event.title}

            notify_members(managers, club=signup.event.club, title=_("Referee dropped out"), body=body, source=signup.event)

    return signup
