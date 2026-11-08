"""Telling people about forms (inbox + push, category "form").

* ``dispatch_send_notification``: queue a task that tells the send's audience (members and, for children, their
  guardians; one message per account) there is a form to fill in. A send that opens later is told by the sweep when it
  opens. A send that is public-only (no audience) tells nobody.
* ``send_form_reminders``: the periodic sweep (every club): open sends that close within the next 24-48 hours get ONE
  nudge, to the audience members who have not responded yet.

Each person gets the text in their own language (``Member.language``; empty means the club's own, Dutch). The
``notified_at`` / ``reminder_sent_at`` claims are atomic, so a dispatch and the sweep never both send."""

import datetime
import logging

from django.db.models import Q
from django.utils import timezone

from formbuilder.models import FormSend

logger = logging.getLogger(__name__)

CATEGORY = "form"
DEFAULT_LANGUAGE = "nl"
#: A reminder goes out when the send closes within this window (from now): at least 24 and at most 48 hours away.
REMINDER_WINDOW = (datetime.timedelta(hours=24), datetime.timedelta(hours=48))

TEXTS = {
    "nl": {
        "new_title": "Nieuw formulier: {form}",
        "new_body": "Vul het in vóór {deadline}.",
        "new_body_open": "Vul het in wanneer je tijd hebt.",
        "reminder_title": "Herinnering: {form}",
        "reminder_body": "Je antwoord ontbreekt nog. Het formulier sluit op {deadline}.",
    },
    "en": {
        "new_title": "New form: {form}",
        "new_body": "Please fill it in before {deadline}.",
        "new_body_open": "Please fill it in when you have a moment.",
        "reminder_title": "Reminder: {form}",
        "reminder_body": "We are still missing your response. The form closes on {deadline}.",
    },
    "fr": {
        "new_title": "Nouveau formulaire : {form}",
        "new_body": "Merci de le remplir avant le {deadline}.",
        "new_body_open": "Merci de le remplir quand vous avez un moment.",
        "reminder_title": "Rappel : {form}",
        "reminder_body": "Votre réponse manque encore. Le formulaire se ferme le {deadline}.",
    },
}


def _lang(code: str | None) -> str:
    code = (code or "").split("-")[0].lower()
    return code if code in TEXTS else DEFAULT_LANGUAGE


def _href(send: FormSend) -> str:
    """Where the notification leads: the public fill page for a public send; otherwise the fill page the member app
    will serve (there is no member-facing page yet)."""
    return f"/f/{send.public_token}" if send.is_public else f"/forms/{send.pk}/fill"


def _current_club():
    from events.services.attendance import current_club

    return current_club()


def _deadline(send: FormSend) -> str:
    return f"{timezone.localtime(send.closes_at):%d/%m/%Y %H:%M}" if send.closes_at else ""


def _recipients(members) -> list[tuple[object, object, str]]:
    """``[(user, on_behalf_of, language)]``, one row per account: the account's own language (a guardian reads it in
    theirs, not the child's)."""
    from members.models import Member
    from members.services import recipients

    rows, seen = [], set()
    for member in members.select_related("user"):
        for user, child in recipients.recipients(member):
            if user.pk not in seen:
                seen.add(user.pk)
                rows.append((user, child))
    languages = dict(Member.objects.filter(user__in=[u for u, _c in rows]).values_list("user_id", "language"))
    return [(user, child, _lang(languages.get(user.pk))) for user, child in rows]


def _deliver(send: FormSend, members, *, title_key: str, body_key: str, open_body_key: str | None = None) -> int:
    from notifications import inbox

    club = _current_club()
    if club is None:
        return 0
    told = 0
    for user, child, lang in _recipients(members):
        texts = TEXTS[lang]
        fmt = {"form": send.form.title, "deadline": _deadline(send)}
        title = texts[title_key].format(**fmt)
        body_key_now = body_key if send.closes_at or open_body_key is None else open_body_key
        body = texts[body_key_now].format(**fmt)
        if child is not None:
            title = f"{child.first_name}: {title}"
        inbox.notify(user, club, title=title, body=body, kind=inbox.Kind.INFO, href=_href(send), push=True, category=CATEGORY)
        told += 1
    return told


def _claim(send_id, now) -> FormSend | None:
    """Atomically take the right to announce: sets ``notified_at`` only if still empty, the send is on and its window has
    opened and not closed. A scheduled send is therefore left alone until it opens."""
    claimed = FormSend.objects.filter(pk=send_id, notified_at__isnull=True, is_active=True).filter(Q(opens_at__isnull=True) | Q(opens_at__lte=now)).filter(Q(closes_at__isnull=True) | Q(closes_at__gt=now)).update(notified_at=now)
    return FormSend.objects.select_related("form").get(pk=send_id) if claimed else None


def notify_now(send_id, now=None) -> int:
    """Tell the audience of ``send_id`` unless it was announced already, is not open yet / any more, or is gone. Returns the
    number of accounts told. A send with no audience (public link only) is marked as handled and tells nobody."""
    from formbuilder.services import audience

    now = now or timezone.now()
    send = _claim(send_id, now)
    if send is None:
        return 0
    try:
        members = audience.effective_members(send)
        if not members.exists():
            return 0
        return _deliver(send, members, title_key="new_title", body_key="new_body", open_body_key="new_body_open")
    except Exception:
        FormSend.objects.filter(pk=send_id).update(notified_at=None)  # let the next sweep retry
        raise


def notify_due(now=None) -> dict:
    """The announcement sweep, in the current club schema: every on, open, not yet announced send (this is what delivers a
    scheduled send once its ``opens_at`` has passed)."""
    now = now or timezone.now()
    sends = told = 0
    due = FormSend.objects.filter(notified_at__isnull=True, is_active=True).filter(Q(opens_at__isnull=True) | Q(opens_at__lte=now)).filter(Q(closes_at__isnull=True) | Q(closes_at__gt=now))
    for send_id in list(due.values_list("pk", flat=True)):
        try:
            told += notify_now(send_id, now)
            sends += 1
        except Exception:  # one broken send must not stop the others
            logger.exception("Notifying form send %s failed", send_id)
    return {"sends": sends, "told": told}


def remind_due(now=None) -> dict:
    """The reminder sweep, in the current club schema."""
    from formbuilder.services import audience

    now = now or timezone.now()
    low, high = REMINDER_WINDOW
    candidates = FormSend.objects.filter(is_active=True, reminder_sent_at__isnull=True, closes_at__gte=now + low, closes_at__lte=now + high).filter(Q(opens_at__isnull=True) | Q(opens_at__lte=now))
    sends = told = 0
    for send_id in list(candidates.values_list("pk", flat=True)):
        try:
            # Claim first (atomic): a concurrent sweep finds nothing to do. Marked even when nobody is left to nudge,
            # as the window only opens once.
            if not FormSend.objects.filter(pk=send_id, reminder_sent_at__isnull=True).update(reminder_sent_at=now):
                continue
            send = FormSend.objects.select_related("form").get(pk=send_id)
            try:
                told += _deliver(send, audience.members_not_yet_submitted(send), title_key="reminder_title", body_key="reminder_body")
            except Exception:
                FormSend.objects.filter(pk=send_id).update(reminder_sent_at=None)
                raise
            sends += 1
        except Exception:
            logger.exception("Reminding for form send %s failed", send_id)
    return {"sends": sends, "told": told}


def dispatch_send_notification(send_id) -> None:
    """Tell the audience of ``send_id`` (in a background task). A send that is not open yet is told by the sweep when it
    opens. Broker errors are logged, never raised: the sweep catches up."""
    from formbuilder import tasks

    try:
        tasks.notify_send.delay(str(send_id))
    except Exception:
        logger.exception("formbuilder.enqueue_failed notify_send")


def send_form_reminders() -> dict:
    """The periodic sweep across every active club (also the entry point the Celery task calls)."""
    from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

    sends = told = 0
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                counts = remind_due()
                sends += counts["sends"]
                told += counts["told"]
        except Exception:
            logger.exception("Form reminders failed for club %s", club.slug)
            failed.append(club.slug)
    return {"sends": sends, "told": told, "failed": failed}
