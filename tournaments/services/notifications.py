"""Tournament-registration emails: the confirmation sent right at public
submission, and the approve/reject decision notice sent once staff has acted
on it (tournaments.services.registration.approve_registration/
reject_registration call send_registration_decision_email themselves -- this
module doesn't call into that one, only the other way around, so a future
service there never has to import this module back).

Both are never fatal, same shape as every other branded send in this
codebase (see registration.services.notifications' own module docstring):
the underlying TournamentRegistration row exists in the database whether or
not the mail actually leaves the building.
"""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from rosterchief.mail import send_message


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
