import datetime

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from clubmanager.base import ClubScopedModel, UUIDModel, unique_slugify, validate_club_scope
from members.models import Member


class ClubManager(models.Manager):
    def current(self):
        """Return the club for the active tenant context, if any."""
        from .tenancy import get_current_club

        return get_current_club()

    def active(self):
        return self.filter(archived_at__isnull=True)

    def archived(self):
        return self.filter(archived_at__isnull=False)


class Club(UUIDModel):
    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True, help_text=_("Drives subdomain / path resolution (e.g. ajax-united.clubmanager.app)."))

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
