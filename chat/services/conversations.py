"""Creating and changing conversations and their participants."""

from django.db import transaction
from django.utils import timezone

from chat.models import AudienceRule, Conversation, Participant
from chat.services import access, people
from formbuilder.services.audience import guardian_ids_of
from members.models import Member
from members.services import access as member_access
from members.services.errors import Invalid, NotAllowed
from teams.models import Team


def direct_key(a_id, b_id) -> str:
    return ":".join(sorted([str(a_id), str(b_id)]))


def can_create_team_chat(user, team) -> bool:
    return team.pk in member_access.staffed_team_ids(user) or member_access.can_moderate_chat(user)


def may_announce(user, conversation_or_team) -> bool:
    """Announcement mode on a team chat: staff of that team or ``chat.announce``; anywhere else only ``chat.announce``."""
    team = conversation_or_team.team if isinstance(conversation_or_team, Conversation) else conversation_or_team
    return member_access.can_announce_chat(user) or (team is not None and team.pk in member_access.staffed_team_ids(user))


def _add(conversation, member, *, is_admin=False, source=Participant.Source.MANUAL) -> Participant:
    p, created = Participant.objects.get_or_create(conversation=conversation, member=member, defaults={"is_admin": is_admin, "source": source, "last_read_seq": conversation.last_seq})
    if created:
        return p
    changed = False
    if p.left_at is not None:
        p.left_at, p.removed_by_admin, p.joined_at, changed = None, False, timezone.now(), True
    if is_admin and not p.is_admin:
        p.is_admin, changed = True, True
    if source == Participant.Source.MANUAL and p.source == Participant.Source.RULE:
        p.source, changed = source, True
    if changed:
        p.save()
    return p


def safeguard_minors(conversation) -> None:
    """A minor chatting with an adult who is neither staff nor their parent needs a parent in the chat: add the child's parents.
    (Team chats are led by staff and keep their own audience.)"""
    if conversation.kind == Conversation.Kind.TEAM:
        return
    members = [p.member for p in access.active(conversation.participants.select_related("member"))]
    persons = people.resolve(members, team=conversation.team)
    minors = [m for m in members if people.is_minor(m)]
    if not minors:
        return
    adults = [m for m in members if m not in minors]
    present = {m.pk for m in members}
    for child in minors:
        guardians = guardian_ids_of(child)
        strangers = [a for a in adults if not people.is_staffish(persons[a.pk]) and a.pk not in guardians]
        if not strangers or guardians & present:
            continue
        candidates = list(Member.objects.filter(pk__in=guardians, user__isnull=False))
        if not candidates:
            raise Invalid("This child has no parent with a login, so they cannot be added to a chat with adults who are not staff.", code="minor_needs_parent")
        for g in candidates:
            _add(conversation, g)
            present.add(g.pk)


def _people(ids, *, exclude=None) -> list:
    found = {m.pk: m for m in Member.objects.filter(pk__in=ids)}
    if len(found) != len(set(ids)):
        raise Invalid("Unknown person.", errors={"member_ids": ["Unknown person."]})
    return [m for m in found.values() if exclude is None or m.pk != exclude.pk]


@transaction.atomic
def create(user, me: Member, *, kind, mode="discussion", title="", member_ids=(), team_id=None, include=None) -> tuple[Conversation, bool]:
    """Returns ``(conversation, created)``. Raises ``Invalid`` / ``NotAllowed``."""
    if kind not in Conversation.Kind.values:
        raise Invalid("Unknown kind.", errors={"kind": ["Unknown kind."]})
    if mode not in Conversation.Mode.values:
        raise Invalid("Unknown mode.", errors={"mode": ["Unknown mode."]})
    title = (title or "").strip()
    member_ids = list(dict.fromkeys(member_ids or []))
    if kind == Conversation.Kind.DIRECT:
        if mode != Conversation.Mode.DISCUSSION:
            raise Invalid("A direct chat is a discussion.")
        return _create_direct(me, member_ids)
    if kind == Conversation.Kind.GROUP:
        if not title:
            raise Invalid("A group chat needs a title.", errors={"title": ["A group chat needs a title."]})
        if not member_ids:
            raise Invalid("Add at least one person.", errors={"member_ids": ["Add at least one person."]})
        if mode == Conversation.Mode.ANNOUNCEMENT and not member_access.can_announce_chat(user):
            raise NotAllowed("You may not start announcement chats.")
        others = _people(member_ids, exclude=me)
        c = Conversation.objects.create(kind=kind, mode=mode, title=title[:150], created_by=me)
        _add(c, me, is_admin=True)
        for m in others:
            _add(c, m)
        safeguard_minors(c)
        return c, True
    team = Team.objects.filter(pk=team_id).first() if team_id else None
    if team is None:
        raise Invalid("Pick a team.", errors={"team_id": ["Pick a team."]})
    if not can_create_team_chat(user, team):
        raise NotAllowed("You may not start a chat for this team.")
    if mode == Conversation.Mode.ANNOUNCEMENT and not may_announce(user, team):
        raise NotAllowed("You may not start announcement chats for this team.")
    include = include or {}
    c = Conversation.objects.create(kind=kind, mode=mode, title=(title or team.name)[:150], team=team, created_by=me)
    _add(c, me, is_admin=True)
    AudienceRule.objects.create(conversation=c, team=team, include_staff=bool(include.get("staff", True)), include_players=bool(include.get("players", True)), include_parents=bool(include.get("parents", False)))
    from chat.services import roster

    roster.sync(c)
    return c, True


def _create_direct(me: Member, member_ids) -> tuple[Conversation, bool]:
    if len(member_ids) != 1:
        raise Invalid("Pick exactly one person.", errors={"member_ids": ["Pick exactly one person."]})
    other = Member.objects.filter(pk=member_ids[0]).first()
    if other is None or other.pk == me.pk or not other.user_id:
        raise Invalid("You cannot start a chat with this person.", errors={"member_ids": ["You cannot start a chat with this person."]})
    key = direct_key(me.pk, other.pk)
    existing = Conversation.objects.filter(direct_key=key).first()
    if existing is not None:
        for m in (me, other):
            _add(existing, m)
        return existing, False
    c = Conversation.objects.create(kind=Conversation.Kind.DIRECT, direct_key=key, created_by=me)
    _add(c, me)
    _add(c, other)
    # A minor and an adult who is neither staff nor their parent: the chat becomes a group with the parent(s) in it.
    before = access.active(c.participants).count()
    safeguard_minors(c)
    if access.active(c.participants).count() > before:
        c.kind, c.title = Conversation.Kind.GROUP, f"{me.get_full_name()}, {other.get_full_name()}"[:150]
        c.save(update_fields=["kind", "title", "modified"])
    return c, True


def update(user, me, conversation, *, title=None, mode=None, muted=None, archived=None) -> Conversation:
    mine = access.own_participant(me, conversation)
    if mine is None:
        raise NotAllowed("You are not in this chat.")
    if muted is not None:
        mine.muted = bool(muted)
        mine.save(update_fields=["muted", "modified"])
    if title is None and mode is None and archived is None:
        return conversation
    if not mine.is_admin:
        raise NotAllowed("Only chat admins can change this.")
    if conversation.kind == Conversation.Kind.DIRECT:
        raise Invalid("A direct chat has no settings.")
    fields = ["modified"]
    if title is not None:
        title = title.strip()
        if not title:
            raise Invalid("A chat needs a title.", errors={"title": ["A chat needs a title."]})
        conversation.title = title[:150]
        fields.append("title")
    if mode is not None:
        if mode not in Conversation.Mode.values:
            raise Invalid("Unknown mode.", errors={"mode": ["Unknown mode."]})
        if mode == Conversation.Mode.ANNOUNCEMENT and conversation.mode != mode and not (may_announce(user, conversation) if conversation.kind == Conversation.Kind.TEAM else member_access.can_announce_chat(user)):
            raise NotAllowed("You may not switch this chat to announcements.")
        conversation.mode = mode
        fields.append("mode")
    if archived is not None:
        conversation.archived = bool(archived)
        fields.append("archived")
    conversation.save(update_fields=fields)
    return conversation


@transaction.atomic
def add_participants(me, conversation, member_ids) -> None:
    mine = access.own_participant(me, conversation)
    if mine is None:
        raise NotAllowed("You are not in this chat.")
    if conversation.kind == Conversation.Kind.DIRECT:
        raise Invalid("You cannot add people to a direct chat.")
    if not mine.is_admin:
        raise NotAllowed("Only chat admins can add people.")
    for m in _people(list(dict.fromkeys(member_ids or []))):
        _add(conversation, m)
    safeguard_minors(conversation)


@transaction.atomic
def remove_participant(me, conversation, member_id) -> None:
    mine = access.own_participant(me, conversation)
    if mine is None:
        raise NotAllowed("You are not in this chat.")
    if conversation.kind == Conversation.Kind.DIRECT:
        raise Invalid("You cannot remove people from a direct chat.")
    if not mine.is_admin:
        raise NotAllowed("Only chat admins can remove people.")
    if member_id == me.pk:
        raise Invalid("You cannot remove yourself.")
    target = access.active(conversation.participants).filter(member_id=member_id).first()
    if target is None:
        raise Invalid("This person is not in the chat.", code="not_found")
    target.left_at, target.removed_by_admin = timezone.now(), True
    target.save(update_fields=["left_at", "removed_by_admin", "modified"])
