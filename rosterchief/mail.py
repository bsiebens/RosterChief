"""Outgoing email: the pause switch for automated mail, and a backend that sends through Resend's HTTP API.

Which mailer sends is chosen in ``settings.MAILERS`` (console by default, SMTP when ``DJANGO_SMTP_HOST`` is set, Resend's API when
``RESEND_API_KEY`` is set). Resend's SMTP relay (``smtp.resend.com``, user ``resend``, the API key as password) also works with the SMTP settings.
"""

import base64
from email.mime.base import MIMEBase

import requests
from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend


def email_paused() -> bool:
    from controlpanel.models import EmailSuppression

    return EmailSuppression.is_on()


def send_message(message: EmailMessage, *, exempt: bool = False, fail_silently: bool = False) -> bool:
    """Send ``message`` unless automated email is paused. Account email (reset, invites) passes ``exempt=True``."""
    if not exempt and email_paused():
        return False
    return bool(message.send(fail_silently=fail_silently))


RESEND_API_URL = "https://api.resend.com/emails"


class ResendEmailBackend(BaseEmailBackend):
    """Sends each message to Resend's ``POST /emails`` (https://resend.com/docs/api-reference/emails/send-email) instead of over SMTP.

    Carries the plain text, the HTML alternative of an ``EmailMultiAlternatives``, cc, bcc, reply-to and attachments. Configure it as
    ``{"BACKEND": "rosterchief.mail.ResendEmailBackend", "OPTIONS": {"api_key": "..."}}`` in ``MAILERS``."""

    def __init__(self, fail_silently=False, *, api_key="", timeout=10, **kwargs):
        super().__init__(**kwargs)
        self.fail_silently = fail_silently
        self.api_key = api_key
        self.timeout = timeout

    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0
        if not self.api_key:
            if self.fail_silently:
                return 0
            raise ValueError("The Resend API key is not set (RESEND_API_KEY).")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        sent = 0
        with requests.Session() as session:
            for message in email_messages:
                try:
                    response = session.post(RESEND_API_URL, headers=headers, json=_payload_for(message), timeout=self.timeout)
                    try:
                        response.raise_for_status()
                    except requests.HTTPError as exc:
                        # Resend explains a refusal in the body (an invalid "from", an unverified domain); the status line alone does not.
                        raise requests.HTTPError(f"{exc} - Resend said: {response.text[:500]}", response=response) from exc
                except requests.RequestException:
                    if not self.fail_silently:
                        raise
                    continue
                sent += 1
        return sent


def _payload_for(message) -> dict:
    payload = {"from": message.from_email, "to": list(message.to), "subject": message.subject, "text": message.body}
    if message.cc:
        payload["cc"] = list(message.cc)
    if message.bcc:
        payload["bcc"] = list(message.bcc)
    if message.reply_to:
        payload["reply_to"] = list(message.reply_to)
    # An EmailMultiAlternatives (the templated mails) carries its HTML as an "alternative" to the text body.
    html = next((content for content, mimetype in getattr(message, "alternatives", []) if mimetype == "text/html"), None)
    if html:
        payload["html"] = html
    attachments = _attachments_for(message)
    if attachments:
        payload["attachments"] = attachments
    return payload


def _attachments_for(message) -> list[dict]:
    attachments = []
    for attachment in message.attachments:
        if isinstance(attachment, MIMEBase):
            filename, content = attachment.get_filename(), attachment.get_payload(decode=True)
        else:
            filename, content, _mimetype = attachment
            if isinstance(content, str):
                content = content.encode()
        attachments.append({"filename": filename, "content": base64.b64encode(content).decode("ascii")})
    return attachments
