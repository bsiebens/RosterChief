"""Fan a notification out to members -- and, always, their parents/guardians.

Built for news publishing first (see news.tasks.notify_news_published), but
recipient resolution and delivery live here, generically, so any future
"notify these members about X" need reuses this instead of writing its own
version of the same guardian-fallback logic club.services.invoicing.recipient_for
already established for dues invoices.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.functional import Promise

from rosterchief.mail import send_message

from . import delivery_log
from .dispatch import schedule_notifications
from .models import Notification

#: A native install that has checked in within this many days counts as the account's phone
#: (see users_with_native_push). Older installs stop counting, in case the token went stale
#: without the push service ever reporting it dead.
NATIVE_DEVICE_ACTIVE_DAYS = 60


def users_with_native_push(user_ids) -> set:
    """Of ``user_ids``, the accounts that currently receive native (iOS/Android) push: an
    enabled device that has been seen recently."""
    from appapi.models import PushDevice  # deferred: appapi imports this module

    user_ids = {user_id for user_id in user_ids if user_id}
    if not user_ids:
        return set()
    cutoff = timezone.now() - timedelta(days=NATIVE_DEVICE_ACTIVE_DAYS)
    return set(PushDevice.objects.filter(user_id__in=user_ids, enabled=True, last_seen__gte=cutoff).values_list("user_id", flat=True))


def recipient_user_ids(notification: Notification) -> set:
    """The accounts a notification is for: the member's own login plus every parent/guardian's
    in that club -- the same people recipient_emails() resolves, as accounts. Both push
    channels deliver to these."""
    member = notification.member
    user_ids = {member.user_id} if member.user_id else set()
    user_ids |= set(member.guardians(notification.club).exclude(user__isnull=True).values_list("user_id", flat=True))
    return user_ids


def recipient_emails(member, club, *, honor_preferences: bool = False) -> list[str]:
    """The member's own email if they hold a login, plus every parent/guardian's,
    always -- a child with their own account doesn't opt their parents out of
    also being told. De-duplicated, order-stable. Empty means nobody reachable
    at all.

    With ``honor_preferences``, an address that belongs to an account set to "not when the app
    can notify me" (User.email_notifications) is left out while that account has an active
    native install. Addresses with no account behind them (a guardian without a login) are
    never dropped."""
    candidates = []  # (email, account or None)
    if member.user_id and member.contact_email:
        candidates.append((member.contact_email, member.user))
    for guardian in member.guardians(club).select_related("user").order_by("last_name", "first_name"):
        if guardian.contact_email:
            candidates.append((guardian.contact_email, guardian.user))

    if honor_preferences:
        opted_out = {user.pk for _email, user in candidates if user is not None and user.email_notifications == user.EmailNotifications.UNLESS_APP}
        if opted_out:
            quiet = users_with_native_push(opted_out)
            candidates = [(email, user) for email, user in candidates if user is None or user.pk not in quiet]

    seen, emails = set(), []
    for email, _user in candidates:
        if email not in seen:
            seen.add(email)
            emails.append(email)
    return emails


def _render_email(notification: Notification):
    context = {"club": notification.club, "notification": notification}
    subject = " ".join(render_to_string("notifications/email/notification_subject.txt", context).split())
    text_body = render_to_string("notifications/email/notification.txt", context).strip() + "\n"
    html_body = render_to_string("notifications/email/notification.html", context)
    return subject, text_body, html_body


def _build_message(subject, text_body, html_body, email, attachments=None):
    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [email])
    message.attach_alternative(html_body, "text/html")
    for filename, content, mimetype in attachments or []:
        message.attach(filename, content, mimetype)
    return message


def _send_email(notification: Notification, emails: list[str], attachments=None) -> list[str]:
    """Send to each address and log one delivery row per address; returns the addresses that went out.
    A failed plain mail is logged for retry; one with attachments can't be re-sent later (the
    attachments aren't stored), so it is logged as failed."""
    subject, text_body, html_body = _render_email(notification)
    users = {}
    try:
        users = {address.lower(): pk for address, pk in get_user_model().objects.filter(email__in=emails).values_list("email", "pk")}
    except Exception:
        pass

    delivered = []
    for email in emails:
        message = _build_message(subject, text_body, html_body, email, attachments)
        user = None
        if users.get(email.lower()):
            user = get_user_model()(pk=users[email.lower()])
        try:
            send_message(message, fail_silently=False)
        except OSError as error:
            # Never fatal -- the Notification row (and whichever other addresses
            # in this same batch do go out) stands either way, same reasoning as
            # every other branded send in this app.
            if attachments:
                delivery_log.record(club=notification.club, channel="email", target=email, outcome="failed", notification=notification, user=user, error=error)
            else:
                delivery_log.record(club=notification.club, channel="email", target=email, outcome="retry", notification=notification, user=user, error=error, payload={"notification": str(notification.pk), "email": email})
            continue
        delivered.append(email)
        delivery_log.record(club=notification.club, channel="email", target=email, outcome="sent", notification=notification, user=user)
    return delivered


def resend_email(notification: Notification, email: str) -> None:
    """Send the notification mail to one address again (the email channel's retry). Raises OSError."""
    subject, text_body, html_body = _render_email(notification)
    send_message(_build_message(subject, text_body, html_body, email), fail_silently=False)


def _text_in(value, language: str) -> str:
    """``value`` rendered in ``language``: a callable is called, a lazy string evaluated, a plain
    string (user-written content) returned as is -- all inside that language's override."""
    with translation.override(language):
        return str(value() if callable(value) and not isinstance(value, Promise) else value)


def localized_texts(title, body) -> dict:
    """``{language: {"title", "body"}}`` for every language the API serves."""
    return {code: {"title": _text_in(title, code), "body": _text_in(body, code)} for code, _name in settings.LANGUAGES}


def send_notification_emails(notifications, *, attachments=None) -> None:
    """Email each notification to everyone recipient_emails() resolves for its member, and record
    on the row what was sent. Skips nobody-reachable rows. The EmailChannel's work."""
    sent = []
    for notification in notifications:
        # An attachment (an invoice) is a document, not a nudge: that mail is never skipped.
        emails = recipient_emails(notification.member, notification.club, honor_preferences=not attachments)
        if emails:
            delivered = _send_email(notification, emails, attachments=attachments)
            if delivered:
                notification.sent_at = timezone.now()
                notification.sent_to_emails = delivered
                notification.modified = timezone.now()
                sent.append(notification)
    if sent:
        Notification.objects.bulk_update(sent, ["sent_at", "sent_to_emails", "modified"])


def notify_members(members, *, club, title: str, body: str, source=None, target_type: str = "", send_email: bool = True, attachments=None) -> list[Notification]:
    """One Notification per member. Emailed to everyone recipient_emails()
    resolves for them, unless send_email is False -- e.g. a staff review
    queue (see news.services.notify_editors_of_pending_review), which is
    in-app only: it doesn't need every editor emailed on every submission,
    just the topbar/dashboard entry. Always creates the row, even when
    nobody was reachable or emailing was skipped -- that's still true
    history for the in-app feed, not a failure to silently drop.

    ``title`` and ``body`` may be plain strings (user-written content, same in every language),
    lazy translated strings, or zero-argument callables returning a string -- the latter two are
    rendered once per language in settings.LANGUAGES and stored in ``Notification.translations``
    for the app API. The stored ``title``/``body`` (and so the email) are the English rendering.

    ``target_type`` names a screen of the native app to open for a notification that has no
    ``source`` object to point at (e.g. ``"calendar"``); ignored when there is a source.

    ``attachments``, when given, is a list of ``(filename, content, mimetype)``
    tuples attached to every email this batch sends -- e.g.
    shop.services.notifications.dispatch_order_placed_notification's invoice
    PDF. Nobody else passes this today, so it costs existing callers nothing.

    One bulk_create for every row instead of one INSERT per member, and one
    dispatch for the whole batch (notifications.dispatch: native push, web push and email,
    after commit and off the request thread) -- a channel resolves every member's devices
    in a few queries, not one row at a time."""
    members = list(members)
    translations = localized_texts(title, body)
    english = translations["en"]
    notifications = [Notification(club=club, member=member, title=english["title"], body=english["body"], translations=translations, source=source, target_type=target_type) for member in members]
    Notification.objects.bulk_create(notifications)

    # An attachment (an invoice) is a document, not a nudge: mailed right here, never
    # skipped for an account's preference. Plain notification mail goes out with every
    # other channel, after commit, off the request thread (notifications.dispatch).
    if send_email and attachments:
        send_notification_emails(notifications, attachments=attachments)
    schedule_notifications(notifications, email=send_email and not attachments)
    return notifications


# -- the in-app inbox -------------------------------------------------------------------
# One set of queries behind the member app's Notifications screen and unread badge
# (mobile) and the app API's /notifications endpoints. Scoped to a club and a list of
# managed people (members.services.family.managed_people): notifications are about a
# member, and a parent reads their children's too.


def inbox_for(club, people):
    """Every notification for ``people`` in ``club``, newest first."""
    return Notification.objects.filter(club=club, member__in=list(people)).order_by("-created")


def unread_count(club, people) -> int:
    people = list(people)
    if not people:
        return 0
    return inbox_for(club, people).filter(read_at__isnull=True).count()


def mark_read(club, people, notification_id) -> Notification | None:
    """Mark one notification read; ``None`` when it isn't one of ``people``'s (or the id
    is malformed). Already-read ones keep their original read time."""
    try:
        notification = inbox_for(club, people).filter(pk=notification_id).first()
    except ValueError, ValidationError:
        return None
    if notification is not None and notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at", "modified"])
    return notification


def mark_all_read(club, people) -> int:
    return inbox_for(club, people).filter(read_at__isnull=True).update(read_at=timezone.now())


def clear_all(club, people) -> int:
    """A hard delete, like clearing a phone's notification centre -- not a read flag."""
    count, _deleted = inbox_for(club, people).delete()
    return count
