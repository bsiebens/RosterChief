"""Deciding a pending TournamentRegistration -- the one place "approved" or
"rejected" actually happens, so the public status page, the staff review
queue, and any future bulk-decision screen all go through the same rules
rather than each reimplementing them.
"""

from datetime import datetime, time

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from teams.models import TeamMembership

from ..models import TournamentRegistration, TournamentRosterEntry, TournamentRosterRole, TournamentTeam


class RegistrationDecisionError(Exception):
    """A registration could not be approved or rejected."""


class TeamWithdrawalError(Exception):
    """A team could not be withdrawn."""


class TeamDeletionError(Exception):
    """A team could not be deleted."""


def _guard_pending(registration: TournamentRegistration):
    if registration.status != TournamentRegistration.Status.PENDING:
        raise RegistrationDecisionError(_("This registration has already been decided."))


@transaction.atomic
def approve_registration(registration: TournamentRegistration, *, reviewed_by) -> TournamentTeam:
    """Creates the live ``TournamentTeam`` and its roster, then marks the
    registration approved. Roster comes from one of two places depending on
    what was actually registered:

    - A club-team registration gets its own calendar event, whose
      line-up decides who is on the roster (``enter_club_team``), and
      its staff for the tournament's season.
    - A foreign-team registration copies whatever was submitted with it
      (``TournamentRegistrationPlayer``), which only exists for exactly
      this purpose.

    Raises ``RegistrationDecisionError`` if the registration isn't pending
    (already approved/rejected -- e.g. a double-click, or two admins acting
    on the same queue at once).
    """
    registration = TournamentRegistration.objects.select_for_update().get(pk=registration.pk)
    _guard_pending(registration)

    team = TournamentTeam.objects.create(
        tournament=registration.tournament,
        registration=registration,
        team=registration.team,
        foreign_name=registration.foreign_name,
        foreign_contact_name=registration.foreign_contact_name,
        foreign_contact_email=registration.foreign_contact_email,
        foreign_contact_phone=registration.foreign_contact_phone,
        foreign_primary_color=registration.foreign_primary_color,
        foreign_secondary_color=registration.foreign_secondary_color,
        foreign_logo=registration.foreign_logo,
    )

    if registration.team_id is not None:
        enter_club_team(team)  # its own calendar event, whose line-up decides the roster, and its staff
    else:
        TournamentRosterEntry.objects.bulk_create(
            [
                TournamentRosterEntry(
                    tournament_team=team,
                    foreign_first_name=player.first_name,
                    foreign_last_name=player.last_name,
                    jersey_number=player.jersey_number,
                    role=player.role,
                    position_label=player.position_label,
                    date_of_birth=player.date_of_birth,
                    license_number=player.license_number,
                    dietary_restrictions=player.dietary_restrictions,
                    lunch_days=player.lunch_days,
                )
                for player in registration.players.all()
            ]
        )

    registration.status = TournamentRegistration.Status.APPROVED
    registration.reviewed_by = reviewed_by
    registration.reviewed_at = timezone.now()
    registration.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    return team


@transaction.atomic
def reject_registration(registration: TournamentRegistration, *, reviewed_by, reason: str = "") -> TournamentRegistration:
    """Marks a pending registration rejected -- no ``TournamentTeam`` is ever
    created for it. ``reason`` is shown back to the submitter on their own
    status page. Raises ``RegistrationDecisionError`` under the same
    already-decided guard as ``approve_registration``."""
    registration = TournamentRegistration.objects.select_for_update().get(pk=registration.pk)
    _guard_pending(registration)

    registration.status = TournamentRegistration.Status.REJECTED
    registration.rejection_reason = reason
    registration.reviewed_by = reviewed_by
    registration.reviewed_at = timezone.now()
    registration.save(update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at"])

    return registration


@transaction.atomic
def withdraw_team(team: TournamentTeam, *, reason: str = "", withdrawn_by=None) -> TournamentTeam:
    """Marks an already-in ``TournamentTeam`` withdrawn -- a soft state
    (``withdrawn_at``/``withdrawal_reason``), not a delete, same reasoning
    ``club.models.Club.archived_at`` already gives: matches already scheduled
    against this team, and its roster/dressing-room history, all stay
    intact. ``reason`` is shown back on the team's own registration status
    page (when it has one -- a manually-added team never does) the same way
    ``reject_registration``'s own ``reason`` is. Freeing a slot here is
    implicit: ``Tournament.accepts_registrations`` counts non-withdrawn
    teams live, so nothing else needs to react to this.

    Raises ``TeamWithdrawalError`` if the team is already withdrawn (e.g. a
    double-click, or two admins acting on the same team at once) --
    ``withdrawn_by`` isn't persisted anywhere yet (no such field on the
    model), accepted for symmetry with the request-scoped caller and future
    use, same as ``MatchReferee.assigned_by``'s own shape elsewhere in this
    app.
    """
    team = TournamentTeam.objects.select_for_update().get(pk=team.pk)
    if team.withdrawn_at is not None:
        raise TeamWithdrawalError(_("This team has already been withdrawn."))
    if team.is_home_team:
        raise TeamWithdrawalError(_("The home team can't be withdrawn -- change or clear the tournament's home team in Settings instead."))

    team.withdrawn_at = timezone.now()
    team.withdrawal_reason = reason
    team.save(update_fields=["withdrawn_at", "withdrawal_reason"])
    if team.event_id is not None:  # its calendar event stays for the record but is no longer on
        team.event.cancelled = True
        team.event.save(update_fields=["cancelled"])

    return team


@transaction.atomic
def delete_manual_team(team: TournamentTeam):
    """Actually removes a ``TournamentTeam`` -- unlike ``withdraw_team``'s
    soft state, only ever appropriate for one entered by hand
    (``registration_id`` is null): there's no registration/audit trail
    behind it worth preserving, so leaving a permanent "withdrawn" row
    around for a mistaken or test entry is pure clutter, not history.
    A real applicant's team (``registration_id`` set) must go through
    ``withdraw_team`` instead -- its own status page depends on the
    ``TournamentTeam`` row still existing to show the withdrawal reason.

    Raises ``TeamDeletionError`` for a registration-backed team or the
    tournament's own home team (same "can't remove the home team" rule
    ``withdraw_team`` enforces). Roster entries and dressing-room
    assignments cascade-delete with it; any match already scheduled
    against it just loses that side (``Match.home_team``/``away_team``
    are ``SET_NULL``), same as a withdrawal leaves them.
    """
    team = TournamentTeam.objects.select_for_update().get(pk=team.pk)
    if team.registration_id is not None:
        raise TeamDeletionError(_("This team came from a registration -- withdraw it instead of deleting it, so its own status page can still show why."))
    if team.is_home_team:
        raise TeamDeletionError(_("The home team can't be removed -- change or clear the tournament's home team in Settings instead."))

    event = team.event
    team.delete()
    if event is not None:  # the calendar event was made for this entry
        event.delete()


@transaction.atomic
def create_manual_team(tournament, *, club_team=None, foreign_fields: dict | None = None) -> TournamentTeam:
    """Adds a ``TournamentTeam`` directly, bypassing the public registration
    queue entirely -- the "host club skips the queue" shortcut
    ``TournamentTeam``'s own docstring already describes, extended to any
    team staff want to enter by hand (a late walk-up, one that registered by
    phone). Exactly one of ``club_team``/``foreign_fields`` is given by the
    caller. A club team enters like the home team does
    (``enter_club_team``): its own calendar event, whose line-up decides
    the roster, plus its staff."""
    if club_team is not None:
        # The club doesn't pay itself -- a club team entered here is always
        # already fully paid, no staff step required.
        team = TournamentTeam.objects.create(tournament=tournament, team=club_team, payment_status=TournamentTeam.PaymentStatus.FULLY_PAID)
        enter_club_team(team)
    else:
        team = TournamentTeam.objects.create(tournament=tournament, **foreign_fields)

    return team


@transaction.atomic
def add_team_staff(tournament_team: TournamentTeam, team, season) -> None:
    """Adds ``team``'s staff for ``season`` (coaches, managers ...) to ``tournament_team``'s roster as staff, with their position as the
    label. A one-time copy, unlike the players, so the organisers can then add and remove staff by hand as the tournament needs."""
    from teams.models import StaffAssignment  # local import -- see ensure_home_team

    assignments = StaffAssignment.objects.filter(team=team, season=season).select_related("position")
    TournamentRosterEntry.objects.bulk_create(
        [TournamentRosterEntry(tournament_team=tournament_team, member_id=a.member_id, role=TournamentRosterRole.STAFF, position_label=a.position.name) for a in assignments]
    )


def sync_team_roster(team: TournamentTeam) -> None:
    """Makes a club team's roster mirror the line-up of its calendar event: the players the coach picked there, minus
    anyone who has since declined (absent or excused). Live, whether or not the line-up has been published, because the roster is who
    the coach is fielding. Only member players are added and removed: staff and guests typed in by hand are never touched, and someone
    who stays picked keeps whatever was filled in on their roster line (position, licence, lunch ...)."""
    if team.team_id is None or team.event_id is None:
        return
    tournament = team.tournament

    from events.models import Attendance, LineupSelection  # local import -- see ensure_home_team

    declined = {Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.EXCUSED}
    picked = set(LineupSelection.objects.filter(lineup__event_id=team.event_id).values_list("member_id", flat=True))
    wanted = picked - set(Attendance.objects.filter(event_id=team.event_id, status__in=declined).values_list("member_id", flat=True))
    on_roster = set(team.roster_entries.filter(member__isnull=False).values_list("member_id", flat=True))  # any role: a staff member who is also picked is not added twice

    team.roster_entries.filter(member__isnull=False, role=TournamentRosterRole.PLAYER).exclude(member_id__in=wanted).delete()

    # Jersey number and position come from the player's place in the team for the tournament's season. They are only filled in where the
    # roster line has none, so whatever was typed by hand sticks, and a number that is already taken on this roster is left out.
    memberships = {m.member_id: m for m in TeamMembership.objects.filter(team_id=team.team_id, season=tournament.season, member_id__in=wanted).select_related("position")}
    taken = set(team.roster_entries.exclude(jersey_number__isnull=True).values_list("jersey_number", flat=True))

    def take_over(membership):
        number = membership.jersey_number if membership and membership.jersey_number not in taken else None
        if number is not None:
            taken.add(number)
        return number, (membership.position.name if membership and membership.position_id else "")

    for entry in team.roster_entries.filter(member_id__in=wanted & on_roster, role=TournamentRosterRole.PLAYER):
        if entry.jersey_number is not None and entry.position_label:
            continue
        number, position = take_over(memberships.get(entry.member_id))
        changed = []
        if entry.jersey_number is None and number is not None:
            entry.jersey_number = number
            changed.append("jersey_number")
        if not entry.position_label and position:
            entry.position_label = position
            changed.append("position_label")
        if changed:
            entry.save(update_fields=[*changed, "modified"])
    new_entries = []
    for member_id in wanted - on_roster:
        number, position = take_over(memberships.get(member_id))
        new_entries.append(TournamentRosterEntry(tournament_team=team, member_id=member_id, role=TournamentRosterRole.PLAYER, jersey_number=number, position_label=position))
    TournamentRosterEntry.objects.bulk_create(new_entries)


def _event_span(tournament):
    """The calendar event's start and end: the first day's start until the last day's end, else (a tournament without days rows)
    the whole of ``start_date`` until the end of ``end_date``."""
    span = tournament.first_start_and_last_end()
    start, end = span if span else (datetime.combine(tournament.start_date, time.min), datetime.combine(tournament.end_date, time(23, 59)))
    return timezone.make_aware(start), timezone.make_aware(end)


def ensure_team_event(team: TournamentTeam):
    """Gives a club team its calendar event -- kind tournament, scoped to that team, so its players are invited and RSVP like to any
    other event and its coach picks the roster on the event's line-up -- and keeps the event's span on the tournament's days (first day's
    start until the last day's end). Idempotent: an existing event is only moved, never duplicated. The home team's event is the
    tournament's own ``event``; every other club team gets one of its own, titled with the team's name. Returns ``None`` for a guest team."""
    if team.team_id is None:
        return None
    from events.models import Event  # local import -- see Tournament.location's own docstring for why every cross-app reference in this app is lazy

    tournament = team.tournament
    is_home = team.team_id == tournament.home_team_id
    event = team.event or (tournament.event if is_home else None)
    start, end = _event_span(tournament)
    if event is None:
        title = tournament.name if is_home else f"{tournament.name} — {team.team.name}"
        event = Event.objects.create(season=tournament.season, kind=Event.EventKind.TOURNAMENT, title=title, start=start, end=end)
    elif (event.start, event.end) != (start, end):
        # The days were changed: the calendar item follows them.
        event.start, event.end = start, end
        event.save(update_fields=["start", "end"])

    # One event belongs to one team: when the home team changed, the previous home team lets go of the tournament's event.
    TournamentTeam.objects.filter(event=event).exclude(pk=team.pk).update(event=None)
    if team.event_id != event.pk:
        team.event = event
        team.save(update_fields=["event"])
    if is_home and tournament.event_id != event.pk:
        tournament.event = event
        tournament.save(update_fields=["event"])
    event.teams.set([team.team])
    return event


@transaction.atomic
def enter_club_team(team: TournamentTeam) -> None:
    """A club team has entered the tournament: its calendar event, its staff for the tournament's season (a one-time copy, kept by
    hand afterwards), and the players its line-up picks (none yet, until the coach picks them)."""
    ensure_team_event(team)
    add_team_staff(team, team.team, team.tournament.season)
    sync_team_roster(team)


def ensure_home_team(tournament) -> TournamentTeam | None:
    """Enters ``tournament.home_team`` (the host club's own team, skipping the registration queue) and makes sure every club team in the
    tournament has its calendar event on the tournament's days. Called on every save of the tournament, so it is idempotent: it does
    nothing when ``home_team`` is blank, and only completes whatever is missing.

    The home team's event is the tournament's own ``event``; changing the home team re-points it at the new team, and the previous home
    team gets an event of its own. The home team's roster follows its line-up on every call, like any other team's follows its own when
    the coach saves it. A club team entered before it had an event only gets the event here, never a re-sync: whatever roster it has
    stays until its line-up is saved."""
    if tournament.home_team_id is None:
        return None

    team = TournamentTeam.objects.filter(tournament=tournament, team_id=tournament.home_team_id).select_related("team", "event").first()
    if team is None:
        team = create_manual_team(tournament, club_team=tournament.home_team)  # enters it: event, staff, roster
    else:
        team.tournament = tournament  # one instance, so the tournament's own event link stays current for the caller
        ensure_team_event(team)
        sync_team_roster(team)

    for other in tournament.teams.filter(team__isnull=False, withdrawn_at__isnull=True).exclude(pk=team.pk).select_related("team", "event"):
        other.tournament = tournament
        ensure_team_event(other)

    return team
