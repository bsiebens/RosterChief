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


def snapshot_club_team_roster(tournament_team: TournamentTeam, team, season) -> None:
    """Bulk-copies ``team``'s own season roster (``TeamMembership``, scoped
    to ``season``) onto ``tournament_team`` as ``TournamentRosterEntry``
    rows -- not read live afterwards, same "snapshot, don't trail a live
    source" reasoning ``TournamentRosterEntry`` itself documents. The
    club-team half of both ``approve_registration`` (an approved club-team
    registration) and ``create_manual_team`` (the host club's own team,
    added directly from the Teams tab) -- extracted here so both go through
    exactly the same copy rather than two copies of the same loop drifting
    apart."""
    memberships = TeamMembership.objects.filter(team=team, season=season).select_related("member", "position")
    TournamentRosterEntry.objects.bulk_create(
        [
            TournamentRosterEntry(
                tournament_team=tournament_team,
                member=membership.member,
                jersey_number=membership.jersey_number,
                position_label=membership.position.name if membership.position_id else "",
            )
            for membership in memberships
        ]
    )


@transaction.atomic
def approve_registration(registration: TournamentRegistration, *, reviewed_by) -> TournamentTeam:
    """Creates the live ``TournamentTeam`` and its roster, then marks the
    registration approved. Roster comes from one of two places depending on
    what was actually registered:

    - A club-team registration snapshots the real season roster
      (``TeamMembership``, scoped to the tournament's own season) at
      approval time -- not read live afterwards, same "snapshot, don't
      trail a live source" reasoning ``TournamentRosterEntry`` itself
      documents, so a later roster edit on the parent team can't
      retroactively change who's eligible here.
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
        club=registration.tournament.club,
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
        snapshot_club_team_roster(team, registration.team, registration.tournament.season)
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

    team.delete()


@transaction.atomic
def create_manual_team(tournament, *, club_team=None, foreign_fields: dict | None = None, snapshot_roster: bool = True) -> TournamentTeam:
    """Adds a ``TournamentTeam`` directly, bypassing the public registration
    queue entirely -- the "host club skips the queue" shortcut
    ``TournamentTeam``'s own docstring already describes, extended to any
    team staff want to enter by hand (a late walk-up, one that registered by
    phone). Exactly one of ``club_team``/``foreign_fields`` is given by the
    caller (``TournamentTeamForm`` enforces that); a club team's roster is
    snapshotted the same way an approved club-team registration's is --
    unless ``snapshot_roster=False``, which ``ensure_home_team`` passes:
    the home team's roster is live (``tournaments/signals.py``'s
    ``sync_home_team_roster``), built up entirely from whichever members
    the coach actually selects via the auto-created Event's Attendance,
    not a one-time dump of the whole team regardless of who's been picked
    for this tournament."""
    if club_team is not None:
        # The club doesn't pay itself -- a club team entered here is always
        # already fully paid, no staff step required.
        team = TournamentTeam.objects.create(club=tournament.club, tournament=tournament, team=club_team, payment_status=TournamentTeam.PaymentStatus.FULLY_PAID)
        if snapshot_roster:
            snapshot_club_team_roster(team, club_team, tournament.season)
    else:
        team = TournamentTeam.objects.create(club=tournament.club, tournament=tournament, **foreign_fields)

    return team


@transaction.atomic
def ensure_home_team(tournament) -> TournamentTeam | None:
    """Auto-adds ``tournament.home_team`` as a ``TournamentTeam`` and gives
    the tournament its own linked calendar ``Event`` scoped to that team --
    called from ``TournamentCreateView``/``TournamentUpdateView.form_valid``
    on *every* save, not just the first one home_team is set on, so it has
    to be idempotent: does nothing at all when ``home_team`` is blank, and
    skips whichever half (team, event) already exists rather than
    duplicating it.

    The ``TournamentTeam`` goes through ``create_manual_team`` -- same
    "host club skips the queue" shortcut a manually-added team already
    uses, so the home team gets its FULLY_PAID default and club-logo
    fallback (``TournamentTeam.display_logo``) for free, no separate path
    -- but with ``snapshot_roster=False``: unlike every other club-team
    path in this app, the home team's roster is never a one-time
    TeamMembership dump, only ever the live Attendance-driven sync below.
    ``withdraw_team`` refuses to withdraw it (see that function's own
    guard).

    The ``Event`` is what makes "so that signup works" true: scoping it to
    ``teams={home_team}`` fires events.signals.sync_on_audience_change,
    which creates one ``Attendance`` row per team member the same way any
    other team event does -- from there, tournaments/signals.py's own
    ``sync_home_team_roster`` keeps ``TournamentRosterEntry`` in step with
    however those Attendances get answered, live, for as long as the
    tournament exists. Deliberately not passed via ``Event.objects.create
    (..., teams=...)`` -- a ManyToManyField can't be set that way at all;
    it has to be ``.set()`` *after* the row exists, which is also the only
    way that matters here: ``teams`` is an M2M, so ``sync_on_audience_change``
    only ever fires on an ``m2m_changed`` signal, never on the plain
    ``post_save`` a kwarg-only create would rely on.

    The linked ``Event``'s own ``teams`` is kept following ``home_team``
    on every call, not just the one that creates it -- changing the home
    team to a different one re-points the same event at the new team
    (``event.teams.set(...)`` only actually adds/removes what changed, so
    this is a cheap no-op when home_team hasn't moved). That's what keeps
    "so that signup works" true after a change, not just on the first
    save. The original home team's own ``TournamentTeam``/roster still
    just stand as an ordinary (no longer "home") entry once replaced --
    reconciling *those* is left to staff by hand via the Teams tab, not
    automated here; only the event's audience follows automatically.
    Clearing ``home_team`` back to blank leaves an already-linked event's
    teams alone (there's no way to tell a since-cleared home team's
    audience apart from one a club set on the event by hand before this
    feature existed) -- staff can always fix that manually via the event's
    own edit screen.

    The roster itself is reconciled against currently-SELECTED Attendance
    on *every* call too, not just left to tournaments/signals.py's
    ``sync_home_team_roster`` to keep up reactively -- that signal only
    ever fires on an ``Attendance`` save, so a ``TournamentTeam`` that
    already existed (and was already snapshotted, e.g. added by hand via
    the Teams tab's own "Add team" *before* being made the home team) would
    otherwise keep its stale full roster forever, since nothing would ever
    prompt a re-sync for members who never actually change their Attendance
    afterwards. Scoped to ``member__isnull=False`` rows only -- a foreign-
    named entry someone typed in by hand isn't part of the Attendance-
    driven sync at all, same scope the signal itself uses. Same "always
    live" tradeoff as that signal's own docstring already states plainly:
    a real Member manually added straight onto the roster (bypassing the
    normal sign-up/selection flow entirely) is removed here the next time
    the tournament is saved, if they're not also SELECTED -- accepted, not
    an oversight.
    """
    if tournament.home_team_id is None:
        return None

    team = TournamentTeam.objects.filter(tournament=tournament, team_id=tournament.home_team_id).first()
    if team is None:
        team = create_manual_team(tournament, club_team=tournament.home_team, snapshot_roster=False)

    if tournament.event_id is None:
        from events.models import Event  # local import -- see Tournament.location's own docstring for why every cross-app reference in this app is lazy

        start = timezone.make_aware(datetime.combine(tournament.start_date, time.min))
        end = timezone.make_aware(datetime.combine(tournament.end_date, time(23, 59)))
        event = Event.objects.create(club=tournament.club, season=tournament.season, kind=Event.EventKind.TOURNAMENT, title=tournament.name, start=start, end=end)
        tournament.event = event
        tournament.save(update_fields=["event"])

    tournament.event.teams.set([tournament.home_team])

    from events.models import Attendance  # local import -- see above

    from ..signals import ATTENDING_STATUSES  # local import -- avoids importing events.models at tournaments/services/registration.py's own module-load time (see above)

    selected_member_ids = set(Attendance.objects.filter(event=tournament.event, status__in=ATTENDING_STATUSES).values_list("member_id", flat=True))
    on_roster_member_ids = set(team.roster_entries.filter(member__isnull=False).values_list("member_id", flat=True))

    team.roster_entries.filter(member__isnull=False).exclude(member_id__in=selected_member_ids).delete()
    TournamentRosterEntry.objects.bulk_create([TournamentRosterEntry(tournament_team=team, member_id=member_id, role=TournamentRosterRole.PLAYER) for member_id in selected_member_ids - on_roster_member_ids])

    return team
