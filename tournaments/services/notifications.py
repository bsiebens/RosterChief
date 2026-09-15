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

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _

from members.models import Member
from notifications.services import notify_members
from rosterchief.concurrency import run_in_background
from rosterchief.mail import send_message
from teams.models import TeamMembership

from ..models import Tournament


def _status_url(registration, *, request=None) -> str:
    path = reverse("tournaments:registration_status", kwargs={"tournament_slug": registration.tournament.slug, "token": registration.status_token})
    return request.build_absolute_uri(path) if request is not None else path


def send_registration_confirmation_email(registration, *, request=None) -> bool:
    # "request" rides along in the context for the .html template's own
    # {% absolute_media_url %} use (the club's logo), same as registration's
    # own send_registration_confirmation_email.
    context = {
        "club": registration.tournament.club,
        "tournament": registration.tournament,
        "registration": registration,
        "contact_name": registration.foreign_contact_name,
        "status_url": _status_url(registration, request=request),
        "request": request,
    }
    subject = " ".join(render_to_string("tournaments/email/confirmation_subject.txt", context).split())
    text_body = render_to_string("tournaments/email/confirmation.txt", context).strip() + "\n"
    html_body = render_to_string("tournaments/email/confirmation.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [registration.foreign_contact_email])
    message.attach_alternative(html_body, "text/html")

    try:
        return send_message(message, fail_silently=False)
    except OSError:
        # Anything the mail backend raises for an unreachable server or a
        # refused connection. The registration and its status page stand
        # either way.
        return False


def send_registration_decision_email(registration, *, request=None) -> bool:
    """Sent once staff has approved or rejected a pending registration --
    called from tournaments.services.registration's own
    approve_registration/reject_registration, or the staff review queue that
    calls those. One template set covering both outcomes (registration.status
    picks the wording inside it, same "branch inside the template" shape
    registration/templates/registration/status.html already uses for a
    membership's status) rather than a template per outcome."""
    context = {
        "club": registration.tournament.club,
        "tournament": registration.tournament,
        "registration": registration,
        "contact_name": registration.foreign_contact_name,
        "status_url": _status_url(registration, request=request),
        "request": request,
    }
    subject = " ".join(render_to_string("tournaments/email/decision_subject.txt", context).split())
    text_body = render_to_string("tournaments/email/decision.txt", context).strip() + "\n"
    html_body = render_to_string("tournaments/email/decision.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [registration.foreign_contact_email])
    message.attach_alternative(html_body, "text/html")

    try:
        return send_message(message, fail_silently=False)
    except OSError:
        return False


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
    tournament = Tournament.objects.filter(pk=tournament_id).select_related("club").first()
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
    notifications = notify_members(members, club=tournament.club, title=title, body=body, source=tournament)
    return f"Notified {len(notifications)} member(s)."


def dispatch_notify_tournament_shifts_open(tournament_id):
    """Runs notify_tournament_shifts_open on a daemon background thread so
    the request that just published the tournament doesn't wait on it --
    see rosterchief.concurrency.run_in_background."""
    run_in_background(notify_tournament_shifts_open, tournament_id)
