"""Deciding a pending TournamentRegistration -- the one place "approved" or
"rejected" actually happens, so the public status page, the staff review
queue, and any future bulk-decision screen all go through the same rules
rather than each reimplementing them.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from teams.models import TeamMembership

from ..models import TournamentRegistration, TournamentRosterEntry, TournamentTeam


class RegistrationDecisionError(Exception):
    """A registration could not be approved or rejected."""


class TeamWithdrawalError(Exception):
    """A team could not be withdrawn."""


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

    team.withdrawn_at = timezone.now()
    team.withdrawal_reason = reason
    team.save(update_fields=["withdrawn_at", "withdrawal_reason"])

    return team


@transaction.atomic
def create_manual_team(tournament, *, club_team=None, foreign_fields: dict | None = None) -> TournamentTeam:
    """Adds a ``TournamentTeam`` directly, bypassing the public registration
    queue entirely -- the "host club skips the queue" shortcut
    ``TournamentTeam``'s own docstring already describes, extended to any
    team staff want to enter by hand (a late walk-up, one that registered by
    phone). Exactly one of ``club_team``/``foreign_fields`` is given by the
    caller (``TournamentTeamForm`` enforces that); a club team's roster is
    snapshotted the same way an approved club-team registration's is."""
    if club_team is not None:
        team = TournamentTeam.objects.create(club=tournament.club, tournament=tournament, team=club_team)
        snapshot_club_team_roster(team, club_team, tournament.season)
    else:
        team = TournamentTeam.objects.create(club=tournament.club, tournament=tournament, **foreign_fields)

    return team
