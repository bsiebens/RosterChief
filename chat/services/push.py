"""Push for new messages: to the participants who have not read the chat, have not muted it and are not the author, at most one push per
(conversation, person) every 5 minutes, collapsed per conversation. Never one inbox entry per message. An urgent announcement skips the
throttle and goes out as a critical notification (it passes the person's quiet hours)."""

import datetime
import logging

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from chat.models import Conversation, Message, Participant
from notifications.models import Category, Priority
from notifications.services import notify_user

log = logging.getLogger(__name__)

THROTTLE = datetime.timedelta(minutes=5)
BODY_LIMIT = 140


def preview(message) -> str:
    """``Name: text`` (attachments get a short label)."""
    name = message.author.first_name if message.author_id else _("Chat")
    text = " ".join((message.body or "").split())
    if not text:
        kinds = {a.content_type for a in message.attachments.all()}
        text = _("sent a photo") if kinds and all(k.startswith("image/") for k in kinds) else _("sent a file")
    if len(text) > BODY_LIMIT:
        text = text[: BODY_LIMIT - 1] + "…"
    return f"{name}: {text}"


def title_for(conversation, recipient) -> str:
    if conversation.kind == Conversation.Kind.DIRECT:
        other = conversation.participants.exclude(member=recipient).select_related("member").first()
        return other.member.get_full_name() if other else _("Chat")
    return conversation.title or _("Chat")


def notify_message(message) -> int:
    """Push ``message`` to whoever should hear about it. Returns the number of pushes queued."""
    conversation = message.conversation
    if conversation.archived or message.deleted_at is not None:
        return 0
    now = timezone.now()
    urgent = message.urgent
    announcement = conversation.mode == Conversation.Mode.ANNOUNCEMENT
    category = Category.ANNOUNCEMENT if announcement else Category.CHAT
    body = preview(message)
    club = _club()
    sent = 0
    recipients = (
        Participant.objects.filter(conversation=conversation, left_at__isnull=True, muted=False, last_read_seq__lt=message.seq, member__user__isnull=False)
        .exclude(member_id=message.author_id)
        .select_related("member__user")
    )
    for p in recipients:
        if not urgent:
            claimed = Participant.objects.filter(pk=p.pk).filter(Q(last_push_at__isnull=True) | Q(last_push_at__lt=now - THROTTLE)).update(last_push_at=now)
            if not claimed:
                continue
        else:
            Participant.objects.filter(pk=p.pk).update(last_push_at=now)
        try:
            notify_user(
                p.member.user,
                title=title_for(conversation, p.member),
                body=body,
                category=category,
                priority=Priority.CRITICAL if urgent else Priority.NORMAL,
                club=club,
                data={"collapse_id": f"chat:{conversation.pk}", "href": f"/chat/{conversation.pk}", "conversation_id": str(conversation.pk), "message_id": str(message.pk)},
            )
            sent += 1
        except Exception:
            log.exception("chat.push_failed")
    return sent


def _club():
    """The club of the current schema (a task runs inside ``schema_context``, whose tenant is only a stand-in)."""
    from django.db import connection

    from club.models import Club

    return Club.objects.filter(schema_name=connection.schema_name).first()


def push_message_id(message_id) -> int:
    message = Message.objects.select_related("conversation", "author").filter(pk=message_id).first()
    return notify_message(message) if message else 0
