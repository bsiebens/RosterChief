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
    invoice_number_pattern = models.CharField(_("invoice number pattern"), max_length=40, default="INV-{YYYY}-{NNNNN}", help_text=_("Placeholders {YYYY} {YY} {MM} and one sequence such as {NNNNN}; see billing.services.numbering."))
    credit_note_number_pattern = models.CharField(_("credit note number pattern"), max_length=40, default="CN-{YYYY}-{NNNNN}")
    auto_invoicing = models.BooleanField(
        _("automatic invoicing"),
        default=True,
        help_text=_(
            "On: the daily job opens each club's next period and issues its invoice with the next automatic number. "
            "Off: nothing is opened or invoiced automatically; platform admins are warned (weekly digest and a first alert per club and period) "
            "and open the period and create the invoice, with a typed number, by hand."
        ),
    )

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


CYCLE_NAMES = {1: _("Monthly"), 2: _("Every 2 months"), 3: _("Quarterly"), 6: _("Half-yearly"), 12: _("Yearly"), 24: _("Every 2 years")}


class PlanCycle(UUIDModel):
    """One way to be billed on a plan: how long a period is, when it is invoiced and chased, and (through ``PlanPrice``) what it costs.

    A plan can offer several (monthly, quarterly, yearly ...), each with its own dated prices; the platform picks one per club.
    ``position`` is the order they are offered in; ``is_active`` off means no club can be put on it any more (clubs already on it keep renewing)."""

    plan = models.ForeignKey("features.Plan", verbose_name=_("plan"), on_delete=models.CASCADE, related_name="cycles")
    label = models.CharField(_("name"), max_length=60, blank=True, help_text=_("Optional. Empty = named after its length (Monthly, Quarterly, Yearly ...)."))
    duration_months = models.PositiveSmallIntegerField(_("period length (months)"), default=12, validators=[MinValueValidator(1), MaxValueValidator(120)])
    renewal_lead_days = models.PositiveSmallIntegerField(_("invoice this many days before the period starts"), default=30)
    grace_days = models.PositiveSmallIntegerField(_("grace days, counted from the period start or from the day it was opened, whichever is later"), default=30)
    position = models.PositiveIntegerField(_("position"), default=0, db_index=True)
    is_active = models.BooleanField(_("active"), default=True, help_text=_("Inactive cycles cannot be chosen for a club any more. Clubs already on it keep renewing."))

    class Meta:
        verbose_name = _("plan billing cycle")
        verbose_name_plural = _("plan billing cycles")
        ordering = ["plan", "position", "duration_months"]
        constraints = [models.UniqueConstraint(fields=["plan", "duration_months"], name="uniq_plan_cycle_length")]

    def __str__(self) -> str:
        return f"{self.plan}: {self.display_name}"

    @property
    def display_name(self) -> str:
        if self.label.strip():
            return self.label.strip()
        name = CYCLE_NAMES.get(self.duration_months)
        return str(name) if name else f"Every {self.duration_months} months"

    def clean(self):
        limit = (self.duration_months or 1) * MAX_DAYS_PER_MONTH
        if self.renewal_lead_days >= limit:
            raise ValidationError({"renewal_lead_days": _("The invoice lead time must be shorter than the period.")})
        if self.grace_days > limit:
            raise ValidationError({"grace_days": _("The grace period cannot be longer than the period.")})


class PlanPrice(UUIDModel):
    """A dated price of one billing cycle of a plan. A rate change is always a new row, never an edit; the latest ``active_from <= day`` applies.

    Created without a ``cycle`` it goes to the plan's first cycle (a plan always has one)."""

    plan = models.ForeignKey("features.Plan", verbose_name=_("plan"), on_delete=models.CASCADE, related_name="prices")
    cycle = models.ForeignKey(PlanCycle, verbose_name=_("billing cycle"), on_delete=models.CASCADE, related_name="prices", null=True, blank=True)
    active_from = models.DateField(_("active from"))
    amount = models.DecimalField(_("amount (EUR)"), max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])

    class Meta:
        verbose_name = _("plan price")
        verbose_name_plural = _("plan prices")
        ordering = ["plan", "cycle", "-active_from"]
        constraints = [models.UniqueConstraint(fields=["cycle", "active_from"], name="uniq_cycle_price_date")]

    def __str__(self) -> str:
        return f"{self.cycle or self.plan}: {self.amount} from {self.active_from}"

    def clean(self):
        if self.cycle_id and self.plan_id and self.cycle.plan_id != self.plan_id:
            raise ValidationError({"cycle": _("The billing cycle belongs to another plan.")})

    def save(self, *args, **kwargs):
        if self.cycle_id is None and self.plan_id:
            self.cycle = ensure_default_cycle(self.plan)
        super().save(*args, **kwargs)


def ensure_default_cycle(plan) -> PlanCycle:
    """The plan's first cycle; a plan without any gets a yearly one (the long-standing default) so it can always be priced."""
    cycle = PlanCycle.objects.filter(plan=plan).order_by("position", "duration_months").first()
    return cycle or PlanCycle.objects.create(plan=plan)


class ClubBilling(UUIDModel):
    """How a club is billed. The plan itself is the club's current ``features.Subscription``."""

    club = models.OneToOneField(settings.TENANT_MODEL, verbose_name=_("club"), on_delete=models.CASCADE, related_name="billing")
    auto_renew = models.BooleanField(_("auto renew"), default=True)
    cycle = models.ForeignKey(
        PlanCycle, verbose_name=_("billing cycle"), on_delete=models.SET_NULL, null=True, blank=True, related_name="club_billings", help_text=_("How the club is billed on its plan (monthly, yearly ...). Empty = the plan's first cycle.")
    )
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

    club = models.ForeignKey(settings.TENANT_MODEL, verbose_name=_("club"), on_delete=models.CASCADE, related_name="dues")
    plan = models.ForeignKey("features.Plan", verbose_name=_("plan"), on_delete=models.PROTECT, related_name="dues")
    cycle = models.ForeignKey(
        PlanCycle, verbose_name=_("billing cycle"), on_delete=models.SET_NULL, null=True, blank=True, related_name="dues", help_text=_("The cycle this period was opened on (its figures are frozen on the period itself).")
    )
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2, help_text=_("Net amount, excluding VAT."))
    vat_rate = models.DecimalField(_("VAT rate"), max_digits=5, decimal_places=2, default=Decimal("0"))
    vat_amount = models.DecimalField(_("VAT amount"), max_digits=10, decimal_places=2, default=Decimal("0"))
    reverse_charge = models.BooleanField(_("reverse charge"), default=False)
    amount_paid = models.DecimalField(_("amount paid"), max_digits=10, decimal_places=2, default=Decimal("0"))
    period_start = models.DateField(
        _("period start"),
    )
    period_end = models.DateField(
        _("period end"),
    )  # derived from the plan's length on create unless given (trials)
    grace_until = models.DateField(
        _("grace until"),
    )  # derived on create, see grace_until_for
    status = models.CharField(_("status"), max_length=10, choices=STATUS_CHOICES, default=UNPAID)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    is_trial = models.BooleanField(_("is trial"), default=False)
    last_reminder_level = models.CharField(_("last reminder level"), max_length=20, blank=True)
    last_reminder_sent_at = models.DateTimeField(_("last reminder sent at"), null=True, blank=True)
    # Plan change (see billing.services.plan_change): the period is shortened and its figures prorated. The figures it was
    # opened with are kept here for reference; ``amount``/``vat_amount``/``period_end`` hold the prorated values.
    original_amount = models.DecimalField(_("original amount"), max_digits=10, decimal_places=2, null=True, blank=True)
    original_vat_amount = models.DecimalField(_("original VAT amount"), max_digits=10, decimal_places=2, null=True, blank=True)
    original_period_end = models.DateField(_("original period end"), null=True, blank=True)
    prorated_at = models.DateTimeField(_("prorated at"), null=True, blank=True)
    credit_transferred = models.DecimalField(
        _("credit transferred"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_("Part of the recorded payments that was moved to the club's credit (a plan change made the period cheaper, or cancelled it). amount_paid = sum(payments) - credit_transferred."),
    )

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
    def invoice(self):
        """The CURRENT (non-voided) invoice of this period, or ``None``. A period can have several invoices over time (a
        voided one is kept for the books and replaced), but at most one is active. Uses ``prefetch_related("invoices")``
        when that was done, otherwise one query per access."""
        for inv in self.invoices.all():
            if inv.voided_at is None:
                return inv
        return None

    @property
    def is_prorated(self) -> bool:
        return self.original_period_end is not None

    @property
    def original_total(self) -> Decimal | None:
        if self.original_amount is None:
            return None
        return self.original_amount + (self.original_vat_amount or Decimal("0"))

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
            if self.cycle_id is None:
                self.cycle = ensure_default_cycle(self.plan)
            terms = self.cycle
            if self.period_end is None:  # a trial passes its own end; otherwise the cycle's period length applies
                self.period_end = self.period_start + relativedelta(months=terms.duration_months) - datetime.timedelta(days=1)
            if self.grace_until is None:
                self.grace_until = grace_until_for(self.period_start, terms.grace_days)
        super().save(*args, **kwargs)


class DuePayment(UUIDModel):
    BANK_TRANSFER, CARD, CASH, OTHER, CREDIT = "bank_transfer", "card", "cash", "other", "credit"
    METHOD_CHOICES = [(BANK_TRANSFER, _("Bank transfer")), (CARD, _("Card")), (CASH, _("Cash")), (OTHER, _("Other")), (CREDIT, _("Club credit"))]
    MANUAL_METHODS = (BANK_TRANSFER, CARD, CASH, OTHER)  # ``credit`` is only ever created by the system, from the credit ledger

    due = models.ForeignKey(Due, verbose_name=_("due"), on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(_("method"), max_length=20, choices=METHOD_CHOICES, default=BANK_TRANSFER)
    reference = models.CharField(_("reference"), max_length=255, blank=True)
    paid_at = models.DateTimeField(
        _("paid at"),
    )
    note = models.TextField(_("note"), blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("recorded by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = _("due payment")
        verbose_name_plural = _("due payments")
        ordering = ["paid_at"]


class InvoiceSequence(models.Model):
    """The gapless counter behind automatic invoice numbers, one row per scope (``"2026"``, ``"2026-11"`` or ``"all"``,
    see ``billing.services.numbering``). Independent of the number pattern's text.

    Never derived from existing invoices, so custom numbers cannot skew it. Allocate with ``select_for_update`` inside the
    transaction that creates the invoice, so a rolled-back invoice does not burn a number.
    """

    scope = models.CharField(_("scope"), max_length=16, unique=True)
    last_number = models.PositiveIntegerField(_("last number"), default=0)

    class Meta:
        verbose_name = _("invoice sequence")
        verbose_name_plural = _("invoice sequences")

    def __str__(self) -> str:
        return f"invoices {self.scope}: {self.last_number}"


class Invoice(UUIDModel):
    """Number, send tracking and a ``snapshot`` of issuer / bill-to / amounts taken at issue time; the PDF renders from it."""

    EMAIL, MANUAL = "email", "manual"

    due = models.ForeignKey(Due, verbose_name=_("due"), on_delete=models.CASCADE, related_name="invoices")  # ``Due.invoice`` is the active one
    number = models.CharField(_("number"), max_length=32, unique=True, editable=False)
    custom_number = models.BooleanField(_("custom number"), default=False, help_text=_("Set by hand when issued; automatic numbers come from InvoiceSequence."))
    issued_at = models.DateField(
        _("issued at"),
    )
    snapshot = models.JSONField(_("snapshot"), default=dict, blank=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    sent_method = models.CharField(_("sent method"), max_length=10, choices=[(EMAIL, "Email"), (MANUAL, "Manual")], blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("sent by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    # Void and reissue (see billing.services.reissue): a voided invoice keeps its number forever, can no longer be sent,
    # and points to the invoice that replaced it. At most one invoice per period is active (constraint below).
    voided_at = models.DateTimeField(_("voided at"), null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("voided by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    void_reason = models.TextField(_("void reason"), blank=True)
    replaced_by = models.ForeignKey("self", verbose_name=_("replaced by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="replaced_invoices")

    class Meta:
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        constraints = [models.UniqueConstraint(fields=["due"], condition=models.Q(voided_at__isnull=True), name="uniq_active_invoice_per_due")]

    @property
    def is_voided(self) -> bool:
        return self.voided_at is not None

    @property
    def status(self) -> str:
        return "voided" if self.is_voided else "active"

    @property
    def needs_sending(self) -> bool:
        return self.voided_at is None and self.sent_at is None and self.due.total != 0

    @property
    def replaces(self):
        """The voided invoice this one replaced, if any."""
        return self.replaced_invoices.first()

    def __str__(self) -> str:
        return self.number


class CreditNoteSequence(models.Model):
    """The gapless counter behind automatic credit note numbers, one row per scope. Separate from invoices.

    Same rules as ``InvoiceSequence``: never derived from existing numbers, allocated with ``select_for_update`` inside
    the transaction that creates the credit note, so a rolled-back credit note does not burn a number.
    """

    scope = models.CharField(_("scope"), max_length=16, unique=True)
    last_number = models.PositiveIntegerField(_("last number"), default=0)

    class Meta:
        verbose_name = _("credit note sequence")
        verbose_name_plural = _("credit note sequences")

    def __str__(self) -> str:
        return f"credit notes {self.scope}: {self.last_number}"


class CreditNote(UUIDModel):
    """An immutable credit document against an invoice (the invoice itself is never edited). Amounts are positive and
    are the credited part; ``snapshot`` freezes issuer / bill-to / texts like an invoice snapshot."""

    PLAN_CHANGE, VOID = "plan_change", "void"
    KIND_CHOICES = [(PLAN_CHANGE, _("Plan change")), (VOID, _("Invoice voided"))]

    club = models.ForeignKey(settings.TENANT_MODEL, verbose_name=_("club"), on_delete=models.CASCADE, related_name="credit_notes")
    invoice = models.ForeignKey(Invoice, verbose_name=_("invoice"), on_delete=models.CASCADE, related_name="credit_notes")
    due = models.ForeignKey(Due, verbose_name=_("due"), on_delete=models.CASCADE, related_name="credit_notes")
    number = models.CharField(_("number"), max_length=32, unique=True, editable=False)
    issued_at = models.DateField(
        _("issued at"),
    )
    net = models.DecimalField(_("net"), max_digits=10, decimal_places=2)
    vat = models.DecimalField(_("VAT"), max_digits=10, decimal_places=2)
    total = models.DecimalField(_("total"), max_digits=10, decimal_places=2)
    vat_rate = models.DecimalField(_("VAT rate"), max_digits=5, decimal_places=2, default=Decimal("0"))
    reverse_charge = models.BooleanField(_("reverse charge"), default=False)
    reason = models.CharField(_("reason"), max_length=255)
    snapshot = models.JSONField(_("snapshot"), default=dict, blank=True)
    # ``void``: a document only, issued when an invoice is voided and reissued. It never touches the period, payments or
    # club credit (the replacement invoice covers the same period and amounts).
    kind = models.CharField(_("kind"), max_length=12, choices=KIND_CHOICES, default=PLAN_CHANGE)
    replacement_invoice = models.ForeignKey(Invoice, verbose_name=_("replacement invoice"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = _("credit note")
        verbose_name_plural = _("credit notes")
        ordering = ["-issued_at", "-created"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Credit notes are immutable.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.number


class ClubCreditEntry(UUIDModel):
    """Append-only ledger of a club's credit. ``credit_balance`` is the sum of ``amount``; no money is ever paid out.

    Positive: money the club paid that no longer has a period to pay for (a prorated period is cheaper than what was
    paid, or a period paid in advance was cancelled). Negative: credit used to pay an invoice (a ``credit`` DuePayment).
    """

    OVERPAYMENT, CANCELLED, APPLIED = "overpayment", "cancelled_period", "applied"
    KIND_CHOICES = [(OVERPAYMENT, _("Overpayment after proration")), (CANCELLED, _("Payment on a cancelled period")), (APPLIED, _("Applied to an invoice"))]

    club = models.ForeignKey(settings.TENANT_MODEL, verbose_name=_("club"), on_delete=models.CASCADE, related_name="credit_entries")
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)
    kind = models.CharField(_("kind"), max_length=20, choices=KIND_CHOICES)
    credit_note = models.ForeignKey(CreditNote, verbose_name=_("credit note"), null=True, blank=True, on_delete=models.SET_NULL, related_name="credit_entries")
    due = models.ForeignKey(Due, verbose_name=_("due"), null=True, blank=True, on_delete=models.SET_NULL, related_name="credit_entries")
    note = models.CharField(_("note"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("club credit entry")
        verbose_name_plural = _("club credit entries")
        ordering = ["-created"]
        indexes = [models.Index(fields=["club", "created"])]

    def __str__(self) -> str:
        return f"{self.club} {self.amount:+} ({self.kind})"


class InvoicingAlert(UUIDModel):
    """A "this club needs an invoice" alert that was sent while automatic invoicing is off. One row per club and next period start, so the
    daily check mails each club's alert once (the weekly digest is not recorded)."""

    club = models.ForeignKey("club.Club", verbose_name=_("club"), on_delete=models.CASCADE, related_name="invoicing_alerts")
    period_start = models.DateField(_("period start"))
    sent_at = models.DateTimeField(_("sent at"), default=timezone.now)

    class Meta:
        verbose_name = _("invoicing alert")
        verbose_name_plural = _("invoicing alerts")
        constraints = [models.UniqueConstraint(fields=["club", "period_start"], name="unique_invoicing_alert_per_club_period")]

    def __str__(self) -> str:
        return f"{self.club} {self.period_start}"
