"""Django email backend that sends through Resend's HTTP API
(https://resend.com/docs/api-reference/emails/send-email) instead of SMTP.

Opt in with DJANGO_EMAIL_BACKEND=rosterchief.mail.ResendEmailBackend and
RESEND_API_KEY set (see settings.py's Email section) -- every Django-sent
email (allauth's password reset, billing reminders, ...) goes through
django.core.mail's EMAIL_BACKEND setting, so nothing else has to change to
route mail through Resend once this is configured.

Resend's own SMTP relay is also a valid, code-free alternative (point the
stock django.core.mail.backends.smtp.EmailBackend at it with your API key as
the SMTP password) -- this backend exists for teams who'd rather go through
Resend's HTTP API directly.
"""

import base64
from email.mime.base import MIMEBase

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

RESEND_API_URL = "https://api.resend.com/emails"
REQUEST_TIMEOUT = 10


def send_message(message, *, exempt: bool = False, fail_silently: bool = False) -> bool:
    """The one choke point every automated send in the app goes through instead of
    calling ``message.send()`` directly -- club/services/invoicing.py, members/
    services/claims.py, billing/services/reminders.py, and notifications/services.py
    all route through this, as does authentication.adapters.RosterChiefAccountAdapter
    for allauth's own mail -- so the control panel's "pause automated email" switch
    (features.models.EmailSuppression) actually silences everything at once instead
    of each call site needing its own check.

    ``exempt=True`` skips the check entirely -- used only for password reset (see
    the adapter above), so the switch can never lock someone out of their own
    account. Returns whether the message was actually handed to the backend, not
    whether delivery succeeded -- that's still on message.send()'s own return value/
    exception, unchanged from before this existed."""
    from features.models import EmailSuppression

    if not exempt and EmailSuppression.is_on():
        return False
    return bool(message.send(fail_silently=fail_silently))


class ResendEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0

        api_key = settings.RESEND_API_KEY
        if not api_key:
            if self.fail_silently:
                return 0
            raise ValueError("RESEND_API_KEY is not set.")

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        sent = 0
        with requests.Session() as session:
            for message in email_messages:
                try:
                    response = session.post(RESEND_API_URL, headers=headers, json=_payload_for(message), timeout=REQUEST_TIMEOUT)
                    response.raise_for_status()
                except requests.RequestException:
                    if not self.fail_silently:
                        raise
                    continue
                sent += 1

        return sent


def _payload_for(message) -> dict:
    payload = {
        "from": message.from_email,
        "to": list(message.to),
        "subject": message.subject,
        "text": message.body,
    }
    if message.cc:
        payload["cc"] = list(message.cc)
    if message.bcc:
        payload["bcc"] = list(message.bcc)
    if message.reply_to:
        payload["reply_to"] = list(message.reply_to)

    # EmailMultiAlternatives (what allauth's templated emails use) carries the
    # HTML version as an "alternative" to the plain-text body, not a separate field.
    html_body = next((content for content, mimetype in getattr(message, "alternatives", []) if mimetype == "text/html"), None)
    if html_body:
        payload["html"] = html_body

    attachments = _attachments_for(message)
    if attachments:
        payload["attachments"] = attachments

    return payload


def _attachments_for(message) -> list[dict]:
    attachments = []
    for attachment in message.attachments:
        if isinstance(attachment, MIMEBase):
            filename = attachment.get_filename()
            content = attachment.get_payload(decode=True)
        else:
            filename, content, _mimetype = attachment
            if isinstance(content, str):
                content = content.encode()
        attachments.append({"filename": filename, "content": base64.b64encode(content).decode("ascii")})
    return attachments
