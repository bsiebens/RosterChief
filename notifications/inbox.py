"""The in-app inbox (the bell in the management app): create notifications for people, list, mark read, clear.

Anything in the code base that wants to tell someone something calls ``notify`` (or ``notify_many``); the management
app reads them through ``/api/v1/notifications``. Push and email delivery are separate (see ``notifications.services``)."""

import logging

from django.utils import timezone

from .models import InboxNotification

LIST_LIMIT = 50
Kind = InboxNotification.Kind


def notify(user, club, *, title: str, body: str = "", kind: str = Kind.INFO, href: str = "", push: bool = False, category: str = "general", priority: str = "normal", send_email: bool = False) -> InboxNotification:
    """Put a message in the user's inbox and, with ``push=True``, also queue a push (and optionally an email) through the
    push engine. The engine applies the user's own settings when it delivers: the per-category and master switches, quiet
    hours and email mode (``notifications.preferences``), so a user who switched a category off gets the inbox entry only.
    ``category`` is one of ``notifications.models.Category`` (event, lineup, coach, ...). Pushing never makes the inbox
    write fail."""
    item = InboxNotification.objects.create(user=user, club=club, kind=kind, title=title[:255], body=body, href=href[:500])
    if push:
        try:
            from .services import notify_user

            notify_user(user, title=title, body=body, category=category, priority=priority, club=club, notification_id=item.pk, data={"href": href, "inbox_id": str(item.pk)}, send_email=send_email)
        except Exception:
            logging.getLogger(__name__).exception("inbox.push_failed")
    return item


def notify_many(users, club, **kwargs) -> int:
    n = 0
    for user in users:
        notify(user, club, **kwargs)
        n += 1
    return n


def inbox(user, club):
    return InboxNotification.objects.filter(user=user, club=club)


def list_for(user, club, limit: int = LIST_LIMIT) -> list[InboxNotification]:
    """Newest first."""
    return list(inbox(user, club)[:limit])


def mark_read(user, club, notification_id) -> bool:
    """``False`` when it isn't this user's (or doesn't exist)."""
    return inbox(user, club).filter(pk=notification_id).update(read_at=timezone.now()) > 0 or inbox(user, club).filter(pk=notification_id, read_at__isnull=False).exists()


def mark_all_read(user, club) -> int:
    return inbox(user, club).filter(read_at__isnull=True).update(read_at=timezone.now())


def clear_all(user, club) -> int:
    return inbox(user, club).delete()[0]
