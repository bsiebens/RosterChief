"""Outgoing automated email goes through here so the platform can pause it (controlpanel email suppression)."""

from django.core.mail import EmailMessage


def email_paused() -> bool:
    from controlpanel.models import EmailSuppression

    return EmailSuppression.is_on()


def send_message(message: EmailMessage, *, exempt: bool = False, fail_silently: bool = False) -> bool:
    """Send ``message`` unless automated email is paused. Account email (reset, invites) passes ``exempt=True``."""
    if not exempt and email_paused():
        return False
    return bool(message.send(fail_silently=fail_silently))
