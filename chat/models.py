"""Chat: conversations between club members (direct, group and team chats), with discussion and announcement modes.

Who somebody is in a chat (staff, player, parent, ...) is never stored: it is resolved at read time from the current season
(``chat.services.people``). Read state is a per-participant ``last_read_seq``; the per-conversation ``last_seq`` counter gives every
message a strictly increasing sequence number the clients poll with."""

import os
import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel
from rosterchief.storage import get_private_storage

REACTION_EMOJI = ("👍", "❤️", "😂", "😮", "🙏", "✅")


class Conversation(UUIDModel):
    class Kind(models.TextChoices):
        TEAM = "team", _("Team chat")
        GROUP = "group", _("Group chat")
        DIRECT = "direct", _("Direct chat")

    class Mode(models.TextChoices):
        DISCUSSION = "discussion", _("Discussion")
        ANNOUNCEMENT = "announcement", _("Announcement")

    kind = models.CharField(_("kind"), max_length=10, choices=Kind.choices)
    mode = models.CharField(_("mode"), max_length=15, choices=Mode.choices, default=Mode.DISCUSSION)
    title = models.CharField(_("title"), max_length=150, blank=True)
    team = models.ForeignKey("teams.Team", verbose_name=_("team"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_by = models.ForeignKey("members.Member", verbose_name=_("created by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # "<smaller member id>:<larger member id>" for a direct chat, so a pair has at most one.
    direct_key = models.CharField(max_length=80, null=True, blank=True, unique=True, editable=False)
    last_seq = models.PositiveIntegerField(default=0, editable=False)
    last_message_at = models.DateTimeField(null=True, blank=True, editable=False)
    archived = models.BooleanField(_("archived"), default=False)

    class Meta:
        ordering = ["-last_message_at"]

    def __str__(self):
        return self.title or f"{self.kind} {self.pk}"


class Participant(UUIDModel):
    """One row per person per chat. Players and parents each have their own row. ``left_at`` is set when the person drops out of
    a team roster (or an admin removes them); the row stays so a re-added person keeps their read state."""

    class Source(models.TextChoices):
        MANUAL = "manual", _("Added by hand")
        RULE = "rule", _("Team roster")

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="participants")
    member = models.ForeignKey("members.Member", on_delete=models.CASCADE, related_name="chat_participations")
    is_admin = models.BooleanField(default=False)
    muted = models.BooleanField(default=False)
    last_read_seq = models.PositiveIntegerField(default=0)
    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    removed_by_admin = models.BooleanField(default=False)  # never re-added by the roster sync
    last_push_at = models.DateTimeField(null=True, blank=True)  # throttle: one push per conversation and person per 5 minutes

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "member"], name="chat_unique_participant")]
        indexes = [models.Index(fields=["member", "left_at"])]


class Message(UUIDModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    seq = models.PositiveIntegerField()
    author = models.ForeignKey("members.Member", null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField(blank=True, max_length=4000)
    urgent = models.BooleanField(default=False)
    client_key = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey("members.Member", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["seq"]
        constraints = [
            models.UniqueConstraint(fields=["conversation", "seq"], name="chat_unique_seq"),
            models.UniqueConstraint(fields=["conversation", "author", "client_key"], name="chat_unique_client_key", condition=models.Q(client_key__isnull=False)),
        ]

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None


def attachment_path(instance, filename):
    return f"chat/{instance.conversation_id}/{uuid.uuid4().hex}{os.path.splitext(filename)[1][:10].lower()}"


class MessageAttachment(UUIDModel):
    """Uploaded first (``message`` empty), attached when the message is sent. Stored in private media, served through an authenticated view."""

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="attachments")
    message = models.ForeignKey(Message, null=True, blank=True, on_delete=models.CASCADE, related_name="attachments")
    uploader = models.ForeignKey("members.Member", null=True, on_delete=models.SET_NULL, related_name="+")
    file = models.FileField(upload_to=attachment_path, storage=get_private_storage, max_length=255)
    name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()

    class Meta:
        ordering = ["created"]


class MessageReaction(UUIDModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reactions")
    member = models.ForeignKey("members.Member", on_delete=models.CASCADE, related_name="+")
    emoji = models.CharField(max_length=16)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "member", "emoji"], name="chat_unique_reaction")]


class MessageReport(UUIDModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey("members.Member", null=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.TextField(max_length=1000)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey("members.Member", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created"]


class AudienceRule(UUIDModel):
    """Who a team chat contains: the staff / players / parents of a team in the current season. The roster sync keeps the participants in step.
    A rule without a team is a club-wide audience (every reachable member, narrowed by the include flags); a club-wide chat narrowed to
    teams has one rule per team."""

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="rules")
    team = models.ForeignKey("teams.Team", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    group = models.ForeignKey("members.Group", null=True, blank=True, on_delete=models.CASCADE, related_name="+", help_text="A chat for a members group: its admins are the staff, the other members the players.")
    include_staff = models.BooleanField(default=True)
    include_players = models.BooleanField(default=True)
    include_parents = models.BooleanField(default=False)


class ChatSettings(UUIDModel):
    """One row per club (``ChatSettings.current()``)."""

    retention_months = models.PositiveSmallIntegerField(_("keep messages for (months)"), default=24, help_text=_("0 keeps messages forever."))

    @classmethod
    def current(cls) -> ChatSettings:
        return cls.objects.order_by("created").first() or cls.objects.create()
