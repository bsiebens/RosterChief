import datetime

from django.core.validators import RegexValidator
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
    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True, help_text=_("Drives subdomain / path resolution (e.g. ajax-united.rosterchief.app)."))

    logo = models.ImageField(_("logo"), upload_to=club_logo_path, blank=True, help_text=_("Shown on the club's own pages. Without one, the club's initials are used."))
    primary_color = models.CharField(
        _("primary colour"),
        max_length=7,
        blank=True,
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", _("Enter a colour as a hex value, e.g. #1e40af."))],
        help_text=_("Hex colour for buttons and links on the club's pages, e.g. #1e40af."),
    )

    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True, help_text=_("Archived clubs stop resolving on their subdomain, but their data is retained."))

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
    def initials(self) -> str:
        """Stand-in for a missing logo. Never the RosterChief mark — that would
        pass our branding off as the club's own."""
        return "".join(word[0] for word in self.name.split()[:2]).upper()

    @property
    def primary_content_color(self) -> str:
        """Readable text colour to sit *on* ``primary_color``.

        A club picking a pale yellow would otherwise get white-on-yellow buttons.
        Relative luminance per WCAG, with its 0.179 threshold for black vs white.
        """
        if not self.primary_color:
            return ""

        def channel(value: int) -> float:
            fraction = value / 255
            return fraction / 12.92 if fraction <= 0.04045 else ((fraction + 0.055) / 1.055) ** 2.4

        red, green, blue = (channel(int(self.primary_color[index : index + 2], 16)) for index in (1, 3, 5))
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


class ClubMembership(ClubScopedModel):
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

    license = models.CharField(_("license"), max_length=250, blank=True)
    status = models.CharField(_("status"), max_length=250, choices=StatusChoices.choices, default=StatusChoices.PENDING)
    fee_status = models.CharField(_("fee status"), max_length=250, choices=FeeStatus.choices, default=FeeStatus.UNPAID)

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

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("season",))


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
