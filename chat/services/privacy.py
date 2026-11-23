"""Erasure: when a member is deleted, what they wrote stays out of everybody else's way but not under their name."""

from django.db.models import Q
from django.utils import timezone

from chat.models import Message, MessageAttachment


def anonymise_member(member) -> int:
    """Called before a member is deleted. Their messages lose the author and the text (they show as deleted messages from a former
    member), their attachments' files are removed. Participations, reactions and reports go with the member row itself. Returns the number
    of messages anonymised."""
    for attachment in MessageAttachment.objects.filter(Q(message__author=member) | Q(uploader=member)):
        attachment.file.delete(save=False)
        attachment.delete()
    return Message.objects.filter(author=member).update(author=None, body="", deleted_at=timezone.now(), deleted_by=None, client_key=None)
