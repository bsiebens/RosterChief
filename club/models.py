import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel, unique_slugify, validate_club_scope


class ClubManager(models.Manager):
    def current(self):
        """Return the club for the active tenant context, if any."""
        from .tenancy import get_current_club

        return get_current_club()

    def active(self):
        return self.filter(archived_at__isnull=True)

    def archived(self):
        return self.filter(archived_at__isnull=False)


def club_logo_path(instance: Club, filename: str) -> str:
    return f"clubs/{instance.slug}/{filename}"


class Club(UUIDModel):
    class SportType(models.TextChoices):
        """Which sport this club plays. Only two options for now -- expand this as
        more sport-specific competition fetchers (see events.competition) are added."""

        ICE_HOCKEY = "ice_hockey", _("Ice hockey")
        OTHER = "other", _("Other")

    name = models.CharField(_("name"), max_length=255)
    legal_name = models.CharField(_("legal name"), max_length=255, blank=True, help_text=_("Full registered name (e.g. including a legal form like VZW/ASBL), used on official documents. Falls back to club name if blank."))
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True, help_text=_("Drives subdomain / path resolution (e.g. ajax-united.rosterchief.app)."))
    contact_email = models.EmailField(
        _("contact email"),
        blank=True,
        help_text=_("The club's public address, shown to people the club writes to or asks to get in touch -- e.g. a parent claiming a child. Falls back to nothing being shown at all, so it's worth setting."),
    )

    logo = models.FileField(
        _("logo"),
        upload_to=club_logo_path,
        blank=True,
        # A plain FileField, not ImageField: Pillow (which ImageField validates through)
        # cannot read SVGs, and club crests are commonly vector logos.
        validators=[FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg", "gif", "webp", "svg"])],
        help_text=_("Shown on the club's own pages. Without one, the club's initials are used."),
    )
    primary_color = models.CharField(
        _("primary colour"),
        max_length=7,
        blank=True,
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", _("Enter a colour as a hex value, e.g. #1e40af."))],
        help_text=_("Hex colour for buttons and links on the club's pages, e.g. #1e40af."),
    )

    secondary_color = models.CharField(
        _("secondary colour"),
        max_length=7,
        blank=True,
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", _("Enter a colour as a hex value, e.g. #be185d."))],
        help_text=_("Hex colour for highlights on the club's pages, e.g. avatar initials. Defaults to the theme's secondary colour."),
    )

    sport_type = models.CharField(
        _("sport"),
        max_length=20,
        choices=SportType.choices,
        default=SportType.OTHER,
        help_text=_("Which sport this club plays -- determines which competitions and score fetchers are relevant to it."),
    )

    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True, help_text=_("Archived clubs stop resolving on their subdomain, but their data is retained."))

    season_start = models.DateField(
        _("season start"),
        default=datetime.date(2000, 8, 1),
        help_text=_("Which day of the year a season begins — only the month and day are used, the year is ignored."),
    )
    season_duration_months = models.PositiveSmallIntegerField(
        _("season duration (months)"),
        default=12,
        validators=[MinValueValidator(1), MaxValueValidator(24)],
        help_text=_("How many months a season lasts, counted from its start date."),
    )

    objects = ClubManager()

    class Meta:
        verbose_name = _("club")
        verbose_name_plural = _("clubs")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def official_name(self) -> str:
        """The name official documents (e.g. the referee payment form) should
        show -- `legal_name` when the club has set one, else the everyday `name`."""
        return self.legal_name or self.name

    @property
    def initials(self) -> str:
        """Stand-in for a missing logo. Never the RosterChief mark — that would
        pass our branding off as the club's own."""
        return "".join(word[0] for word in self.name.split()[:2]).upper()

    @property
    def primary_content_color(self) -> str:
        """Readable text colour to sit *on* ``primary_color``. See ``_content_color_for``."""
        return self._content_color_for(self.primary_color)

    @property
    def secondary_content_color(self) -> str:
        """Readable text colour to sit *on* ``secondary_color``. See ``_content_color_for``."""
        return self._content_color_for(self.secondary_color)

    @staticmethod
    def _content_color_for(hex_color: str) -> str:
        """Black or white, whichever reads on ``hex_color``.

        A club picking a pale yellow would otherwise get white-on-yellow buttons.
        Relative luminance per WCAG, with its 0.179 threshold for black vs white.
        """
        if not hex_color:
            return ""

        def channel(value: int) -> float:
            fraction = value / 255
            return fraction / 12.92 if fraction <= 0.04045 else ((fraction + 0.055) / 1.055) ** 2.4

        red, green, blue = (channel(int(hex_color[index : index + 2], 16)) for index in (1, 3, 5))
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue

        return "#000000" if luminance > 0.179 else "#ffffff"

    def archive(self):
        """Soft-delete: the club stops resolving, but nothing is destroyed.

        Clubs are never hard-deleted — a club with any data cannot be removed
        anyway (ClubMembership PROTECTs its Season), and financial records must
        be retained.
        """
        if not self.is_archived:
            self.archived_at = timezone.now()
            self.save(update_fields=["archived_at"])

    def restore(self):
        if self.is_archived:
            self.archived_at = None
            self.save(update_fields=["archived_at"])


def sponsor_logo_path(instance: Sponsor, filename: str) -> str:
    return f"clubs/{instance.club.slug}/sponsors/{instance.pk}/{filename}"


class Sponsor(ClubScopedModel):
    name = models.CharField(_("name"), max_length=255)
    logo = models.FileField(
        _("logo"),
        upload_to=sponsor_logo_path,
        blank=True,
        # A plain FileField, not ImageField: same reasoning as Club.logo -- a
        # sponsor's own logo is just as commonly a vector file, and ImageField's
        # Pillow validation can't read those.
        validators=[FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg", "gif", "webp", "svg"])],
    )
    # Not user-editable: recomputed from the logo file itself on every save, same reasoning
    # NewsPhoto/TeamPhoto don't need this -- FileField (not ImageField) means Django never
    # populates width/height on its own. The public API exposes these so a consumer can lay
    # out a sponsor strip without waiting on the image to load.
    logo_width = models.PositiveIntegerField(_("logo width"), null=True, blank=True, editable=False)
    logo_height = models.PositiveIntegerField(_("logo height"), null=True, blank=True, editable=False)
    url = models.URLField(_("URL"), blank=True, help_text=_("The sponsor's own website, if they have one."))

    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"), null=True, blank=True, help_text=_("Leave blank to keep this sponsor active indefinitely once it starts."))

    class Meta:
        verbose_name = _("sponsor")
        verbose_name_plural = _("sponsors")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.end_date is not None and self.start_date is not None and self.end_date < self.start_date:
            raise ValidationError({"end_date": _("End date can't be before the start date.")})

    def save(self, *args, **kwargs):
        # Deferred: club.services (via its __init__) imports back from club.models, so a
        # module-level import here would be circular.
        from club.services.images import get_image_dimensions

        self.logo_width, self.logo_height = get_image_dimensions(self.logo) if self.logo else (None, None)
        super().save(*args, **kwargs)


class Season(ClubScopedModel):
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("season")
        verbose_name_plural = _("seasons")
        constraints = [
            models.UniqueConstraint(fields=["club", "start_date", "end_date"], name="unique_season_dates_per_club"),
        ]

    @property
    def name(self):
        """Short label built from the start/end years, e.g. "25-26"."""
        return f"{self.start_date:%y}-{self.end_date:%y}"

    @classmethod
    def get_current(cls, date: datetime.date | None = None):
        """Return the current club's season covering ``date`` (today by default)."""
        if date is None:
            date = timezone.now().date()

        return cls.objects.current_club().filter(start_date__lte=date, end_date__gte=date).first()

    @classmethod
    def covering(cls, club, date: datetime.date):
        """Return ``club``'s season covering ``date`` (no tenant context needed)."""
        return cls.objects.filter(club=club, start_date__lte=date, end_date__gte=date).first()

    @classmethod
    def next_after(cls, club, date: datetime.date):
        """Return ``club``'s soonest season starting after ``date`` (no tenant
        context needed) -- the season that follows the one covering ``date``."""
        return cls.objects.filter(club=club, start_date__gt=date).order_by("start_date").first()


class ClubMembership(ClubScopedModel):
    class Kind(models.TextChoices):
        MEMBER = "member", _("member")
        GUARDIAN = "guardian", _("guardian")

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

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="member_of", verbose_name=_("member"))
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="memberships", verbose_name=_("season"))

    kind = models.CharField(
        _("kind"),
        max_length=20,
        choices=Kind.choices,
        default=Kind.MEMBER,
        help_text=_("A guardian is attached to the club only as a parent of a member -- they hold the login, but don't count as a member themselves and owe no fee. A parent who also plays is a member."),
    )

    license = models.CharField(_("license"), max_length=250, blank=True)
    status = models.CharField(_("status"), max_length=250, choices=StatusChoices.choices, default=StatusChoices.PENDING)
    fee_status = models.CharField(_("fee status"), max_length=250, choices=FeeStatus.choices, default=FeeStatus.UNPAID)

    fee_amount = models.DecimalField(_("fee amount"), max_digits=10, decimal_places=2, default=Decimal("0.00"), blank=True)
    amount_paid = models.DecimalField(_("amount paid"), max_digits=10, decimal_places=2, default=Decimal("0.00"), blank=True, help_text=_("Kept in step with payments by the fee service; not hand-edited."))

    signed_up_at = models.DateField(_("signed up at"), blank=True, null=True)
    activated_at = models.DateField(_("activated at"), blank=True, null=True)

    class Meta:
        verbose_name = _("club membership")
        verbose_name_plural = _("club memberships")
        ordering = ["club", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["club", "member", "season"], name="unique_member_per_club_per_season"),
        ]

    def __str__(self):
        return f"{self.club} - {self.member}"

    @property
    def is_guardian(self) -> bool:
        """Attached to the club as a parent of a member, not as one themselves.

        Guardians are deliberately kept as ClubMembership rows rather than given
        their own model: everything that answers "is this person attached to this
        club" (tenancy scoping, group membership, the event audience) already
        reads through this table, and a second kind of link would need a parallel
        path through all of it. What changes is only who *counts* -- the member
        list, the fee list and every member KPI filter on ``kind``.
        """
        return self.kind == self.Kind.GUARDIAN

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("season",))
        # A guardian owes nothing -- they're not a member. Caught here rather than
        # silently zeroed on save so a mistaken import row says so out loud.
        if self.is_guardian and self.fee_amount:
            raise ValidationError({"fee_amount": _("A guardian doesn't hold a membership, so they can't owe a fee.")})


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


class ClubRole(ClubScopedModel):
    class Roles(models.TextChoices):
        ADMIN = "admin", _("admin")
        MEMBER = "member", _("member")
        EDITOR = "editor", _("editor")

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="roles", verbose_name=_("member"))
    role = models.CharField(_("role"), max_length=250, choices=Roles.choices, default=Roles.MEMBER)

    class Meta:
        verbose_name = _("club role")
        verbose_name_plural = _("club roles")
        ordering = ["club", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["club", "member"], name="unique_member_per_club"),
        ]

    def __str__(self):
        return f"{self.club} - {self.member}"
