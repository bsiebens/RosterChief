"""``/chat``: conversations between club members (direct, group and team chats; discussion or announcement mode).

Open to every signed-in person with a member record (players and parents too, so these endpoints skip the management-app door); the
``chat`` feature gate answers 404 when the club does not have it. Polling: ``GET /conversations`` for the list and
``GET /conversations/{id}/messages?after=<seq>`` for new messages; neither changes read state, ``POST .../read`` does."""

import datetime
import uuid
from typing import Literal

from django.db.models import Q
from django.utils import timezone
from ninja import File, Router, Schema, UploadedFile
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from chat.models import Conversation
from chat.services import access, conversations, messages, moderation, people, present
from members.models import Member
from members.services import access as member_access
from members.services.errors import NotAllowed

router = Router(auth=[TokenAuth(door=False), CookieAuth(door=False)])


def gate(request):
    """Feature gate (404), then the member record (403). Returns ``(user, me)``."""
    from django_tenants.utils import get_public_schema_name

    if request.tenant.schema_name == get_public_schema_name() or not request.features.has("chat"):
        raise HttpError(404, "Not found.")
    return request.user, access.viewer_member(request.user)


def gate_moderator(request):
    user, me = gate(request)
    if not member_access.can_moderate_chat(user):
        raise NotAllowed("You may not moderate chat.")
    return user, me


def load(me, conversation_id):
    """``(conversation, my participant or None)`` for a chat I take part in or read along in as a parent; 404 otherwise."""
    conversation = Conversation.objects.select_related("team").filter(pk=conversation_id).first()
    mine = access.own_participant(me, conversation) if conversation else None
    if conversation is None or (mine is None and not access.parent_view(me, conversation)):
        raise HttpError(404, "Not found.")
    return conversation, mine


class Include(Schema):
    staff: bool = True
    players: bool = True
    parents: bool = False


class ConversationIn(Schema):
    kind: Literal["team", "group", "direct"]
    mode: Literal["discussion", "announcement"] = "discussion"
    title: str = ""
    member_ids: list[uuid.UUID] = []
    team_id: uuid.UUID | None = None
    include: Include | None = None


class ConversationPatch(Schema):
    title: str | None = None
    mode: Literal["discussion", "announcement"] | None = None
    muted: bool | None = None
    archived: bool | None = None


class MessageIn(Schema):
    body: str = ""
    client_key: str
    attachment_ids: list[uuid.UUID] = []
    urgent: bool = False


class ReadIn(Schema):
    seq: int


class MembersIn(Schema):
    member_ids: list[uuid.UUID]


class ReactionIn(Schema):
    emoji: str


class ReportIn(Schema):
    reason: str


# --- conversations ----------------------------------------------------------------------------------------------------------


@router.get("/conversations", response={200: dict}, summary="My conversations, newest activity first", operation_id="chat_conversations")
def list_conversations(request):
    _user, me = gate(request)
    qs = access.visible_conversations(me).select_related("team").order_by("-last_message_at", "-created")
    return {"results": present.conversations_out(qs, me), "unread_total": present.unread_total(me)}


@router.post("/conversations", response={200: dict, 201: dict}, summary="Start a chat", operation_id="chat_conversation_create")
def create_conversation(request, payload: ConversationIn):
    user, me = gate(request)
    conversation, created = conversations.create(
        user, me, kind=payload.kind, mode=payload.mode, title=payload.title, member_ids=payload.member_ids, team_id=payload.team_id, include=payload.include.dict() if payload.include else None
    )
    return (201 if created else 200), present.conversation_out(conversation, me)


@router.get("/conversations/{conversation_id}", response=dict, summary="One conversation with its participants", operation_id="chat_conversation")
def get_conversation(request, conversation_id: uuid.UUID):
    _user, me = gate(request)
    conversation, _mine = load(me, conversation_id)
    return {"conversation": present.conversation_out(conversation, me), "participants": present.participants_out(conversation)}


@router.patch("/conversations/{conversation_id}", response=dict, summary="Rename, change mode, archive (admins) or mute (anyone, for themselves)", operation_id="chat_conversation_update")
def patch_conversation(request, conversation_id: uuid.UUID, payload: ConversationPatch):
    user, me = gate(request)
    conversation, _mine = load(me, conversation_id)
    data = payload.dict(exclude_unset=True)
    conversation = conversations.update(user, me, conversation, **data)
    return present.conversation_out(conversation, me)


@router.post("/conversations/{conversation_id}/participants", response=dict, summary="Add people (chat admins)", operation_id="chat_participants_add")
def add_participants(request, conversation_id: uuid.UUID, payload: MembersIn):
    _user, me = gate(request)
    conversation, _mine = load(me, conversation_id)
    conversations.add_participants(me, conversation, payload.member_ids)
    return {"participants": present.participants_out(conversation)}


@router.delete("/conversations/{conversation_id}/participants/{member_id}", response={204: None}, summary="Remove somebody (chat admins)", operation_id="chat_participants_remove")
def remove_participant(request, conversation_id: uuid.UUID, member_id: uuid.UUID):
    _user, me = gate(request)
    conversation, _mine = load(me, conversation_id)
    conversations.remove_participant(me, conversation, member_id)
    return 204, None


# --- messages ---------------------------------------------------------------------------------------------------------------


@router.get("/conversations/{conversation_id}/messages", response=dict, summary="Messages, ascending by seq (poll with after=<seq>)", operation_id="chat_messages")
def list_messages(request, conversation_id: uuid.UUID, after: int | None = None, before: int | None = None, limit: int = 50, changed_after: datetime.datetime | None = None):
    """With ``changed_after`` (the ``server_time`` of the previous poll) the answer also has ``changed``: messages up to ``after`` whose
    reactions or deletion changed since then, to replace in the client's list. ``server_time`` is always returned."""
    _user, me = gate(request)
    conversation, _mine = load(me, conversation_id)
    now = timezone.now()
    rows, more = messages.history(conversation, after=after, before=before, limit=limit)
    out = {"messages": present.messages_out(rows, me, conversation), "conversation": present.conversation_out(conversation, me), "has_more_before": more, "server_time": now}
    if changed_after is not None:
        out["changed"] = present.messages_out(messages.changed_since(conversation, changed_after, up_to=after), me, conversation)
    return out


@router.post("/conversations/{conversation_id}/messages", response={200: dict, 201: dict}, summary="Post a message (idempotent on client_key)", operation_id="chat_message_send")
def send_message(request, conversation_id: uuid.UUID, payload: MessageIn):
    _user, me = gate(request)
    conversation, mine = load(me, conversation_id)
    if mine is None:
        raise NotAllowed("Parents can read along but not post.")
    message, created = messages.send(me, conversation, body=payload.body, client_key=payload.client_key, attachment_ids=payload.attachment_ids, urgent=payload.urgent)
    return (201 if created else 200), present.message_out(message, me, conversation)


@router.post("/conversations/{conversation_id}/attachments", response={201: dict}, summary="Upload an image or PDF to attach to a message", operation_id="chat_attachment_upload")
def upload_attachment(request, conversation_id: uuid.UUID, file: File[UploadedFile]):
    _user, me = gate(request)
    conversation, _mine = load(me, conversation_id)
    return 201, present.attachment_out(messages.store_attachment(me, conversation, file))


@router.post("/conversations/{conversation_id}/read", response=dict, summary="Mark messages up to seq as read", operation_id="chat_read")
def read(request, conversation_id: uuid.UUID, payload: ReadIn):
    _user, me = gate(request)
    conversation, _mine = load(me, conversation_id)
    messages.mark_read(me, conversation, payload.seq)
    return {"unread_total": present.unread_total(me)}


@router.delete("/messages/{message_id}", response={204: None}, summary="Delete a message (author, chat admin or moderator)", operation_id="chat_message_delete")
def delete_message(request, message_id: uuid.UUID):
    user, me = gate(request)
    messages.delete(me, user, message_id)
    return 204, None


@router.post("/messages/{message_id}/reactions", response=dict, summary="Toggle my reaction", operation_id="chat_reaction_toggle")
def toggle_reaction(request, message_id: uuid.UUID, payload: ReactionIn):
    user, me = gate(request)
    message = messages.toggle_reaction(me, user, message_id, payload.emoji)
    return {"reactions": present.message_out(message, me, message.conversation)["reactions"]}


@router.post("/messages/{message_id}/report", response={204: None}, summary="Report a message to the moderators", operation_id="chat_message_report")
def report_message(request, message_id: uuid.UUID, payload: ReportIn):
    user, me = gate(request)
    messages.report(me, user, message_id, payload.reason)
    return 204, None


# --- lookups ----------------------------------------------------------------------------------------------------------------


@router.get("/people", response=dict, summary="People I can start a chat with", operation_id="chat_people")
def find_people(request, q: str = "", limit: int = 20):
    _user, me = gate(request)
    qs = Member.objects.filter(user__isnull=False, user__is_active=True).exclude(pk=me.pk)
    for word in q.split()[:5]:
        qs = qs.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word))
    found = list(qs.order_by("last_name", "first_name")[: max(1, min(limit, 50))])
    resolved = people.resolve(found)
    return {"results": [resolved[m.pk] for m in found]}


@router.get("/teams", response=dict, summary="Teams I can start a team chat for", operation_id="chat_teams")
def my_teams(request):
    from teams.models import Team

    user, _me = gate(request)
    qs = Team.objects.all() if member_access.can_moderate_chat(user) else Team.objects.filter(pk__in=member_access.staffed_team_ids(user))
    return {"results": [{"id": t.pk, "name": t.name} for t in qs.order_by("ordering", "name")]}


# --- moderation (chat.moderate) ---------------------------------------------------------------------------------------------


def _report_out(r, me) -> dict:
    conversation = r.message.conversation
    return {
        "id": r.pk,
        "message": present.message_out(r.message, me, conversation),
        "conversation_id": conversation.pk,
        "conversation_title": conversation.title or conversation.kind,
        "reporter": people.resolve([r.reporter])[r.reporter_id] if r.reporter_id else people.former_person(),
        "reason": r.reason,
        "created": r.created,
        "handled": r.handled_at is not None,
    }


@router.get("/moderation/reports", response=dict, summary="Reported messages", operation_id="chat_moderation_reports")
def moderation_reports(request):
    user, me = gate_moderator(request)
    return {"results": [_report_out(r, me) for r in moderation.reports(user)[:200]]}


@router.post("/moderation/reports/{report_id}/handle", response=dict, summary="Mark a report as handled", operation_id="chat_moderation_report_handle")
def moderation_handle(request, report_id: uuid.UUID):
    user, me = gate_moderator(request)
    return _report_out(moderation.handle_report(user, me, report_id), me)


@router.get("/moderation/conversations", response=dict, summary="Conversations (minors=1: only the ones with minors in them)", operation_id="chat_moderation_conversations")
def moderation_conversations(request, minors: int = 0):
    user, me = gate_moderator(request)
    qs = moderation.conversations(user, minors_only=bool(minors)).select_related("team")[:200]
    return {"results": present.conversations_out(qs, me, moderator_view=True)}


@router.get("/moderation/conversations/{conversation_id}/messages", response=dict, summary="Read any chat (audit-logged)", operation_id="chat_moderation_messages")
def moderation_messages(request, conversation_id: uuid.UUID, after: int | None = None, before: int | None = None, limit: int = 50):
    user, me = gate_moderator(request)
    conversation = moderation.get_conversation(user, conversation_id)
    moderation.audit(user, "chat.moderation_viewed", conversation)
    rows, more = messages.history(conversation, after=after, before=before, limit=limit)
    return {
        "messages": present.messages_out(rows, me, conversation),
        "conversation": present.conversation_out(conversation, me, moderator_view=True),
        "participants": present.participants_out(conversation),
        "has_more_before": more,
    }
