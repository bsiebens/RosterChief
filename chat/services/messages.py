"""Sending, reading and moderating messages."""

import logging

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from chat.models import REACTION_EMOJI, Conversation, Message, MessageAttachment, MessageReaction, MessageReport
from chat.services import access
from members.services import access as member_access
from members.services.errors import Invalid, NotAllowed

log = logging.getLogger(__name__)

MAX_BODY = 4000
MAX_ATTACHMENT = 10 * 1024 * 1024
MAX_PER_MESSAGE = 10
MAX_PAGE = 100


def changed_since(conversation, changed_after, *, up_to=None):
    """Messages whose reactions or deletion changed after ``changed_after`` (older than the polled ``after`` cursor, so never also in ``messages``)."""
    qs = conversation.messages.select_related("author").filter(modified__gt=changed_after)
    if up_to is not None:
        qs = qs.filter(seq__lte=up_to)
    return list(qs.order_by("seq")[:MAX_PAGE])


def history(conversation, *, after=None, before=None, limit=50):
    """``(messages ascending by seq, has_more_before)``. No cursor: the latest ``limit``. Never touches read state."""
    limit = max(1, min(int(limit or 50), MAX_PAGE))
    qs = conversation.messages.select_related("author")
    if after is not None:
        rows = list(qs.filter(seq__gt=after).order_by("seq")[:limit])
    else:
        if before is not None:
            qs = qs.filter(seq__lt=before)
        rows = list(qs.order_by("-seq")[:limit])[::-1]
    more = bool(rows) and conversation.messages.filter(seq__lt=rows[0].seq).exists()
    return rows, more


@transaction.atomic
def send(me, conversation, *, body, client_key, attachment_ids=(), urgent=False) -> tuple[Message, bool]:
    """``(message, created)``. A repeated ``client_key`` returns the first message (created False)."""
    client_key = (client_key or "").strip()
    if not client_key or len(client_key) > 64:
        raise Invalid("client_key is required (max 64 characters).", errors={"client_key": ["Required."]})
    conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
    existing = Message.objects.filter(conversation=conversation, author=me, client_key=client_key).first()
    if existing is not None:
        return existing, False
    mine = access.own_participant(me, conversation)
    if mine is None:
        raise NotAllowed("You are not in this chat.")
    if conversation.archived:
        raise Invalid("This chat is archived.", code="archived")
    if not access.can_post(conversation, mine):
        raise NotAllowed("Only chat admins can post in an announcement chat.")
    body = (body or "").strip()
    if len(body) > MAX_BODY:
        raise Invalid("The message is too long.", errors={"body": ["The message is too long."]})
    attachment_ids = list(dict.fromkeys(attachment_ids or []))
    if len(attachment_ids) > MAX_PER_MESSAGE:
        raise Invalid("Too many attachments.", errors={"attachment_ids": ["Too many attachments."]})
    attachments = list(MessageAttachment.objects.filter(pk__in=attachment_ids, conversation=conversation, uploader=me, message__isnull=True))
    if len(attachments) != len(attachment_ids):
        raise Invalid("Unknown attachment.", errors={"attachment_ids": ["Unknown attachment."]})
    if not body and not attachments:
        raise Invalid("Write something or attach a file.", errors={"body": ["Write something or attach a file."]})
    if urgent and not (conversation.mode == Conversation.Mode.ANNOUNCEMENT and mine.is_admin):
        raise Invalid("Only chat admins can mark an announcement as urgent.", errors={"urgent": ["Only chat admins can mark an announcement as urgent."]})
    now = timezone.now()
    seq = conversation.last_seq + 1
    message = Message.objects.create(conversation=conversation, seq=seq, author=me, body=body, urgent=bool(urgent), client_key=client_key)
    MessageAttachment.objects.filter(pk__in=[a.pk for a in attachments]).update(message=message)
    Conversation.objects.filter(pk=conversation.pk).update(last_seq=seq, last_message_at=now, modified=now)
    mine.last_read_seq = seq
    mine.save(update_fields=["last_read_seq", "modified"])
    transaction.on_commit(lambda: _queue_push(message.pk))
    return message, True


def _queue_push(message_id) -> None:
    """After commit; a broker hiccup never fails the send."""
    try:
        from chat.tasks import push_message

        push_message.delay(str(message_id))
    except Exception:
        log.exception("chat.push_queue_failed")


def store_attachment(me, conversation, upload) -> MessageAttachment:
    mine = access.own_participant(me, conversation)
    if mine is None or not access.can_post(conversation, mine):
        raise NotAllowed("You cannot post in this chat.")
    name = (upload.name or "file")[:255]
    kind = _sniff(upload)
    if upload.size > MAX_ATTACHMENT:
        raise Invalid("The file is larger than 10 MB.", errors={"file": ["The file is larger than 10 MB."]}, code="too_large")
    if kind is None:
        raise Invalid("Only images and PDF files can be attached.", errors={"file": ["Only images and PDF files can be attached."]}, code="unsupported")
    upload.seek(0)
    return MessageAttachment.objects.create(conversation=conversation, uploader=me, file=upload, name=name, content_type=kind, size=upload.size)


def _sniff(upload) -> str | None:
    """The real type from the file's bytes: a PDF or a raster image (SVG is refused: it can carry scripts)."""
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    if head.startswith(b"%PDF"):
        return "application/pdf"
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(upload) as img:
            img.verify()
            fmt = (img.format or "").lower()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return None
    finally:
        upload.seek(0)
    return {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp", "avif": "image/avif"}.get(fmt)


def mark_read(me, conversation, seq) -> None:
    mine = access.own_participant(me, conversation)
    if mine is None:
        return
    seq = max(0, min(int(seq), conversation.last_seq))
    if seq > mine.last_read_seq:
        mine.last_read_seq = seq
        mine.save(update_fields=["last_read_seq", "modified"])


def get_message(me, user, message_id, *, allow_moderator=False) -> tuple[Message, object]:
    """``(message, participant or None)``; ``NotAllowed`` unless ``me`` takes part, watches as a parent, or (``allow_moderator``) moderates."""
    message = Message.objects.select_related("conversation", "conversation__team", "author").filter(pk=message_id).first()
    if message is None:
        raise HttpError(404, "Not found.")
    mine = access.own_participant(me, message.conversation)
    if mine is None and not access.parent_view(me, message.conversation) and not (allow_moderator and member_access.can_moderate_chat(user)):
        raise HttpError(404, "Not found.")
    return message, mine


def delete(me, user, message_id) -> None:
    from chat.services import moderation

    message, mine = get_message(me, user, message_id, allow_moderator=True)
    is_author = message.author_id == me.pk
    is_chat_admin = bool(mine and mine.is_admin)
    is_moderator = member_access.can_moderate_chat(user)
    if not (is_author or is_chat_admin or is_moderator):
        raise NotAllowed("You cannot delete this message.")
    if message.deleted_at is None:
        message.deleted_at, message.deleted_by = timezone.now(), me
        message.save(update_fields=["deleted_at", "deleted_by", "modified"])
        for a in message.attachments.all():
            a.file.delete(save=False)
        message.attachments.all().delete()
    if is_moderator and not is_author and not is_chat_admin:
        moderation.audit(user, "chat.message_deleted", message.conversation, extra={"message": str(message.pk)})


def toggle_reaction(me, user, message_id, emoji) -> Message:
    message, mine = get_message(me, user, message_id)
    if mine is None:
        raise NotAllowed("Parents can read along but not react.")
    if emoji not in REACTION_EMOJI:
        raise Invalid("This reaction is not available.", errors={"emoji": ["This reaction is not available."]})
    if message.deleted_at is not None or message.conversation.archived:
        raise Invalid("You cannot react to this message.")
    deleted, _ = MessageReaction.objects.filter(message=message, member=me, emoji=emoji).delete()
    if not deleted:
        MessageReaction.objects.create(message=message, member=me, emoji=emoji)
    Message.objects.filter(pk=message.pk).update(modified=timezone.now())  # shows up in ``changed`` of the polls
    return message


def report(me, user, message_id, reason) -> MessageReport:
    message, _mine = get_message(me, user, message_id)
    reason = (reason or "").strip()
    if not reason:
        raise Invalid("Say what is wrong.", errors={"reason": ["Say what is wrong."]})
    existing = MessageReport.objects.filter(message=message, reporter=me, handled_at__isnull=True).first()
    if existing:
        return existing
    return MessageReport.objects.create(message=message, reporter=me, reason=reason[:1000])
