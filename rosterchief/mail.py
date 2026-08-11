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
