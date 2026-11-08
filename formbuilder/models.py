"""Form builder: reusable question sets (``Form`` + ``Field``), sent to an audience on an occasion (``FormSend``), answered
by members (``Submission`` + ``Answer``). Lives in the club schema, so there is no club foreign key anywhere."""

import secrets

from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.text import get_valid_filename, slugify
from django.utils.translation import gettext_lazy as _

from events.models import Season
from members.models import Group, Member
from rosterchief.base import UUIDModel
from rosterchief.storage import get_private_storage
from teams.models import Team


def _generate_public_token() -> str:
    return secrets.token_urlsafe(32)


#: Longest original file name kept in a stored path (the extension survives).
_MAX_STORED_NAME = 100


def answer_file_path(answer, filename):
    """``formbuilder/<send id>/<submission id>/<random>-<safe file name>`` in private storage. The random prefix keeps two
    uploads with the same name apart and makes the path unguessable; the original name is kept (made safe for a
    filesystem) only so a downloaded file is recognisable."""
    try:
        safe_name = get_valid_filename(filename)
    except SuspiciousFileOperation:  # a name like "." or "" has nothing usable left
        safe_name = "file"
    if len(safe_name) > _MAX_STORED_NAME:
        stem, dot, extension = safe_name.rpartition(".")
        safe_name = f"{stem[: _MAX_STORED_NAME - len(extension) - 1]}.{extension}" if dot and len(extension) < 10 else safe_name[:_MAX_STORED_NAME]
    submission = answer.submission
    return f"formbuilder/{submission.send_id}/{submission.pk}/{secrets.token_hex(8)}-{safe_name}"


def unique_slug(instance, value: str, *, slug_field: str = "slug", scope: dict | None = None, fallback: str = "item") -> str:
    """A slug from ``value`` that no sibling has (within ``scope``): ``-2``, ``-3``, ... on collision, cut to the field length."""
    max_length = instance._meta.get_field(slug_field).max_length
    base = slugify(value)[:max_length] or fallback
    others = type(instance)._default_manager.exclude(pk=instance.pk)
    if scope:
        others = others.filter(**scope)
    slug, suffix = base, 2
    while others.filter(**{slug_field: slug}).exists():
        tail = f"-{suffix}"
        slug = f"{base[: max_length - len(tail)]}{tail}"
        suffix += 1
    return slug


class Form(UUIDModel):
    """A reusable question set -- title/description/fields only. Sending it to an audience, with its own deadline and
    response window, is a separate ``FormSend`` (a form can be sent more than once without redefining its questions)."""

    title = models.CharField(_("title"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True)
    description = models.TextField(_("description"), blank=True)
    #: Template lifecycle only: whether this question set can still be picked for a *new* send. A send's own
    #: is_active/opens_at/closes_at governs whether it accepts responses.
    is_active = models.BooleanField(_("is active?"), default=True)
    login_required = models.BooleanField(_("login required?"), default=False)

    class Meta:
        verbose_name = _("form")
        verbose_name_plural = _("forms")
        ordering = ["title"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.title, fallback="form")
        super().save(*args, **kwargs)


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
    key = models.SlugField(_("key"), max_length=100, blank=True)
    label = models.CharField(_("label"), max_length=255)
    field_type = models.CharField(_("field type"), max_length=20, choices=FieldType.choices, default=FieldType.TEXT)
    required = models.BooleanField(_("required?"), default=True)
    help_text = models.TextField(_("help text"), blank=True)
    #: Display grouping only: runs of consecutive fields (by ``order``) sharing a section value are shown together.
    section = models.CharField(_("section"), max_length=255, blank=True)
    order = models.PositiveIntegerField(_("order"), default=0)
    is_active = models.BooleanField(_("is active?"), default=True)
    #: Choice definitions: a list of plain strings, or of ``{"value": ..., "label": ...}`` objects.
    options = models.JSONField(_("options"), blank=True, null=True)

    class Meta:
        verbose_name = _("field")
        verbose_name_plural = _("fields")
        ordering = ["form", "order"]
        constraints = [UniqueConstraint(fields=["form", "key"], name="unique_field_key_per_form")]

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = unique_slug(self, self.label, slug_field="key", scope={"form": self.form_id}, fallback="field")
        super().save(*args, **kwargs)


class FormSend(UUIDModel):
    """One occasion of sending a ``Form`` to an audience: the audience, the response window and the reminder state. Same
    audience shape as ``events.Event``. A ``Submission`` belongs to a send, so sending the same form three times gives
    three separate response sets."""

    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="sends", verbose_name=_("form"))

    teams = models.ManyToManyField(Team, related_name="form_sends", blank=True, verbose_name=_("teams"))
    groups = models.ManyToManyField(Group, related_name="form_sends", blank=True, verbose_name=_("groups"))
    club_wide = models.BooleanField(_("whole club"), default=False, help_text=_("Every active club member of the season. Can't be combined with teams or groups."))
    invited_members = models.ManyToManyField(Member, related_name="invited_to_form_sends", blank=True, verbose_name=_("invited members"))
    excluded_members = models.ManyToManyField(Member, related_name="excluded_from_form_sends", blank=True, verbose_name=_("excluded members"))
    season = models.ForeignKey(Season, on_delete=models.SET_NULL, related_name="form_sends", null=True, blank=True, verbose_name=_("season"), help_text=_("Season whose rosters define the audience; the current season when blank."))

    opens_at = models.DateTimeField(_("opens at"), blank=True, null=True)
    closes_at = models.DateTimeField(_("closes at"), blank=True, null=True)
    max_submissions_per_user = models.PositiveIntegerField(_("max submissions per user"), blank=True, null=True)
    #: This occasion's own on/off switch, independent of opens_at / closes_at.
    is_active = models.BooleanField(_("is active?"), default=True)
    #: Opt-in: a send never becomes link-shareable by accident. Needs a form that does not require a login.
    is_public = models.BooleanField(_("public"), default=False)
    public_token = models.CharField(_("public token"), max_length=64, unique=True, editable=False, default=_generate_public_token)
    #: Set (atomically) when the audience was told about this send; a scheduled send is told when it opens.
    notified_at = models.DateTimeField(_("notified at"), blank=True, null=True)
    reminder_sent_at = models.DateTimeField(_("reminder sent at"), blank=True, null=True)
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="form_sends_created", blank=True, null=True, verbose_name=_("created by"))

    class Meta:
        verbose_name = _("form send")
        verbose_name_plural = _("form sends")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.form} ({self.created:%d %b %Y})"


class Submission(UUIDModel):
    send = models.ForeignKey(FormSend, on_delete=models.CASCADE, related_name="submissions", verbose_name=_("send"))
    #: Who the response is *for*: the audience member it counts against.
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="form_submissions", verbose_name=_("member"), blank=True, null=True)
    #: Who actually filled it in: the member, or a guardian answering for a child. Blank for an anonymous public response.
    submitted_by = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="form_submissions_made", verbose_name=_("submitted by"), blank=True, null=True)
    submitted_at = models.DateTimeField(_("submitted at"), auto_now_add=True)

    class Meta:
        verbose_name = _("submission")
        verbose_name_plural = _("submissions")
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.send.form} - {self.member}"

    @property
    def submitted_on_behalf_by(self):
        """The submitter when someone other than ``member`` filled this in, else ``None``."""
        if self.submitted_by_id is not None and self.submitted_by_id != self.member_id:
            return self.submitted_by
        return None


class Answer(UUIDModel):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="answers", verbose_name=_("submission"))
    field = models.ForeignKey(Field, on_delete=models.PROTECT, related_name="answers", verbose_name=_("field"))
    #: DjangoJSONEncoder: a number validates to a Decimal, which the stdlib encoder cannot serialise.
    value = models.JSONField(_("value"), blank=True, null=True, encoder=DjangoJSONEncoder)
    #: A file field's upload, in private storage (read only through an authenticated endpoint). ``value`` then holds the
    #: upload's metadata (``{"name", "size", "content_type"}``) so reports show the original name, never the path.
    file = models.FileField(_("file"), storage=get_private_storage, upload_to=answer_file_path, max_length=255, blank=True)

    class Meta:
        verbose_name = _("answer")
        verbose_name_plural = _("answers")
        ordering = ["submission", "field"]
        constraints = [UniqueConstraint(fields=["submission", "field"], name="unique_answer_per_field_per_submission")]

    def __str__(self):
        return f"{self.submission} - {self.field}"

    def clean(self):
        if self.field_id and self.submission_id and self.field.form_id != self.submission.send.form_id:
            raise ValidationError({"field": _("Must belong to the same form as the submission's send.")})

    @property
    def file_name(self) -> str:
        """The name the submitter's file had, for display and downloads."""
        if isinstance(self.value, dict) and self.value.get("name"):
            return str(self.value["name"])
        return self.file.name.rsplit("/", 1)[-1] if self.file else ""


@receiver(post_delete, sender=Answer)
def _delete_answer_file(sender, instance, **kwargs):
    """Drop the stored file along with its answer (also when a submission, send or form cascades)."""
    if instance.file:
        instance.file.storage.delete(instance.file.name)
