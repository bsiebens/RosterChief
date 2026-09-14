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
        memberships = TeamMembership.objects.filter(team=registration.team, season=registration.tournament.season).select_related("member", "position")
        TournamentRosterEntry.objects.bulk_create(
            [
                TournamentRosterEntry(
                    tournament_team=team,
                    member=membership.member,
                    jersey_number=membership.jersey_number,
                    position_label=membership.position.name if membership.position_id else "",
                )
                for membership in memberships
            ]
        )
    else:
        TournamentRosterEntry.objects.bulk_create(
            [
                TournamentRosterEntry(
                    tournament_team=team,
                    foreign_first_name=player.first_name,
                    foreign_last_name=player.last_name,
                    jersey_number=player.jersey_number,
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
