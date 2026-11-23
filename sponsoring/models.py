from django.core.exceptions import ValidationError
from django.core.files.images import get_image_dimensions
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


def sponsor_logo_path(instance, filename):
    return f"sponsors/{instance.pk}/{filename}"


class Sponsor(UUIDModel):
    name = models.CharField(_("name"), max_length=255)
    logo = models.FileField(
        _("logo"),
        upload_to=sponsor_logo_path,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg", "webp"])],
    )
    logo_width = models.PositiveIntegerField(_("logo width"), null=True, blank=True, editable=False)
    logo_height = models.PositiveIntegerField(_("logo height"), null=True, blank=True, editable=False)
    url = models.URLField(_("URL"), blank=True)
    start_date = models.DateField(_("start date"), default=timezone.localdate)
    end_date = models.DateField(_("end date"), null=True, blank=True)
    #: Set by the daily job when an installment stayed unpaid past the agreement's grace period; cleared when the arrears are paid. A suspended
    #: sponsor is not active (not shown in the app or on public pages) although its dates still run.
    suspended_at = models.DateTimeField(_("suspended at"), null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("sponsor")
        verbose_name_plural = _("sponsors")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": _("End date can't be before the start date.")})

    def save(self, *args, **kwargs):
        if not self.logo:
            self.logo_width, self.logo_height = None, None
        elif not self.logo._committed:
            self.logo_width, self.logo_height = get_image_dimensions(self.logo)
        update_fields = kwargs.get("update_fields")
        if update_fields and "logo" in update_fields:
            kwargs["update_fields"] = {*update_fields, "logo_width", "logo_height"}
        super().save(*args, **kwargs)

    @classmethod
    def active(cls, today=None):
        today = today or timezone.localdate()
        return cls.objects.filter(start_date__lte=today, suspended_at__isnull=True).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))


class SponsorAgreement(UUIDModel):
    """What a sponsor pays: the contact it is invoiced at, the fixed-date installments (``SponsorInstallment``) and the grace period."""

    sponsor = models.OneToOneField(Sponsor, on_delete=models.CASCADE, related_name="agreement")
    contact_name = models.CharField(_("contact name"), max_length=150)
    contact_email = models.EmailField(_("contact email"))
    #: What the invoices say, for example "Sponsorship 2026-27"; the sponsor's name and the installment number are added.
    description = models.CharField(_("description"), max_length=150, blank=True)
    #: Each installment is invoiced this many days before its date.
    lead_days = models.PositiveSmallIntegerField(_("invoice days before the date"), default=30)
    #: The sponsor becomes inactive when an installment is unpaid more than this many days past its date.
    grace_days = models.PositiveSmallIntegerField(_("grace period in days"), default=14)
    #: Heads-up: this many days before the grace period ends the contact is emailed and the sponsor managers are told (once per installment). 0: off.
    warn_days = models.PositiveSmallIntegerField(_("heads-up days before suspension"), default=3)
    #: Manual override: while on (and not past ``keep_active_until``) the daily job does not suspend the sponsor, and a suspended one is reactivated.
    keep_active = models.BooleanField(_("keep active anyway"), default=False)
    keep_active_until = models.DateField(_("keep active until"), null=True, blank=True)
    #: When the contact was last mailed that the sponsor was paused / shown again (one mail per state change).
    suspension_notified_at = models.DateTimeField(null=True, blank=True, editable=False)
    reactivation_notified_at = models.DateTimeField(null=True, blank=True, editable=False)
    #: The login-less member the invoices are addressed to (made from the contact); reset when the contact changes.
    payer = models.ForeignKey("members.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = _("sponsor agreement")
        verbose_name_plural = _("sponsor agreements")

    def __str__(self):
        return f"{self.sponsor}"

    def keeps_active(self, today=None) -> bool:
        today = today or timezone.localdate()
        return self.keep_active and (self.keep_active_until is None or self.keep_active_until >= today)


class SponsorInstallment(UUIDModel):
    """One payment of an agreement, due on a fixed date. Its invoice is a shop charge (``order``); its status follows that order's payments."""

    agreement = models.ForeignKey(SponsorAgreement, on_delete=models.CASCADE, related_name="installments")
    due_date = models.DateField(_("due date"))
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)
    order = models.ForeignKey("shop.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="sponsor_installments")
    #: When the suspension heads-up for this installment went out (once).
    warned_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("sponsor installment")
        verbose_name_plural = _("sponsor installments")
        ordering = ["due_date", "created"]

    def __str__(self):
        return f"{self.agreement} {self.due_date}"
