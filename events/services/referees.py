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
from django.db.models import BooleanField, Case, When
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from club.models import RefereeType
from club.services.access import event_season
from events.models import ASSUMED_EVENT_DURATION, Event, EventReferee, RefereeSignup
from events.services.attendance import effective_members
from members.models import Member
from notifications.models import NotificationCategory
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
    return event.is_home_fixture and bool(_club_managed_team_ids(event))


def _club_managed_team_ids(event) -> set:
    # In Python over .all() (not a .filter()) so a prefetched ``teams`` -- the bulk resync
    # in events.services.crew_invites -- costs no query per game.
    return {team.pk for team in event.teams.all() if team.referee_management == Team.RefereeManagement.CLUB}


def referee_roster(club):
    """Everyone with a referee level and a validity date, resolved once: ``[(member_id,
    expired, team_ids)]``, where team_ids is what they'd qualify for were the validity still
    running. Passed to eligible_referees to evaluate many games without redoing this per game."""
    team_ids_by_level = RefereeLevel.eligible_team_ids_by_level(club)
    today = timezone.localdate()
    profiles = RefereeProfile.objects.filter(club=club, level__isnull=False, valid_until__isnull=False).prefetch_related("excluded_teams")
    return [(profile.member_id, profile.valid_until < today, profile.eligible_team_ids(team_ids_by_level.get(profile.level_id, set()), ignore_validity=True)) for profile in profiles]


def eligible_referees(event, *, include_expired=False, roster=None):
    """Members who could referee `event`: their RefereeProfile is currently valid and
    allows one of its club-managed teams -- what the level qualifies for (directly, or via
    whatever that level inherits from) minus the teams explicitly marked as not allowed
    for that person -- minus whoever is already assigned. Empty unless
    needs_referee_management.

    ``include_expired`` also returns people whose validity has lapsed but who would
    otherwise qualify, flagged ``referee_expired`` -- for the staff assign dropdowns, where
    a manager may still pick them; invites (sync_referee_invites) never include them.
    ``roster`` is a precomputed referee_roster(event.club) for bulk callers.

    Levels resolved to ids first (RefereeLevel.eligible_team_ids can't be
    expressed as a single ORM lookup once inheritance is transitive), and the
    profile check done per profile for the same reason plus the exclusions."""
    if not needs_referee_management(event):
        return Member.objects.none()

    team_ids = _club_managed_team_ids(event)
    if roster is None:
        roster = referee_roster(event.club)
    qualifying = [(member_id, expired) for member_id, expired, allowed in roster if allowed & team_ids and (include_expired or not expired)]
    expired_ids = [member_id for member_id, expired in qualifying if expired]
    assigned_ids = event.referees.values_list("member_id", flat=True)

    return Member.objects.filter(pk__in=[member_id for member_id, _expired in qualifying]).exclude(pk__in=assigned_ids).annotate(referee_expired=Case(When(pk__in=expired_ids, then=True), default=False, output_field=BooleanField())).distinct()


def retract_ineligible_invites(profile):
    """After a profile's exclusions (or level) changed: withdraw the *open* invites
    (INVITED, upcoming events) for games the person may no longer referee, so they
    stop seeing them in the app. Accepted sign-ups and real assignments are left alone --
    those are a person's commitments, for the club's managers to sort out. Returns how
    many invites were withdrawn."""
    allowed = profile.eligible_team_ids() if profile.level_id else set()
    withdrawn = 0
    invites = RefereeSignup.objects.filter(member=profile.member, event__club=profile.club, event__start__gt=timezone.now(), status=RefereeSignup.Status.INVITED).select_related("event").prefetch_related("event__teams")
    for invite in invites:
        if not (allowed & _club_managed_team_ids(invite.event)):
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


def referee_types_for(club, kind=RefereeType.Kind.REFEREE):
    """The club's sport's referee types of one kind (REFEREE / OFFICIAL), in form order."""
    return RefereeType.objects.filter(sport__code=club.sport_type, kind=kind).order_by("ordering", "name")


def resolve_referee_type(club, referee_type, kind, error_class):
    """Validate an optional type for an assignment: None passes, anything else must be a RefereeType
    of the club's sport and the right kind, otherwise ``error_class`` is raised."""
    if referee_type is None:
        return None
    if not referee_types_for(club, kind).filter(pk=referee_type.pk).exists():
        raise error_class(_("This type isn't available for your club's sport."))
    return referee_type


def order_assignments(rows):
    """Referee / official rows in display order, used by every screen and the PDFs: by type ordering
    (rows without a type last), then alphabetically by display name -- members and externals interleaved.
    Done in Python so it behaves the same on SQLite and PostgreSQL. Callers should select_related
    ``member`` and ``referee_type`` to avoid per-row queries."""

    def key(row):
        referee_type = row.referee_type
        return (referee_type is None, referee_type.ordering if referee_type else 0, referee_type.name.casefold() if referee_type else "", row.display_name.casefold(), str(row.pk))

    return sorted(rows, key=key)


@transaction.atomic
def assign_referee(event, member, *, assigned_by, referee_type=None):
    """Assign `member` to referee `event`. Raises RefereeAssignmentError if
    it's not a club-managed home game, the game is already at
    Event.max_referees, or `member` is already assigned."""
    referee_type = resolve_referee_type(event.club, referee_type, RefereeType.Kind.REFEREE, RefereeAssignmentError)
    event = _lock_and_check_capacity(event)

    if event.referees.filter(member=member).exists():
        raise RefereeAssignmentError(_("%(member)s is already assigned to this game.") % {"member": member})

    referee = EventReferee.objects.create(event=event, member=member, assigned_by=assigned_by, referee_type=referee_type)
    # An assignment settles the member's invite either way -- a staff pick is
    # as good as a yes. Without this a directly assigned referee kept showing
    # as "invited, no response yet", and one never invited had no signup row
    # at all, so the game never showed up among their own duties in the app.
    RefereeSignup.objects.update_or_create(event=event, member=member, defaults={"status": RefereeSignup.Status.CONFIRMED, "responded_at": timezone.now()})
    if assigned_by is not None:
        # A staff pick, made in the management app: the person didn't take this themselves, so
        # tell them. (Self-service accepts pass assigned_by=None -- they just did it.)
        start = timezone.localtime(event.start)

        def body():
            return _("You have been assigned as a referee for %(event)s on %(when)s.") % {"event": event.title, "when": date_format(start, "D d M, H:i")}

        notify_members([member], club=event.club, title=_("Referee assignment"), body=body, source=event, category=NotificationCategory.CREW)
    return referee


@transaction.atomic
def add_external_referee(event, name, *, assigned_by, referee_type=None):
    """Log a non-member referee (e.g. federation-appointed) by name only.
    Same capacity/home-game rules as assign_referee -- an external slot still
    counts against Event.max_referees."""
    name = name.strip()
    if not name:
        raise RefereeAssignmentError(_("A name is required for an external referee."))

    referee_type = resolve_referee_type(event.club, referee_type, RefereeType.Kind.REFEREE, RefereeAssignmentError)
    event = _lock_and_check_capacity(event)

    return EventReferee.objects.create(event=event, external_name=name, assigned_by=assigned_by, referee_type=referee_type)


def rename_external_referee(referee, name):
    """Correct the name of an externally logged referee. Member-linked rows are refused: their
    name comes from the member record."""
    name = (name or "").strip()
    if not name:
        raise RefereeAssignmentError(_("A name is required for an external referee."))
    if not referee.is_external:
        raise RefereeAssignmentError(_("Only an external referee can be renamed."))
    referee.external_name = name
    referee.save(update_fields=["external_name"])
    return referee


def set_referee_type(referee, referee_type):
    """Change (or clear, with None) the type of an existing assignment."""
    referee.referee_type = resolve_referee_type(referee.event.club, referee_type, RefereeType.Kind.REFEREE, RefereeAssignmentError)
    referee.save(update_fields=["referee_type"])
    return referee


@transaction.atomic
def remove_referee(referee):
    """Staff taking a referee off a game (management app). The person's own sign-up goes
    back to ACCEPTED (still interested, no longer picked), so the game stops showing as a
    confirmed duty in their app but stays on the interest list, and they are told. An external referee has
    no account behind them: only the assignment goes."""
    event, member = referee.event, referee.member
    referee.delete()
    if member is None:
        return
    RefereeSignup.objects.filter(event=event, member=member).update(status=RefereeSignup.Status.ACCEPTED, responded_at=timezone.now())

    def body():
        return _("You have been removed as a referee for %(event)s.") % {"event": event.title}

    notify_members([member], club=event.club, title=_("Referee assignment removed"), body=body, source=event, category=NotificationCategory.CREW)


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


def sync_referee_invites(event, *, notify=True, roster=None):
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
    new_members = [member for member in eligible_referees(event, roster=roster) if member.pk not in already_invited_ids]
    if not new_members:
        return []

    RefereeSignup.objects.bulk_create([RefereeSignup(event=event, member=member) for member in new_members])
    if notify:

        def body():
            return _("%(event)s needs a referee -- can you take it?") % {"event": event.title}

        notify_members(new_members, club=event.club, title=_("Referee needed"), body=body, source=event, category=NotificationCategory.CREW)
    return new_members


@transaction.atomic
def accept_referee_signup(signup):
    """Records that `signup`'s member wants to come (ACCEPTED) -- interest only. It
    creates no EventReferee and uses no capacity: staff pick who actually goes from the
    referee-management screen (confirm_referee_signup). Someone staff already confirmed
    stays CONFIRMED. Raises RefereeAssignmentError if the game no longer needs a club-arranged referee
    (e.g. it was moved away from home)."""
    if not needs_referee_management(signup.event):
        raise RefereeAssignmentError(_("Referees can only sign up for home games of club-managed teams."))
    if signup.status != RefereeSignup.Status.CONFIRMED:
        signup.status = RefereeSignup.Status.ACCEPTED
        signup.responded_at = timezone.now()
        signup.save(update_fields=["status", "responded_at"])
    return signup


@transaction.atomic
def confirm_referee_signup(signup, *, assigned_by):
    """Staff pick `signup`'s member for the game: the capacity-checked assign_referee
    (which marks the signup CONFIRMED and tells the member). Raises RefereeAssignmentError
    when the game is full or the member is already assigned; the signup is left as it was."""
    assign_referee(signup.event, signup.member, assigned_by=assigned_by)
    signup.refresh_from_db()
    return signup


@transaction.atomic
def decline_referee_signup(signup):
    """Declines `signup`. If they were assigned -- by staff (assign_referee marks the signup CONFIRMED) -- the
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

            notify_members(managers, club=signup.event.club, title=_("Referee dropped out"), body=body, source=signup.event, category=NotificationCategory.CREW)

    return signup
