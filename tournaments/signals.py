"""Signal wiring for the tournaments app -- registered from
TournamentsConfig.ready, same shape events.signals uses for its own
receivers.

Currently just one: keeping a tournament's home-team roster in sync with its
own auto-created Event's Attendance (tournaments.services.registration.
ensure_home_team creates that Event scoped to home_team, which is what makes
normal event RSVP/signup decide who's actually on the roster). Lives here,
not in events/signals.py, so tournament-specific logic stays out of the
events app -- Django's signal dispatch doesn't care which app a receiver is
registered from, only that it's connected before the signal fires.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from events.models import Attendance

from .models import Tournament, TournamentRosterEntry, TournamentRosterRole

#: The Attendance status that counts as "confirmed to play" for a home
#: team's roster -- deliberately just SELECTED (explicitly picked onto the
#: roster by a coach), not also PRESENT (a plain "I'm coming" RSVP): a home
#: tournament roster is who the coach is actually fielding, same distinction
#: a normal game day already draws between "attending" and "selected."
ATTENDING_STATUSES = {Attendance.AttendanceStatus.SELECTED}


@receiver(post_save, sender=Attendance)
def sync_home_team_roster(sender, instance, **kwargs):
    """Keeps a tournament's home-team ``TournamentRosterEntry`` rows in step
    with ``Attendance`` on the tournament's own linked ``Event`` --
    deliberately live, not a one-time snapshot the way every other roster in
    this app is (see ``snapshot_club_team_roster``), because the entire
    point of the auto-created Event is that ordinary event sign-up decides
    who's actually coming.

    Cheaply a no-op for the overwhelming majority of ``Attendance`` saves in
    this system: only an event that IS some tournament's own linked event
    reaches past the first line below, so a normal event's RSVPs are
    completely unaffected.

    Real, accepted tradeoff (stated, not an oversight): a
    ``TournamentRosterEntry`` created here starts with nothing but a member
    and the default PLAYER role -- Attendance carries no position/DOB/
    license/dietary/lunch info, staff fill that in by hand on the roster
    screen afterwards. Flipping AWAY from an attending status deletes the
    entry outright rather than archiving it, so any of that hand-entered
    detail is lost if someone un-RSVPs and later re-RSVPs -- a direct cost
    of "always live" per the user's own explicit choice, not a bug.
    """
    try:
        tournament = instance.event.tournament
    except Tournament.DoesNotExist:
        return
    if tournament.home_team_id is None:
        return

    tournament_team = tournament.teams.filter(team_id=tournament.home_team_id).first()
    if tournament_team is None:
        return

    if instance.status in ATTENDING_STATUSES:
        TournamentRosterEntry.objects.get_or_create(tournament_team=tournament_team, member=instance.member, defaults={"role": TournamentRosterRole.PLAYER})
    else:
        TournamentRosterEntry.objects.filter(tournament_team=tournament_team, member=instance.member).delete()
