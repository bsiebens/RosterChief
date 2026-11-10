import hashlib
from decimal import Decimal

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.fields.files import ImageFieldFile
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from rosterchief.base import UUIDModel
from rosterchief.storage import get_private_storage


def member_photo_path(instance, filename):
    return f"members/{instance.pk}/{filename}"


class MemberPhotoFieldFile(ImageFieldFile):
    @property
    def url(self):
        self._require_file()
        return self.instance.photo_url_for(self.field.name)


class MemberPhotoField(models.ImageField):
    attr_class = MemberPhotoFieldFile


class MemberQuerySet(models.QuerySet):
    """Role filters. A person has no stored "type": roles are derived from the
    records that make them one, so one person can be a member, a guardian and
    staff at once without any flag drifting out of date.

    - member:   holds a ``ClubMembership`` (plays/pays in some season)
    - guardian: is a parent/guardian in a family that also has a child
    - staff:    has a ``teams.StaffAssignment`` (reverse ``staff_assignments``)
    """

    def members(self, season=None):
        qs = ClubMembership.objects.filter(member=OuterRef("pk"))
        if season is not None:
            qs = qs.filter(season=season)
        return self.filter(Exists(qs))

    def guardians(self):
        children = FamilyMembership.objects.filter(family=OuterRef("family"), role=FamilyMembership.FamilyRole.CHILD)
        links = FamilyMembership.objects.filter(member=OuterRef("pk"), role__in=FamilyMembership.GUARDIAN_ROLES).filter(Exists(children))
        return self.filter(Exists(links))

    def staff(self, season=None):
        from teams.models import StaffAssignment

        qs = StaffAssignment.objects.filter(member=OuterRef("pk"))
        if season is not None:
            qs = qs.filter(season=season)
        return self.filter(Exists(qs))


class Member(UUIDModel):
    """A person on a club's roster. Lives in the club schema. A login (``user``) is optional.
    What the person is to the club (member, guardian, staff -- any combination) is derived, see ``MemberQuerySet``."""

    class Role(models.TextChoices):
        MEMBER = "member", _("Member")
        GUARDIAN = "guardian", _("Guardian")
        STAFF = "staff", _("Staff")

    user = models.OneToOneField(settings.AUTH_USER_MODEL, verbose_name=_("user"), null=True, blank=True, on_delete=models.DO_NOTHING, related_name="+")

    stats_share_token = models.CharField(
        _("stats share token"),
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        editable=False,
        help_text=_("Secret in the personal public link to this person's game stats; empty = not shared. Replacing it kills the old link."),
    )

    first_name = models.CharField(_("first_name"), max_length=75)
    last_name = models.CharField(_("last_name"), max_length=75)
    email = models.EmailField(_("email"), blank=True, help_text=_("Contact address. Also used as the login email when access is granted."))

    phone = PhoneNumberField(_("phone number"), null=True, blank=True)
    emergency_phone = PhoneNumberField(_("emergency phone number"), null=True, blank=True)

    player_photo = MemberPhotoField(_("player picture"), upload_to=member_photo_path, storage=get_private_storage, blank=True)
    profile_photo = MemberPhotoField(_("profile picture"), upload_to=member_photo_path, storage=get_private_storage, blank=True)
    photo_public_consent = models.BooleanField(_("show the player picture on the public website/API"), default=False)

    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True)
    language = models.CharField(_("language"), max_length=10, blank=True, choices=settings.LANGUAGES, help_text=_("Preferred language for messages to this person. Empty means the club default."))

    license_number = models.CharField(_("license number"), max_length=50, blank=True, null=True)

    objects = MemberQuerySet.as_manager()

    class Meta:
        verbose_name = _("member")
        verbose_name_plural = _("members")
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["last_name", "first_name"])]

    def __str__(self):
        return self.get_full_name()

    @property
    def is_member(self) -> bool:
        return self.club_memberships.exists()

    @property
    def is_guardian(self) -> bool:
        return Member.objects.guardians().filter(pk=self.pk).exists()

    @property
    def is_staff_member(self) -> bool:
        return self.staff_assignments.exists()

    @property
    def roles(self) -> set:
        """Every role this person holds -- possibly several, possibly none (a plain contact)."""
        found = set()
        if self.is_member:
            found.add(self.Role.MEMBER)
        if self.is_guardian:
            found.add(self.Role.GUARDIAN)
        if self.is_staff_member:
            found.add(self.Role.STAFF)
        return found

    @property
    def photo(self):
        """The picture the club apps show: the profile picture, else the player picture"""
        return self.profile_photo or self.player_photo

    @property
    def player_photo_url(self):
        return self.photo_url_for("player_photo")

    @property
    def profile_photo_url(self):
        return self.photo_url_for("profile_photo")

    @property
    def photo_url(self):
        """URL of the effective picture (profile, else player)."""
        f = self.photo
        if not f:
            return None
        version = hashlib.sha256(f.name.encode()).hexdigest()[:12]
        return f"{reverse('members:photo', args=[self.pk])}?v={version}"

    @property
    def public_photo(self):
        """The *player* picture, but only once ``photo_public_consent`` says it
        can leave the club's own app/staff views -- the one gate every public
        surface (teams.api's roster endpoints, a club's public website) must
        go through, so consent can't be forgotten at a new call site. The
        profile picture is never public."""
        return self.player_photo if self.player_photo and self.photo_public_consent else None

    @property
    def contact_email(self):
        """Best email to reach this member: own contact email, else login email."""
        return self.email or (self.user.email if self.user_id else "")

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    def photo_url_for(self, field_name: str):
        f = getattr(self, field_name)

        if not f:
            return None

        version = hashlib.sha256(f.name.encode()).hexdigest()[:12]
        route = {"player_photo": "members:player_photo", "profile_photo": "members:profile_photo"}[field_name]
        return f"{reverse(route, args=[self.pk])}?v={version}"


class Family(UUIDModel):
    name = models.CharField(_("name"), max_length=250, blank=True)

    class Meta:
        verbose_name = _("family")
        verbose_name_plural = _("families")
        ordering = ["name"]

    def __str__(self):
        if self.name:
            return self.name

        surnames = sorted({last_name for last_name in self.memberships.values_list("member__last_name", flat=True) if last_name})

        if surnames:
            return _("%(surnames)s") % {"surnames": " / ".join(surnames)}
        return _("Family %(id)s") % {"id": str(self.pk)[:8]}


class FamilyMembership(UUIDModel):
    class FamilyRole(models.TextChoices):
        PARENT = "parent", _("parent")
        CHILD = "child", _("child")
        GUARDIAN = "guardian", _("guardian")
        OTHER = "other", _("other")

    #: Roles that make someone a guardian when the family also has a child.
    GUARDIAN_ROLES = (FamilyRole.PARENT, FamilyRole.GUARDIAN)

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="memberships", verbose_name=_("family"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="family_memberships", verbose_name=_("member"))
    role = models.CharField(_("role"), max_length=255, choices=FamilyRole.choices, default=FamilyRole.PARENT)

    class Meta:
        verbose_name = _("family membership")
        verbose_name_plural = _("family memberships")
        ordering = ["family", "role", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["family", "member"], name="unique_member_per_family"),
        ]

    def __str__(self):
        return f"{self.family} - {self.member} ({self.get_role_display()})"


class Group(UUIDModel):
    """An arbitrary named collection of members -- deliberately generic, not
    team-shaped and not aware of any specific use: "all coaches", "all team
    managers", an ad-hoc committee. A Team's roster is a separate, more
    specific concept (teams.TeamMembership); nothing here assumes team
    semantics. Referee eligibility (who can ref which team) is a member-level
    fact (teams.RefereeProfile), not a Group concern -- Group carries no
    referee-specific knowledge at all."""

    name = models.CharField(_("name"), max_length=255, unique=True)

    class Meta:
        verbose_name = _("group")
        verbose_name_plural = _("groups")
        ordering = ["name"]

    def __str__(self):
        return self.name


class GroupMembership(UUIDModel):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships", verbose_name=_("group"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="group_memberships", verbose_name=_("member"))
    is_admin = models.BooleanField(_("group admin"), default=False, help_text=_("Can schedule events for this group and add or remove its members -- like a management position on a team."))
    must_respond = models.BooleanField(
        _("responds to invites"),
        default=True,
        help_text=_("Whether this person is expected to answer the group's event invites. Untick it for e.g. the admins of a training group: they see the events, but get no attendance row to answer."),
    )

    class Meta:
        verbose_name = _("group membership")
        verbose_name_plural = _("group memberships")
        ordering = ["group", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["group", "member"], name="unique_member_per_group"),
        ]

    def __str__(self):
        return f"{self.group} - {self.member}"


class ClubMembership(UUIDModel):
    """A person playing/paying in one season. Only members have these: guardians and
    staff are derived (see ``MemberQuerySet``), so a parent who also plays simply has
    a membership and is a guardian too."""

    class StatusChoices(models.TextChoices):
        ACTIVE = "active", _("active")
        PENDING = "pending", _("pending")
        LAPSED = "lapsed", _("lapsed")
        CANCELLED = "cancelled", _("cancelled")

    class FeeStatus(models.TextChoices):
        UNPAID = "unpaid", _("unpaid")
        PAID = "paid", _("paid")
        PARTIALLY_PAID = "partially_paid", _("partially paid")
        WAIVED = "waived", _("waived")

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="club_memberships", verbose_name=_("member"))
    season = models.ForeignKey("events.Season", on_delete=models.PROTECT, related_name="memberships", verbose_name=_("season"))

    status = models.CharField(_("status"), max_length=250, choices=StatusChoices.choices, default=StatusChoices.PENDING)
    fee_status = models.CharField(_("fee status"), max_length=250, choices=FeeStatus.choices, default=FeeStatus.UNPAID)

    fee_amount = models.DecimalField(_("fee amount"), max_digits=10, decimal_places=2, default=Decimal("0.00"), blank=True)
    amount_paid = models.DecimalField(_("amount paid"), max_digits=10, decimal_places=2, default=Decimal("0.00"), blank=True, help_text=_("Kept in step with payments by the fee service; not hand-edited."))

    early_payment_deadline = models.DateField(_("early payment deadline"), blank=True, null=True)
    early_payment_discount = models.DecimalField(_("early payment discount"), max_digits=10, decimal_places=2, default=Decimal("0.00"), blank=True)

    signed_up_at = models.DateField(_("signed up at"), blank=True, null=True)
    activated_at = models.DateField(_("activated at"), blank=True, null=True)

    class Meta:
        verbose_name = _("club membership")
        verbose_name_plural = _("club memberships")
        ordering = ["-season__start_date", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["member", "season"], name="unique_membership_per_member_per_season"),
        ]
        indexes = [
            models.Index(fields=["season", "status"]),
        ]

    def __str__(self):
        return f"{self.season} - {self.member}"

    @property
    def open_requirement_count(self) -> int:
        """How many active onboarding requirements this membership hasn't resolved
        yet (completed or bypassed) -- see OnboardingRequirement's docstring for why
        this is separate from status/fee_status. One query per call; for a list of
        memberships, use ``members.services.onboarding.checklist_for`` per
        membership instead."""
        today = timezone.localdate()
        met = set(self.requirement_statuses.filter(Q(is_bypassed=True) | Q(is_complete=True) & (Q(valid_until__isnull=True) | Q(valid_until__gte=today))).values_list("requirement_id", flat=True))
        required = set(OnboardingRequirement.objects.filter(is_active=True).values_list("pk", flat=True))
        return len(required - met)

    @property
    def onboarding_complete(self) -> bool:
        return self.open_requirement_count == 0


class FeePayment(UUIDModel):
    """Money received against one membership's fee. Several may land on one
    membership: a family paying in two installments must not read as unpaid, and
    the part that did arrive has to be recorded somewhere. Not itself club-scoped
    -- its club is reached through ``membership``, same as DuePayment/Due."""

    class Method(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", _("bank transfer")
        CASH = "cash", _("cash")
        CARD = "card", _("card")
        OTHER = "other", _("other")

    membership = models.ForeignKey(ClubMembership, on_delete=models.CASCADE, related_name="payments", verbose_name=_("membership"))
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(_("method"), max_length=20, choices=Method.choices, default=Method.BANK_TRANSFER)
    reference = models.CharField(_("reference"), max_length=255, blank=True, help_text=_("Bank reference, transaction id — whatever lets you find this again."))
    paid_at = models.DateTimeField(_("paid at"), default=timezone.now)
    note = models.TextField(_("note"), blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="recorded_fee_payments", verbose_name=_("recorded by"))

    class Meta:
        verbose_name = _("fee payment")
        verbose_name_plural = _("fee payments")
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.membership} — {self.amount}"


class DuesInvoice(UUIDModel):
    membership = models.OneToOneField(ClubMembership, on_delete=models.CASCADE, related_name="dues_invoice", verbose_name=_("membership"))
    number = models.CharField(_("number"), max_length=255, blank=True)

    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2, help_text=_("The outstanding balance at the time this was sent — not re-read from the membership afterwards."))
    due_date = models.DateField(_("due date"))

    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    sent_to_email = models.EmailField(_("sent to"), blank=True)
    sent_to_guardian = models.BooleanField(_("sent to a parent/guardian"), default=False, help_text=_("The member had no email on file, so a parent/guardian's was used instead."))

    last_reminder_sent_at = models.DateTimeField(_("last reminder sent at"), null=True, blank=True)
    reminder_count = models.PositiveIntegerField(_("reminders sent"), default=0)

    class Meta:
        verbose_name = _("dues invoice")
        verbose_name_plural = _("dues invoices")
        ordering = ["-sent_at"]
        indexes = [
            # invoices_due_for_reminder's own club + sent_at__isnull filter --
            # unindexed before this.
            models.Index(fields=["sent_at"]),
        ]

    def __str__(self):
        return self.number or _("Unsent invoice for %(member)s") % {"member": self.membership.member}

    @property
    def is_paid(self) -> bool:
        return self.membership.fee_status == ClubMembership.FeeStatus.PAID

    @property
    def is_overdue(self) -> bool:
        return bool(self.sent_at) and not self.is_paid and self.due_date < timezone.now().date()

    def generate_number(self) -> str:
        """Next per-club invoice number for the current year: ``DUE-<year>-<seq>``.
        Same shape as shop.models.Invoice's numbering, duplicated rather than shared
        across the two apps — see that module's own numbering helpers."""
        prefix = f"DUE-{timezone.now().year}-"
        sequences = [int(suffix) for existing in DuesInvoice.objects.filter(number__startswith=prefix).values_list("number", flat=True) if (suffix := existing.removeprefix(prefix)).isdigit()]
        return f"{prefix}{max(sequences, default=0) + 1:05d}"

    def save(self, *args, **kwargs):
        if self.number:
            return super().save(*args, **kwargs)

        # Retrying on a numbering collision (two invoices allocated the same
        # sequence in the same instant) rather than locking: this only ever
        # fires once, on first send, so a rare retry is cheaper than a lock
        # held around every save.
        for attempt in range(5):
            self.number = self.generate_number()
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                self.number = ""
                if attempt == 4:
                    raise


def onboarding_document_path(instance: MemberRequirementStatus, filename: str) -> str:
    return f"onboarding/{instance.membership_id}/{filename}"


class OnboardingRequirement(UUIDModel):
    """A club-defined item every member must satisfy after signing up or renewing --
    e.g. "provide a medical certificate", "upload a photo".

    ``ClubMembership.fee_status`` is still driven by payment alone (see
    ``members.services.memberships.sync_fee_status``) and this never touches it -- a member
    reads as paid *and* still has an open checklist, both true at once. ``status``
    is different: paying in full only ever settles ``fee_status`` now -- it never
    flips ``status`` to ACTIVE by itself. The only path there is the deliberately
    manual one, ``members.services.memberships.activate``, run
    by an admin, which additionally requires every blocking
    requirement to be resolved first. Nothing flips status automatically just
    because the fee cleared or the last checklist item was ticked (checklist actions
    aren't even admin-gated); activation is always that one deliberate admin step,
    so a membership can be fully paid *and* fully checked off and still sit PENDING
    until someone actually clicks Approve.

    ``blocked_event_kinds`` is what makes a specific requirement matter before that
    point: a club can decide e.g. a medical certificate blocks GAME invitations/
    selection but not TRAINING ones, so a provisionally-rostered member (a roster spot
    before activation) can still be invited to practice
    while their paperwork is outstanding. Empty means "informational only" -- open or
    not, it never blocks anything. Stored as a plain list of ``events.models.Event.
    EventKind`` values (not a FK/enum at the DB layer) specifically to avoid a
    club -> events import cycle (events already imports club for Event.club); ``members.services.onboarding``
    is what actually validates against EventKind.

    ``MemberRequirementStatus`` tracks completion per ``ClubMembership`` (so a fresh
    checklist starts each season, matching how membership itself is season-scoped).
    """

    class Kind(models.TextChoices):
        DOCUMENT = "document", _("Document")
        QUESTION = "question", _("Question")

    class QuestionType(models.TextChoices):
        TEXT = "text", _("Short text")
        TEXTAREA = "textarea", _("Long text")
        NUMBER = "number", _("Number")
        DATE = "date", _("Date")
        CHOICE = "choice", _("Single choice")
        MULTICHOICE = "multichoice", _("Multiple choice")
        YES_NO = "yes_no", _("Yes / no")

    name = models.CharField(_("name"), max_length=100, unique=True)
    description = models.TextField(_("description"), blank=True, help_text=_("Shown to staff on the member's checklist."))
    #: A document (the default: something received, optionally with a file) or a question (extra information, answered with a value).
    kind = models.CharField(_("kind"), max_length=10, choices=Kind.choices, default=Kind.DOCUMENT)
    #: Questions only: what kind of answer is expected, its choices (a list of strings or ``{"value", "label"}`` objects) and, for a number, its range.
    question_type = models.CharField(_("question type"), max_length=12, choices=QuestionType.choices, blank=True)
    options = models.JSONField(_("options"), null=True, blank=True)
    min_value = models.DecimalField(_("minimum value"), max_digits=15, decimal_places=4, null=True, blank=True)
    max_value = models.DecimalField(_("maximum value"), max_digits=15, decimal_places=4, null=True, blank=True)
    requires_document = models.BooleanField(_("requires a document"), default=False, help_text=_("Staff can attach a file (e.g. the certificate itself) when marking this complete."))
    blocked_event_kinds = models.JSONField(_("blocks selection for"), default=list, blank=True, help_text=_("Event kinds a member can't be invited to or selected for while this is open. Empty means purely informational."))
    is_active = models.BooleanField(_("active"), default=True, help_text=_("Inactive requirements no longer apply to new memberships, but existing statuses are kept."))
    #: Optional expiry: a completed item stays valid this many months (e.g. a medical certificate valid for 12). Blank = never expires.
    #: When it runs out the item counts as open again, exactly like a missing one.
    valid_for_months = models.PositiveSmallIntegerField(_("valid for (months)"), null=True, blank=True)
    #: How many days before it expires the member (or their guardians) is reminded.
    reminder_days_before = models.PositiveSmallIntegerField(_("remind before expiry (days)"), default=30)

    class Meta:
        verbose_name = _("onboarding requirement")
        verbose_name_plural = _("onboarding requirements")
        # Alphabetical, not a configurable sequence: every active requirement
        # blocks equally and there's no set order to complete them in, so
        # ordering here is purely for a stable, predictable listing.
        ordering = ["name"]

    def __str__(self):
        return self.name


class MemberRequirementStatus(UUIDModel):
    """Whether one ``ClubMembership`` has satisfied one ``OnboardingRequirement``,
    this season. Not itself club-scoped -- its club is reached through ``membership``,
    same reasoning as ``FeePayment`` above."""

    membership = models.ForeignKey(ClubMembership, on_delete=models.CASCADE, related_name="requirement_statuses", verbose_name=_("membership"))
    requirement = models.ForeignKey(OnboardingRequirement, on_delete=models.CASCADE, related_name="statuses", verbose_name=_("requirement"))
    is_complete = models.BooleanField(_("complete"), default=False)
    #: Distinct from is_complete -- "confirmed, not needed for this person" (e.g. they
    #: already have a recent photo on file) reads differently from "actually received"
    #: on a checklist/audit, even though both equally stop this item from blocking
    #: anything (see members.services.onboarding.checklist_for). Mutually exclusive with
    #: is_complete in practice (mark_bypassed/mark_complete each clear the other).
    is_bypassed = models.BooleanField(_("bypassed"), default=False)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("completed by"))
    document = models.FileField(_("document"), storage=get_private_storage, upload_to=onboarding_document_path, blank=True, help_text=_("Stored privately -- readable only through this member's own page, never a direct link."))
    #: A question's answer, cleaned for its type (text, a number as a string, an ISO date, a list for a multiple choice, a boolean for yes/no). Null for documents and while open.
    answer = models.JSONField(_("answer"), null=True, blank=True, encoder=DjangoJSONEncoder)
    note = models.TextField(_("note"), blank=True, help_text=_("Staff-only, e.g. how or when this was received."))
    #: The last day the completed item is valid (from the requirement's ``valid_for_months`` unless staff set it). Blank = no expiry.
    valid_until = models.DateField(_("valid until"), null=True, blank=True)
    #: The last reminder sent about this item (``expiring`` / ``expired`` / ``missing``), so the daily sweep never repeats itself.
    last_reminder_at = models.DateTimeField(_("last reminder at"), null=True, blank=True)
    last_reminder_kind = models.CharField(_("last reminder kind"), max_length=20, blank=True)

    class Meta:
        verbose_name = _("member requirement status")
        verbose_name_plural = _("member requirement statuses")
        constraints = [
            models.UniqueConstraint(fields=["membership", "requirement"], name="unique_requirement_status_per_membership"),
        ]

    def __str__(self):
        return f"{self.membership} — {self.requirement}"


class AuditEntry(UUIDModel):
    """Append-only history of changes to members and everything that hangs off them
    (families, groups, memberships, payments, onboarding). Written explicitly by
    ``members.services.audit.record``; never edited or deleted by the app.

    ``member`` is the person the change is about, so a member's whole history is one
    indexed query; ``target_*`` identifies the exact row (kept as plain text so the
    entry outlives a deleted target). ``changes`` maps field -> ``{"from": .., "to": ..}``."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("actor"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    member = models.ForeignKey(Member, verbose_name=_("member"), null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries")
    action = models.CharField(_("action"), max_length=60, db_index=True)
    target_type = models.CharField(_("target type"), max_length=40, blank=True)
    target_id = models.CharField(_("target id"), max_length=64, blank=True)
    target_label = models.CharField(_("target"), max_length=255, blank=True)
    changes = models.JSONField(_("changes"), default=dict, blank=True)

    class Meta:
        verbose_name = _("audit entry")
        verbose_name_plural = _("audit entries")
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["member", "-created"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        return f"{self.created:%Y-%m-%d %H:%M} {self.action}"


class ImportJob(UUIDModel):
    """A member import running in the background (``members.tasks.run_member_import``): the rows to create go in,
    progress and the per-row results come out. The rows are dropped when it is done."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("queued")
        RUNNING = "running", _("running")
        DONE = "done", _("done")
        FAILED = "failed", _("failed")

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("created by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    season_id = models.UUIDField(_("season"), null=True, blank=True)
    rows = models.JSONField(_("rows"), default=list, blank=True)
    total = models.PositiveIntegerField(_("rows to import"), default=0)
    processed = models.PositiveIntegerField(_("rows processed"), default=0)
    result = models.JSONField(_("result"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)

    class Meta:
        verbose_name = _("import job")
        verbose_name_plural = _("import jobs")
        ordering = ["-created"]

    def __str__(self):
        return f"import {self.pk} ({self.status})"
