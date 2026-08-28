"""The confirmation email a registration batch gets once submitted -- one
link to registration.views.RegistrationStatusView (RegistrationBatch.
status_token), where the registrant can check where each entry stands and
upload a document for any open onboarding requirement that asks for one.
Same shape as members.services.claims.send_claim_approved_email: never
fatal, since the batch (and its status page) exist in the database whether
or not the mail actually leaves the building."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from rosterchief.mail import send_message


def send_registration_confirmation_email(batch, *, request=None):
    path = reverse("registration:status", kwargs={"token": batch.status_token})
    status_url = request.build_absolute_uri(path) if request is not None else path

    # "request" rides along in the context for the .html template's own
    # {% absolute_media_url %} use (the club's logo), same as claims.
    # send_claim_approved_email's own context.
    context = {"club": batch.club, "batch": batch, "contact_first_name": batch.contact_first_name, "status_url": status_url, "request": request}
    subject = " ".join(render_to_string("registration/email/confirmation_subject.txt", context).split())
    text_body = render_to_string("registration/email/confirmation.txt", context).strip() + "\n"
    html_body = render_to_string("registration/email/confirmation.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [batch.contact_email])
    message.attach_alternative(html_body, "text/html")

    try:
        return send_message(message, fail_silently=False)
    except OSError:
        # Anything the mail backend raises for an unreachable server or a
        # refused connection. The batch and its status page stand either way.
        return False
