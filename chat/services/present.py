"""The JSON documents of the chat API (see the contract in ``api/routers/chat.py``)."""

from collections import defaultdict

from django.db.models import Count, F, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.urls import reverse

from chat.models import REACTION_EMOJI, AudienceRule, Conversation, Message, MessageAttachment, MessageReaction, Participant
from chat.services import access, people
from members.models import Member


def attachment_out(a: MessageAttachment) -> dict:
    return {"id": a.pk, "name": a.name, "url": reverse("chat:attachment", args=[a.pk]), "content_type": a.content_type, "size": a.size}


def reactions_out(reactions, member) -> list:
    """``reactions``: MessageReaction rows of one message; fixed emoji order."""
    counts, mine = defaultdict(int), set()
    for r in reactions:
        counts[r.emoji] += 1
        if r.member_id == member.pk:
            mine.add(r.emoji)
    return [{"emoji": e, "count": counts[e], "mine": e in mine} for e in REACTION_EMOJI if counts[e]]


def messages_out(messages, member, conversation) -> list:
    """Messages (any order) as documents, in the given order."""
    messages = list(messages)
    ids = [m.pk for m in messages]
    authors = {m.author_id: m.author for m in messages if m.author_id}
    persons = people.resolve(authors.values(), team=conversation.team)
    attachments, reactions = defaultdict(list), defaultdict(list)
    for a in MessageAttachment.objects.filter(message_id__in=ids):
        attachments[a.message_id].append(a)
    for r in MessageReaction.objects.filter(message_id__in=ids):
        reactions[r.message_id].append(r)
    out = []
    for m in messages:
        mine = m.author_id == member.pk
        out.append(
            {
                "id": m.pk,
                "seq": m.seq,
                "author": persons.get(m.author_id) or people.former_person(),
                "body": "" if m.deleted else m.body,
                "attachments": [] if m.deleted else [attachment_out(a) for a in attachments[m.pk]],
                "created": m.created,
                "edited": m.edited_at,
                "deleted": m.deleted,
                "mine": mine,
                "urgent": m.urgent,
                "reactions": [] if m.deleted else reactions_out(reactions[m.pk], member),
                "client_key": m.client_key if mine else None,
            }
        )
    return out


def message_out(message, member, conversation) -> dict:
    return messages_out([message], member, conversation)[0]


def conversations_out(conversations, member, *, moderator_view: bool = False) -> list:
    """Conversations as the viewer sees them. ``moderator_view``: the viewer is a moderator looking in (no read state, no posting)."""
    conversations = list(conversations)
    ids = [c.pk for c in conversations]
    unread = (
        Message.objects.filter(conversation=OuterRef("conversation"), seq__gt=OuterRef("last_read_seq"), deleted_at__isnull=True)
        .exclude(author=member)
        .order_by()
        .values("conversation")
        .annotate(n=Count("pk"))
        .values("n")
    )
    own = {p.conversation_id: p for p in access.active(Participant.objects.filter(member=member, conversation_id__in=ids)).annotate(unread=Coalesce(Subquery(unread, output_field=IntegerField()), 0))}
    counts = dict(access.active(Participant.objects.filter(conversation_id__in=ids)).values_list("conversation_id").annotate(n=Count("pk")))
    last = {m.conversation_id: m for m in Message.objects.filter(conversation_id__in=ids, seq=F("conversation__last_seq")).select_related("author", "conversation__team")}
    direct_ids = [c.pk for c in conversations if c.kind == Conversation.Kind.DIRECT]
    others = defaultdict(list)
    for p in access.active(Participant.objects.filter(conversation_id__in=direct_ids)).select_related("member"):
        others[p.conversation_id].append(p.member)
    last_docs = {}
    for c in conversations:
        if c.pk in last:
            last_docs[c.pk] = message_out(last[c.pk], member, c)
    club_wide = set(AudienceRule.objects.filter(conversation_id__in=ids, conversation__kind=Conversation.Kind.GROUP).values_list("conversation_id", flat=True))
    out = []
    for c in conversations:
        mine = own.get(c.pk)
        title = c.title
        if c.kind == Conversation.Kind.DIRECT:
            members = others.get(c.pk, [])
            title = " & ".join(m.get_full_name() for m in members) if mine is None else next((m.get_full_name() for m in members if m.pk != member.pk), c.title or people.FORMER_MEMBER)
        out.append(
            {
                "id": c.pk,
                "kind": c.kind,
                "mode": c.mode,
                "title": title,
                "audience": "club" if c.pk in club_wide else None,
                "team": {"id": c.team_id, "name": c.team.name} if c.team_id else None,
                "participant_count": counts.get(c.pk, 0),
                "last_message": last_docs.get(c.pk),
                "unread": mine.unread if mine else 0,
                "muted": bool(mine and mine.muted),
                "is_admin": bool(mine and mine.is_admin),
                "can_post": access.can_post(c, mine),
                "archived": c.archived,
                "read_only_parent_view": mine is None and not moderator_view,
                "updated": c.last_message_at or c.modified,
            }
        )
    return out


def conversation_out(conversation, member, **kw) -> dict:
    return conversations_out([conversation], member, **kw)[0]


def unread_total(member) -> int:
    """Unread messages in the chats the member takes part in and has not muted."""
    unread = (
        Message.objects.filter(conversation=OuterRef("conversation"), seq__gt=OuterRef("last_read_seq"), deleted_at__isnull=True)
        .exclude(author=member)
        .order_by()
        .values("conversation")
        .annotate(n=Count("pk"))
        .values("n")
    )
    rows = access.active(Participant.objects.filter(member=member, muted=False, conversation__archived=False)).annotate(unread=Coalesce(Subquery(unread, output_field=IntegerField()), 0))
    return sum(p.unread for p in rows)


def participants_out(conversation) -> list:
    rows = list(access.active(conversation.participants.all()).select_related("member").order_by("joined_at", "member__last_name"))
    persons = people.resolve([p.member for p in rows], team=conversation.team)
    return [{"person": persons[p.member_id], "is_admin": p.is_admin, "joined_at": p.joined_at} for p in rows]


def person_out(member: Member) -> dict:
    return people.resolve([member])[member.pk]
