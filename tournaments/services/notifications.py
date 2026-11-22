"""Tournament notifications: the two registration emails (confirmation at
public submission, decision once staff has acted) plus, below them,
notify_tournament_shifts_open -- a fire-and-forget in-app/email notification
to the home team's own roster the moment a tournament is published, mirroring
events.services.notifications.notify_new_event/dispatch_notify_new_event
exactly (see that module's own docstring for why a plain background thread,
not a task queue).

The two registration emails are never fatal, same shape as every other
branded send in this codebase (see registration.services.notifications' own
module docstring): the underlying TournamentRegistration row exists in the
database whether or not the mail actually leaves the building.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils.translation import gettext as _

from members.models import Member
from members.services import recipients
from notifications import inbox
from rosterchief.mail import send_message
from teams.models import TeamMembership

from ..models import Tournament

log = logging.getLogger(__name__)


def _club():
    from events.services.attendance import current_club

    return current_club()


def _status_url(registration, *, request=None) -> str:
    """The public status page of a registration: ``<club base url>/tournaments/<year>/<slug>/registration/<token>``."""
    from registration.services.notifications import base_url

    club = _club()
    base = base_url(club) if club is not None else ""
    if not base and request is not None:
        base = f"{request.scheme}://{request.get_host()}"
    return f"{base}{registration.tournament.public_path}/registration/{registration.status_token}"


def _send(registration, subject: str, lines: list[str]) -> bool:
    """Plain-text mail to the registration's contact. Never fatal: the registration exists whether or not the mail leaves."""
    message = EmailMultiAlternatives(subject, "\n\n".join(lines).strip() + "\n", settings.DEFAULT_FROM_EMAIL, [registration.foreign_contact_email])
    try:
        return send_message(message, fail_silently=False)
    except OSError:
        # Anything the mail backend raises for an unreachable server or a refused connection.
        log.warning("tournaments.registration_mail_failed", exc_info=True)
        return False


def send_registration_confirmation_email(registration, *, request=None) -> bool:
    club = _club()
    club_name = club.name if club is not None else ""
    tournament = registration.tournament
    return _send(
        registration,
        " ".join((_("%(club)s: we received your registration for %(tournament)s") % {"club": club_name, "tournament": tournament.name}).split()),
        [
            _("Hi %(name)s,") % {"name": registration.foreign_contact_name},
            _("We received the registration of %(team)s for %(tournament)s. We will review it and come back to you.") % {"team": registration.foreign_name, "tournament": tournament.name},
            _("Follow your registration here: %(url)s") % {"url": _status_url(registration, request=request)},
        ],
    )


def send_registration_decision_email(registration, *, request=None) -> bool:
    """Sent once staff has approved or rejected a pending registration. One wording covering both outcomes, picked by ``registration.status``."""
    club = _club()
    club_name = club.name if club is not None else ""
    tournament = registration.tournament
    names = {"club": club_name, "tournament": tournament.name, "team": registration.foreign_name}
    if registration.status == registration.Status.APPROVED:
        subject = _("%(club)s: your registration for %(tournament)s is approved") % names
        body = _("Good news: the registration of %(team)s for %(tournament)s is approved.") % names
    else:
        subject = _("%(club)s: your registration for %(tournament)s was not accepted") % names
        body = _("Unfortunately the registration of %(team)s for %(tournament)s was not accepted.") % names
    lines = [_("Hi %(name)s,") % {"name": registration.foreign_contact_name}, body]
    reason = getattr(registration, "rejection_reason", "")
    if registration.status != registration.Status.APPROVED and reason:
        lines.append(reason)
    lines.append(_("Details: %(url)s") % {"url": _status_url(registration, request=request)})
    return _send(registration, " ".join(subject.split()), lines)


def notify_tournament_shifts_open(tournament_id):
    """Scheduled from management.views.TournamentCreateView/
    TournamentUpdateView.form_valid the moment a tournament's status
    transitions to PUBLISHED -- not every subsequent save while already
    published (see those views' own "was this JUST published" check, the
    same "deliberately planned, not every row" restraint
    events.services.notifications.notify_new_event's own docstring
    describes for its own dispatch site).

    Notifies the home team's whole squad (``TeamMembership`` for the
    tournament's own season), not just whoever's already been selected onto
    the tournament roster -- the entire point of this notification is to
    ask people to *go sign up* (tournaments/signals.py's sync_home_team_roster
    only ever adds a TournamentRosterEntry once a coach has SELECTED someone
    via that sign-up), so querying the roster itself here would almost
    always find nobody yet, right when a fresh notification is most useful.
    Skipped entirely (same "Skipped: ..." early-return shape notify_new_event
    uses) when there's no home_team, the home team's own TournamentTeam
    hasn't been created yet, there are no shifts defined, or the team has
    no members for this season to notify."""
    tournament = Tournament.objects.filter(pk=tournament_id).first()
    if tournament is None or tournament.home_team_id is None:
        return "Skipped: no tournament or no home team."
    if not tournament.tasks.exists():
        return "Skipped: no shifts defined yet."

    home_team_entry = tournament.teams.filter(team_id=tournament.home_team_id).first()
    if home_team_entry is None:
        return "Skipped: home team not added yet."

    member_ids = TeamMembership.objects.filter(team_id=tournament.home_team_id, season=tournament.season).values_list("member_id", flat=True)
    members = Member.objects.filter(id__in=member_ids)
    if not members:
        return "Skipped: no one to notify."

    body = _("“%(name)s” is published and needs volunteers for its shifts -- take a look and sign up for one that works for you.") % {"name": tournament.name}
    title = _("Volunteer shifts open: %(name)s") % {"name": tournament.name}
    club = _club()
    href = f"/tournaments/{tournament.pk}"
    told = set()
    for member in members:
        for user in recipients.deliver(member, club, title=title, body=body, kind=inbox.Kind.INFO, href=href, push=True, category="event"):
            told.add(user.pk)
    return f"Notified {len(told)} user(s)."


def dispatch_notify_tournament_shifts_open(tournament_id):
    """Queues the Celery task ``tournaments.notify_shifts_open`` after the commit so the request that just published the
    tournament doesn't wait on it; it runs in the club schema it was queued from."""
    from tournaments import tasks

    transaction.on_commit(lambda: tasks.notify_shifts_open.delay(str(tournament_id)))


def send_organiser_message_email(registration, body: str, *, request=None) -> bool:
    """Tell a team's contact that the organisers wrote to them: the text and the private link where they can answer."""
    t = registration.tournament
    lines = [_("The organisers of %(tournament)s sent a message to your team:") % {"tournament": t.name}, body, _("Read and answer it on your team page: %(url)s") % {"url": _status_url(registration, request=request)}]
    return _send(registration, _("New message from the organisers of %(tournament)s") % {"tournament": t.name}, lines)
