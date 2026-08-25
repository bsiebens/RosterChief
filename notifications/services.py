"""Fan a notification out to members -- and, always, their parents/guardians.

Built for news publishing first (see news.tasks.notify_news_published), but
recipient resolution and delivery live here, generically, so any future
"notify these members about X" need reuses this instead of writing its own
version of the same guardian-fallback logic club.services.invoicing.recipient_for
already established for dues invoices.
"""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from rosterchief.mail import send_message

from .models import Notification


def recipient_emails(member) -> list[str]:
    """The member's own email if they hold a login, plus every parent/guardian's,
    always -- a child with their own account doesn't opt their parents out of
    also being told. De-duplicated, order-stable. Empty means nobody reachable
    at all."""
    seen, emails = set(), []
    if member.user_id and member.contact_email:
        seen.add(member.contact_email)
        emails.append(member.contact_email)
    for guardian in member.guardians.order_by("last_name", "first_name"):
        email = guardian.contact_email
        if email and email not in seen:
            seen.add(email)
            emails.append(email)
    return emails


def _send_email(notification: Notification, emails: list[str]) -> None:
    context = {"club": notification.club, "notification": notification}
    subject = " ".join(render_to_string("notifications/email/notification_subject.txt", context).split())
    text_body = render_to_string("notifications/email/notification.txt", context).strip() + "\n"
    html_body = render_to_string("notifications/email/notification.html", context)

    for email in emails:
        message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [email])
        message.attach_alternative(html_body, "text/html")
        try:
            send_message(message, fail_silently=False)
        except OSError:
            # Never fatal -- the Notification row (and whichever other addresses
            # in this same batch do go out) stands either way, same reasoning as
            # every other branded send in this app (see e.g.
            # members.services.claims.send_claim_approved_email).
            continue


def notify_members(members, *, club, title: str, body: str, source=None, send_email: bool = True) -> list[Notification]:
    """One Notification per member. Emailed to everyone recipient_emails()
    resolves for them, unless send_email is False -- e.g. a staff review
    queue (see news.services.notify_editors_of_pending_review), which is
    in-app only: it doesn't need every editor emailed on every submission,
    just the topbar/dashboard entry. Always creates the row, even when
    nobody was reachable or emailing was skipped -- that's still true
    history for the in-app feed, not a failure to silently drop."""
    notifications = []
    for member in members:
        notification = Notification.objects.create(club=club, member=member, title=title, body=body, source=source)
        if send_email:
            emails = recipient_emails(member)
            if emails:
                _send_email(notification, emails)
                notification.sent_at = timezone.now()
                notification.sent_to_emails = emails
                notification.save(update_fields=["sent_at", "sent_to_emails", "modified"])
        notifications.append(notification)
    return notifications
