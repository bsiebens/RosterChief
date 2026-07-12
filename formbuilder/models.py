from django.db import models
from django.db.models import UniqueConstraint
from django.utils.translation import gettext_lazy as _

from clubmanager.base import ClubScopedModel, UUIDModel, unique_slugify
from members.models import Member


class Form(ClubScopedModel):
    title = models.CharField(_("title"), max_length=255)
    slug = models.SlugField(_("slug"), blank=True)
    description = models.TextField(_("description"), blank=True)

    slug_source = "title"

    is_active = models.BooleanField(_("is active?"), default=True)
    login_required = models.BooleanField(_("login required?"), default=False)

    opens_at = models.DateTimeField(_("opens at"), blank=True, null=True)
    closes_at = models.DateTimeField(_("closes at"), blank=True, null=True)
    max_submissions_per_user = models.PositiveIntegerField(_("max submissions per user"), blank=True, null=True)

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


class Submission(UUIDModel):
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="submissions", verbose_name=_("form"))
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="submissions", verbose_name=_("member"), blank=True, null=True)
    submitted_at = models.DateTimeField(_("submitted at"), auto_now_add=True)

    class Meta:
        verbose_name = _("submission")
        verbose_name_plural = _("submissions")
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.form} - {self.member}"


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
