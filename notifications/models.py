from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from members.models import Member
from rosterchief.base import ClubScopedModel, validate_club_scope


class Notification(ClubScopedModel):
    """One member's copy of a notification -- "this member should know about
    X" -- built for news publishing first (see news.tasks.notify_news_published),
    but `source` is generic on purpose: the whole point is reusing this for
    other kinds of activity later without a new model each time.

    Always keyed to the Member it's *about*, even though actual delivery may
    go to a parent/guardian's email instead of (or as well as) the member's
    own -- see notifications.services.notify_members. That's what a future
    in-app feed for this member would list, regardless of who was actually
    emailed; `sent_to_emails` is the audit trail of who that was.
    """

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="notifications", verbose_name=_("member"))
    title = models.CharField(_("title"), max_length=255)
    body = models.TextField(_("body"))

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001 -- GenericForeignKey needs a real NULL, not "", for "no source"
    source = GenericForeignKey("content_type", "object_id")

    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    sent_to_emails = models.JSONField(_("sent to"), default=list, blank=True, help_text=_("Every email address this actually went to -- the member's own, and/or a parent/guardian's. Empty means nobody reachable was found."))
    read_at = models.DateTimeField(_("read at"), null=True, blank=True, help_text=_("Set once there's somewhere for a member to read this -- not used yet."))

    class Meta:
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        ordering = ["-created"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.member} — {self.title}"

    def clean(self):
        validate_club_scope(self, self.club_id, member_fields=("member",))

    @property
    def is_sent(self) -> bool:
        return self.sent_at is not None
