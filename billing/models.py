"""Platform billing its clubs (public schema). Entitlements live in ``features``; this adds money on top of
``features.Plan`` and ``features.Subscription`` without changing how entitlements resolve.

Currency is EUR. Plan prices are NET; VAT (from ``BillingSettings``) is computed when a period opens and frozen on the Due.
"""

import datetime
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField

from rosterchief.base import UUIDModel

MAX_DAYS_PER_MONTH = 28  # lead and grace may not exceed the period length, measured conservatively

# EU member states (ISO 3166-1 alpha-2), used to decide on intra-community reverse charge.
EU_COUNTRIES = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split())


class BillingSettings(UUIDModel):
    """The platform's invoicing identity and VAT rate, stored as a single row (public schema)."""

    CACHE_SECONDS = 10

    legal_name = models.CharField(_("legal name"), max_length=255, blank=True)
    address = models.CharField(_("address"), max_length=255, blank=True)
    zip_code = models.CharField(_("zip code"), max_length=32, blank=True)
    city = models.CharField(_("city"), max_length=255, blank=True)
    country = CountryField(_("country"), blank=True)
    vat_id = models.CharField(_("VAT ID"), max_length=32, blank=True)
    iban = models.CharField(_("IBAN"), max_length=42, blank=True)
    bic = models.CharField(_("BIC"), max_length=11, blank=True)
    contact_email = models.EmailField(_("contact email"), blank=True)
    website = models.CharField(_("website"), max_length=255, blank=True)
    invoice_footer = models.TextField(_("invoice footer"), blank=True)
    payment_terms = models.TextField(_("payment terms"), blank=True)
    archive_after_days = models.PositiveSmallIntegerField(
        _("archive this many days after downgrading"),
        default=30,
        validators=[MaxValueValidator(365)],
        help_text=_("An unpaid club is first moved to the default plan; it is archived this many days later (0 = straight away)."),
    )
    vat_rate = models.DecimalField(_("VAT rate (%)"), max_digits=5, decimal_places=2, default=Decimal("21.00"), validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))])

    class Meta:
        verbose_name = _("billing settings")
        verbose_name_plural = _("billing settings")

    def __str__(self) -> str:
        return f"Billing settings ({self.legal_name or 'unnamed'})"

    @classmethod
    def _cache_key(cls) -> str:
        return "billing:settings"

    @classmethod
    def current(cls):
        """The single row (created on first use). Cached briefly; ``save`` refreshes the cache."""
        from django.core.cache import cache

        row = cache.get(cls._cache_key())
        if row is None:
            row = cls.objects.first() or cls.objects.create()
            cache.set(cls._cache_key(), row, cls.CACHE_SECONDS)
        return row

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from django.core.cache import cache

        cache.set(self._cache_key(), self, self.CACHE_SECONDS)


class PlanTerms(UUIDModel):
    """The commercial clock of a plan: how long a period is and when it is invoiced and chased."""

    plan = models.OneToOneField("features.Plan", on_delete=models.CASCADE, related_name="terms")
    duration_months = models.PositiveSmallIntegerField(_("period length (months)"), default=12, validators=[MinValueValidator(1)])
    renewal_lead_days = models.PositiveSmallIntegerField(_("invoice this many days before the period starts"), default=30)
    grace_days = models.PositiveSmallIntegerField(_("grace days, counted from the period start or from the day it was opened, whichever is later"), default=30)

    class Meta:
        verbose_name = _("plan terms")
        verbose_name_plural = _("plan terms")

    def __str__(self) -> str:
        return f"{self.plan}: {self.duration_months} month(s)"

    def clean(self):
        limit = self.duration_months * MAX_DAYS_PER_MONTH
        if self.renewal_lead_days >= limit:
            raise ValidationError({"renewal_lead_days": _("The invoice lead time must be shorter than the period.")})
        if self.grace_days > limit:
            raise ValidationError({"grace_days": _("The grace period cannot be longer than the period.")})


class PlanPrice(UUIDModel):
    """A dated price. A rate change is always a new row, never an edit; the latest ``active_from <= day`` applies."""

    plan = models.ForeignKey("features.Plan", on_delete=models.CASCADE, related_name="prices")
    active_from = models.DateField(_("active from"))
    amount = models.DecimalField(_("amount (EUR)"), max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])

    class Meta:
        verbose_name = _("plan price")
        verbose_name_plural = _("plan prices")
        ordering = ["plan", "-active_from"]
        constraints = [models.UniqueConstraint(fields=["plan", "active_from"], name="uniq_plan_price_date")]

    def __str__(self) -> str:
        return f"{self.plan}: {self.amount} from {self.active_from}"


class ClubBilling(UUIDModel):
    """How a club is billed. The plan itself is the club's current ``features.Subscription``."""

    club = models.OneToOneField(settings.TENANT_MODEL, on_delete=models.CASCADE, related_name="billing")
    auto_renew = models.BooleanField(_("auto renew"), default=True)
    auto_archive = models.BooleanField(_("auto archive"), default=True, help_text=_("Archive the club when a period stays unpaid past its grace."))
    notes = models.TextField(_("notes"), blank=True)
    trial_ends_at = models.DateField(_("trial ends"), null=True, blank=True, help_text=_("Set while the club's free trial period is running or has not been followed by a paid period yet."))
    downgraded_at = models.DateTimeField(_("downgraded at"), null=True, blank=True, help_text=_("Set while a period is overdue: the club only gets the default plan's entitlements."))

    class Meta:
        verbose_name = _("club billing")
        verbose_name_plural = _("club billing")

    def __str__(self) -> str:
        return f"Billing of {self.club}"


def grace_until_for(period_start: datetime.date, grace_days: int, opened_on: datetime.date | None = None) -> datetime.date:
    """``max(period_start, opened_on) + grace_days``.

    A period opened ahead of time gets its grace counted from its start (unchanged behaviour). A period opened late (a
    lapsed club, a backdated start) still gives the club at least ``grace_days`` after the invoice is issued.
    """
    opened_on = opened_on or timezone.localdate()
    return max(period_start, opened_on) + datetime.timedelta(days=grace_days)


class Due(UUIDModel):
    """One billing period of a club. Plan, amount and dates are frozen when the period opens."""

    UNPAID, PARTIAL, PAID, WAIVED, CANCELLED = "unpaid", "partial", "paid", "waived", "cancelled"
    STATUS_CHOICES = [(UNPAID, _("Unpaid")), (PARTIAL, _("Partially paid")), (PAID, _("Paid")), (WAIVED, _("Waived")), (CANCELLED, _("Cancelled"))]
    OWING = (UNPAID, PARTIAL)

    club = models.ForeignKey(settings.TENANT_MODEL, on_delete=models.CASCADE, related_name="dues")
    plan = models.ForeignKey("features.Plan", on_delete=models.PROTECT, related_name="dues")
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Net amount, excluding VAT.")
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    reverse_charge = models.BooleanField(default=False)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    period_start = models.DateField()
    period_end = models.DateField()  # derived from the plan's length on create unless given (trials)
    grace_until = models.DateField()  # derived on create, see grace_until_for
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=UNPAID)
    paid_at = models.DateTimeField(null=True, blank=True)
    is_trial = models.BooleanField(default=False)
    last_reminder_level = models.CharField(max_length=20, blank=True)
    last_reminder_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("due")
        verbose_name_plural = _("dues")
        ordering = ["-period_start"]
        constraints = [models.UniqueConstraint(fields=["club", "period_start"], name="uniq_due_per_club_period")]
        indexes = [models.Index(fields=["status", "grace_until"])]

    def __str__(self) -> str:
        return f"{self.club} {self.period_start}..{self.period_end} ({self.status})"

    @property
    def total(self) -> Decimal:
        """Gross amount owed: net plus VAT. Everything about settling is measured against this."""
        return self.amount + self.vat_amount

    @property
    def balance(self) -> Decimal:
        return self.total - self.amount_paid

    @property
    def is_owing(self) -> bool:
        return self.status in self.OWING

    def is_overdue(self, today: datetime.date | None = None) -> bool:
        return self.is_owing and self.grace_until < (today or datetime.date.today())

    def is_in_grace(self, today: datetime.date | None = None) -> bool:
        today = today or datetime.date.today()
        return self.is_owing and self.period_start <= today <= self.grace_until

    def is_issued_ahead(self, today: datetime.date | None = None) -> bool:
        return self.is_owing and self.period_start > (today or datetime.date.today())

    def save(self, *args, **kwargs):
        if self._state.adding:
            terms = self.plan.terms
            if self.period_end is None:  # a trial passes its own end; otherwise the plan's period length applies
                self.period_end = self.period_start + relativedelta(months=terms.duration_months) - datetime.timedelta(days=1)
            if self.grace_until is None:
                self.grace_until = grace_until_for(self.period_start, terms.grace_days)
        super().save(*args, **kwargs)


class DuePayment(UUIDModel):
    BANK_TRANSFER, CARD, CASH, OTHER = "bank_transfer", "card", "cash", "other"
    METHOD_CHOICES = [(BANK_TRANSFER, _("Bank transfer")), (CARD, _("Card")), (CASH, _("Cash")), (OTHER, _("Other"))]

    due = models.ForeignKey(Due, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default=BANK_TRANSFER)
    reference = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField()
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["paid_at"]


class InvoiceSequence(models.Model):
    """The gapless counter behind automatic ``INV-YYYY-00001`` numbers, one row per year.

    Never derived from existing invoices, so custom numbers cannot skew it. Allocate with ``select_for_update`` inside the
    transaction that creates the invoice, so a rolled-back invoice does not burn a number.
    """

    year = models.PositiveSmallIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"INV-{self.year}: {self.last_number}"


class Invoice(UUIDModel):
    """Number, send tracking and a ``snapshot`` of issuer / bill-to / amounts taken at issue time; the PDF renders from it."""

    EMAIL, MANUAL = "email", "manual"

    due = models.OneToOneField(Due, on_delete=models.CASCADE, related_name="invoice")
    number = models.CharField(max_length=32, unique=True, editable=False)
    custom_number = models.BooleanField(default=False, help_text="Set by hand when issued; automatic numbers come from InvoiceSequence.")
    issued_at = models.DateField()
    snapshot = models.JSONField(default=dict, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_method = models.CharField(max_length=10, choices=[(EMAIL, "Email"), (MANUAL, "Manual")], blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    @property
    def needs_sending(self) -> bool:
        return self.sent_at is None and self.due.total != 0

    def __str__(self) -> str:
        return self.number
