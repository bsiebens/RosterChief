"""Platform billing its clubs (public schema). Entitlements live in ``features``; this adds money on top of
``features.Plan`` and ``features.Subscription`` without changing how entitlements resolve.

Currency is EUR; there is no VAT calculation (a club's VAT id is printed on its invoice when set).
"""

import datetime
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel

MAX_DAYS_PER_MONTH = 28  # lead and grace may not exceed the period length, measured conservatively


class PlanTerms(UUIDModel):
    """The commercial clock of a plan: how long a period is and when it is invoiced and chased."""

    plan = models.OneToOneField("features.Plan", on_delete=models.CASCADE, related_name="terms")
    duration_months = models.PositiveSmallIntegerField(_("period length (months)"), default=12, validators=[MinValueValidator(1)])
    renewal_lead_days = models.PositiveSmallIntegerField(_("invoice this many days before the period starts"), default=30)
    grace_days = models.PositiveSmallIntegerField(_("grace days, counted from the period start"), default=30)
    is_trial = models.BooleanField(_("trial plan"), default=False, help_text=_("Trial plans are free and convert to another plan when the trial ends."))

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
    trial_ends_at = models.DateField(_("trial ends"), null=True, blank=True)
    post_trial_plan = models.ForeignKey("features.Plan", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        verbose_name = _("club billing")
        verbose_name_plural = _("club billing")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(trial_ends_at__isnull=True, post_trial_plan__isnull=True) | models.Q(trial_ends_at__isnull=False, post_trial_plan__isnull=False),
                name="trial_fields_set_together",
            )
        ]

    def __str__(self) -> str:
        return f"Billing of {self.club}"


class Due(UUIDModel):
    """One billing period of a club. Plan, amount and dates are frozen when the period opens."""

    UNPAID, PARTIAL, PAID, WAIVED, CANCELLED = "unpaid", "partial", "paid", "waived", "cancelled"
    STATUS_CHOICES = [(UNPAID, _("Unpaid")), (PARTIAL, _("Partially paid")), (PAID, _("Paid")), (WAIVED, _("Waived")), (CANCELLED, _("Cancelled"))]
    OWING = (UNPAID, PARTIAL)

    club = models.ForeignKey(settings.TENANT_MODEL, on_delete=models.CASCADE, related_name="dues")
    plan = models.ForeignKey("features.Plan", on_delete=models.PROTECT, related_name="dues")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    period_start = models.DateField()
    period_end = models.DateField()
    grace_until = models.DateField()
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
    def balance(self) -> Decimal:
        return self.amount - self.amount_paid

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
            self.period_end = self.period_start + relativedelta(months=terms.duration_months) - datetime.timedelta(days=1)
            self.grace_until = self.period_start + datetime.timedelta(days=terms.grace_days)
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


class Invoice(UUIDModel):
    """Only the number and send tracking are stored; the document is rendered from the Due's frozen values."""

    EMAIL, MANUAL = "email", "manual"

    due = models.OneToOneField(Due, on_delete=models.CASCADE, related_name="invoice")
    number = models.CharField(max_length=32, unique=True)
    issued_at = models.DateField()
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_method = models.CharField(max_length=10, choices=[(EMAIL, "Email"), (MANUAL, "Manual")], blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    @property
    def needs_sending(self) -> bool:
        return self.sent_at is None and self.due.amount != 0

    def __str__(self) -> str:
        return self.number
