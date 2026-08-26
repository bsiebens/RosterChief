from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import UniqueConstraint
from django.utils.translation import gettext_lazy as _

from club.models import Season
from members.models import Group, Member
from rosterchief.base import ClubScopedModel, UUIDModel, unique_slugify, validate_club_scope
from teams.models import Team


class Form(ClubScopedModel):
    """A reusable question set -- title/description/fields only. Sending it to
    an audience, with its own deadline and response window, is a separate
    ``FormSend`` (a form can be sent more than once, to different audiences,
    on different occasions, without redefining its questions each time)."""

    title = models.CharField(_("title"), max_length=255)
    slug = models.SlugField(_("slug"), blank=True)
    description = models.TextField(_("description"), blank=True)

    slug_source = "title"

    #: Template-lifecycle only -- whether this question set can still be picked
    #: for a *new* FormSend. Not consulted by submit_form at all; a send's own
    #: is_active/opens_at/closes_at governs whether it's currently accepting
    #: responses.
    is_active = models.BooleanField(_("is active?"), default=True, help_text=_("Whether this form can still be picked when creating a new send. Doesn't affect sends already out."))
    login_required = models.BooleanField(_("login required?"), default=False)

    class Meta:
        verbose_name = _("form")
        verbose_name_plural = _("forms")
        ordering = ["title"]
        constraints = [
            UniqueConstraint(fields=["club", "slug"], name="unique_form_slug_per_club"),
        ]

    def __str__(self):
        return self.title


class Field(UUIDModel):
    class FieldType(models.TextChoices):
        TEXT = "text", _("text")
        TEXTAREA = "textarea", _("textarea")
        NUMBER = "number", _("number")
        EMAIL = "email", _("email")
        DATE = "date", _("date")
        CHOICE = "choice", _("choice")
        MULTICHOICE = "multichoice", _("multichoice")
        CHECKBOX = "checkbox", _("checkbox")
        FILE = "file", _("file")

    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="fields", verbose_name=_("form"))
    key = models.SlugField(_("key"), blank=True)
    label = models.CharField(_("label"), max_length=255)
    field_type = models.CharField(_("field type"), max_length=255, choices=FieldType.choices, default=FieldType.TEXT)
    required = models.BooleanField(_("required?"), default=True)
    help_text = models.TextField(_("help text"), blank=True)
    order = models.PositiveIntegerField(_("order"), default=0)
    is_active = models.BooleanField(_("is active?"), default=True)
    options = models.JSONField(_("options"), blank=True, null=True)

    class Meta:
        verbose_name = _("field")
        verbose_name_plural = _("fields")
        ordering = ["form", "order"]
        constraints = [
            UniqueConstraint(fields=["form", "key"], name="unique_field_key_per_form"),
        ]

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = unique_slugify(self, self.label, slug_field="key", scope={"form": self.form})
        super().save(*args, **kwargs)

    def __str__(self):
        return self.label


class FormSend(ClubScopedModel):
    """One occasion of sending a ``Form`` to an audience -- carries the
    audience, the response window, and the reminder/dedup state. Verbatim
    audience shape of ``events.Event``/``EventSeries`` (teams/groups/
    club_wide/invited_members/excluded_members), reusing the same generic
    ``members.Group`` rather than inventing a new grouping concept.

    A ``Submission`` belongs to a send, not directly to a ``Form`` -- the same
    question set sent three times produces three separate response sets."""

    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="sends", verbose_name=_("form"))

    teams = models.ManyToManyField(Team, related_name="form_sends", blank=True, verbose_name=_("teams"))
    groups = models.ManyToManyField(Group, related_name="form_sends", blank=True, verbose_name=_("groups"), help_text=_("Send to every current member of these groups."))
    club_wide = models.BooleanField(_("whole club"), default=False, help_text=_("Send to every active club member for this send's season, instead of specific teams/groups. Can't be combined with teams or groups."))
    invited_members = models.ManyToManyField(Member, related_name="invited_to_form_sends", blank=True, verbose_name=_("invited members"))
    excluded_members = models.ManyToManyField(Member, related_name="excluded_from_form_sends", blank=True, verbose_name=_("excluded members"))
    season = models.ForeignKey(Season, on_delete=models.SET_NULL, related_name="form_sends", null=True, blank=True, verbose_name=_("season"), help_text=_("Season whose rosters define the audience; derived from the current season when blank."))

    opens_at = models.DateTimeField(_("opens at"), blank=True, null=True)
    closes_at = models.DateTimeField(_("closes at"), blank=True, null=True)
    max_submissions_per_user = models.PositiveIntegerField(_("max submissions per user"), blank=True, null=True)

    #: This occasion's own on/off switch, independent of opens_at/closes_at --
    #: e.g. pause collection right now without touching the dates.
    is_active = models.BooleanField(_("is active?"), default=True)

    reminder_sent_at = models.DateTimeField(
        _("reminder sent at"),
        blank=True,
        null=True,
        help_text=_("Set once formbuilder.tasks.send_form_reminders has nudged whoever still hasn't responded -- keeps that job from reminding the same send twice."),
    )
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="form_sends_created", blank=True, null=True, verbose_name=_("created by"))

    class Meta:
        verbose_name = _("form send")
        verbose_name_plural = _("form sends")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.form} ({self.created:%d %b %Y})"

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("form",))


class Submission(UUIDModel):
    send = models.ForeignKey(FormSend, on_delete=models.CASCADE, related_name="submissions", verbose_name=_("send"))
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="submissions", verbose_name=_("member"), blank=True, null=True)
    submitted_at = models.DateTimeField(_("submitted at"), auto_now_add=True)

    class Meta:
        verbose_name = _("submission")
        verbose_name_plural = _("submissions")
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.send.form} - {self.member}"


class Answer(UUIDModel):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="answers", verbose_name=_("submission"))
    field = models.ForeignKey(Field, on_delete=models.PROTECT, related_name="answers", verbose_name=_("field"))
    value = models.JSONField(_("value"), blank=True, null=True)

    class Meta:
        verbose_name = _("answer")
        verbose_name_plural = _("answers")
        ordering = ["submission", "field"]
        constraints = [
            UniqueConstraint(fields=["submission", "field"], name="unique_answer_per_field_per_submission"),
        ]

    def __str__(self):
        return f"{self.submission} - {self.field}"

    def clean(self):
        if self.field_id and self.submission_id and self.field.form_id != self.submission.send.form_id:
            raise ValidationError({"field": _("Must belong to the same form as the submission's send.")})
