"""Housekeeping: messages older than the club's retention (``ChatSettings.retention_months``, default 24) are deleted with their files.
0 months keeps messages forever. A message with an unhandled report is kept until a moderator has handled it. Uploads that never became part of a message go after a day."""

import datetime

from django.utils import timezone

from chat.models import ChatSettings, Conversation, Message, MessageAttachment, MessageReport

DAYS_PER_MONTH = 30.44


def cutoff(months=None, *, now=None) -> datetime.datetime | None:
    """The moment before which messages go; ``None`` when messages are kept forever (0 months)."""
    months = ChatSettings.current().retention_months if months is None else months
    if not months:
        return None
    return (now or timezone.now()) - datetime.timedelta(days=round(months * DAYS_PER_MONTH))


def apply(*, now=None) -> dict:
    now = now or timezone.now()
    limit = cutoff(now=now)
    messages = conversations = 0
    if limit is not None:
        old = Message.objects.filter(created__lt=limit).exclude(pk__in=MessageReport.objects.filter(handled_at__isnull=True).values("message_id"))
        for attachment in MessageAttachment.objects.filter(message__in=old):
            attachment.file.delete(save=False)
        messages = old.count()
        old.delete()
    stale = MessageAttachment.objects.filter(message__isnull=True, created__lt=now - datetime.timedelta(days=1))
    for attachment in stale:
        attachment.file.delete(save=False)
    stale.delete()
    if limit is not None:
        empty = Conversation.objects.filter(messages__isnull=True, created__lt=limit, rules__isnull=True)
        conversations = empty.count()
        empty.delete()
    return {"messages": messages, "conversations": conversations}
