"""Who sees a conversation: its active participants, the parents of a participating child (read-only) and, for the
moderation endpoints, holders of ``chat.moderate`` (audit-logged, see ``chat.services.moderation``)."""

from django.db.models import Q

from chat.models import Conversation, Participant
from chat.services import people
from formbuilder.services.audience import managed_children
from members.models import Member
from members.services.errors import NotAllowed


def viewer_member(user) -> Member:
    member = Member.objects.filter(user=user).first() if user is not None and user.is_authenticated else None
    if member is None:
        raise NotAllowed("Chat is for members of the club.")
    return member


def active(qs):
    return qs.filter(left_at__isnull=True)


def own_participant(member, conversation) -> Participant | None:
    return active(Participant.objects.filter(conversation=conversation, member=member)).first()


def watched_child_ids(member) -> set:
    """Children (minors) of ``member`` whose chats the parent may read."""
    return {c.pk for c in managed_children(member) if people.is_minor(c)}


def parent_view(member, conversation) -> bool:
    """True when ``member`` is not in the chat but a child of theirs is (read-only)."""
    children = watched_child_ids(member)
    return bool(children) and active(Participant.objects.filter(conversation=conversation, member_id__in=children)).exists()


def can_see(member, conversation) -> bool:
    return own_participant(member, conversation) is not None or parent_view(member, conversation)


def visible_conversations(member):
    children = watched_child_ids(member)
    q = Q(participants__member=member, participants__left_at__isnull=True)
    if children:
        q |= Q(participants__member_id__in=children, participants__left_at__isnull=True)
    return Conversation.objects.filter(pk__in=Conversation.objects.filter(q).values("pk"))


def can_post(conversation, participant) -> bool:
    if participant is None or conversation.archived:
        return False
    return conversation.mode == Conversation.Mode.DISCUSSION or participant.is_admin
