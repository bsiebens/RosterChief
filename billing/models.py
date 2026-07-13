"""What the platform charges a club.

Deliberately NOT club-scoped. `shop` is a club charging its members — tenant data, owned by
the club. This is RosterChief charging the club: platform-owned, and no club user ever sees
it. Nothing here inherits ClubScopedModel: these rows reference a Club, they are not owned
by one, and a tenant-scoped manager would be exactly the wrong default.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel, unique_slugify

ZERO = Decimal("0.00")

#: A club stays live for six weeks past the end of an unpaid period before it is archived.
GRACE_DAYS = 45


def add_one_year(day: date) -> date:
    """The day one year on. 29 February has no counterpart in a common year, so it falls
    back to the 28th rather than raising."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


class Tier(UUIDModel):
    """A price band. The price itself lives in TierPrice, which is dated."""

    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True)
    description = models.TextField(_("description"), blank=True)
    is_active = models.BooleanField(_("active"), default=True, help_text=_("Inactive tiers keep billing existing subscriptions but cannot be chosen for new ones."))

    class Meta:
        verbose_name = _("tier")
        verbose_name_plural = _("tiers")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)

    def price_on(self, day: date | None = None) -> Decimal | None:
        """The price in force on ``day`` — the latest one that had started by then.

        None means the tier had no price yet on that date. Callers must treat that as
        "cannot bill", never as free.
        """
        day = day or timezone.localdate()
        price = self.prices.filter(active_from__lte=day).order_by("-active_from").first()

        return price.amount if price else None


class TierPrice(UUIDModel):
    """A dated price for a tier.

    Dated rather than keyed by year: a rate change is one new row with a future
    ``active_from``, and every period already opened keeps the amount it was billed at.
    """

    tier = models.ForeignKey(Tier, on_delete=models.CASCADE, related_name="prices", verbose_name=_("tier"))
    active_from = models.DateField(_("active from"), help_text=_("Periods opening on or after this date are billed at this amount."))
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2, validators=[MinValueValidator(ZERO)])

    class Meta:
        verbose_name = _("tier price")
        verbose_name_plural = _("tier prices")
        ordering = ["tier__name", "-active_from"]
        constraints = [
            models.UniqueConstraint(fields=["tier", "active_from"], name="unique_tier_price_per_start_date"),
        ]

    def __str__(self):
        return f"{self.tier} — {self.amount} from {self.active_from}"


class Subscription(UUIDModel):
    """A club's current plan. The periods it is billed for are Dues."""

    club = models.OneToOneField("club.Club", on_delete=models.CASCADE, related_name="subscription", verbose_name=_("club"))
    tier = models.ForeignKey(Tier, on_delete=models.PROTECT, related_name="subscriptions", verbose_name=_("tier"))
    auto_archive = models.BooleanField(_("auto archive"), default=True, help_text=_("Archive this club when a period goes unpaid past its grace period."))
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("subscription")
        verbose_name_plural = _("subscriptions")
        ordering = ["club__name"]

    def __str__(self):
        return f"{self.club} — {self.tier}"


class Due(UUIDModel):
    """One billing period for one club.

    ``tier`` and ``amount`` are snapshots taken when the period opens, never read back
    through the tier at display time: raise the price and last year's period must still say
    what was actually charged. A live lookup would rewrite financial history.
    """

    class Status(models.TextChoices):
        UNPAID = "unpaid", _("unpaid")
        PARTIAL = "partial", _("partially paid")
        PAID = "paid", _("paid")
        WAIVED = "waived", _("waived")
        CANCELLED = "cancelled", _("cancelled")

    #: Statuses that still owe money.
    OWING = (Status.UNPAID, Status.PARTIAL)

    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, related_name="dues", verbose_name=_("club"))
    tier = models.ForeignKey(Tier, on_delete=models.PROTECT, related_name="dues", verbose_name=_("tier"))

    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2, validators=[MinValueValidator(ZERO)])
    amount_paid = models.DecimalField(_("amount paid"), max_digits=10, decimal_places=2, default=ZERO, help_text=_("Kept in step with the payments by the billing service."))

    period_start = models.DateField(_("period start"))
    period_end = models.DateField(_("period end"), blank=True)
    grace_until = models.DateField(_("grace until"), blank=True, help_text=_("Past this date an unpaid club is archived."))

    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.UNPAID)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)

    class Meta:
        verbose_name = _("due")
        verbose_name_plural = _("dues")
        ordering = ["-period_start", "club__name"]
        constraints = [
            models.UniqueConstraint(fields=["club", "period_start"], name="unique_due_per_club_per_period"),
        ]

    def __str__(self):
        return f"{self.club} — {self.period_start} to {self.period_end}"

    def save(self, *args, **kwargs):
        # A period runs a rolling year from its start and the grace hangs off its end.
        # Derived here so no caller can open a period without them.
        if not self.period_end:
            self.period_end = add_one_year(self.period_start) - timedelta(days=1)
        if not self.grace_until:
            self.grace_until = self.period_end + timedelta(days=GRACE_DAYS)
        super().save(*args, **kwargs)

    @property
    def balance(self) -> Decimal:
        return self.amount - self.amount_paid

    @property
    def is_owing(self) -> bool:
        return self.status in self.OWING

    def is_in_grace(self, today: date | None = None) -> bool:
        """The period has ended unpaid, but the club is not archivable yet."""
        today = today or timezone.localdate()

        return self.is_owing and self.period_end < today <= self.grace_until

    def is_overdue(self, today: date | None = None) -> bool:
        """Unpaid past grace — this is what makes a club archivable."""
        today = today or timezone.localdate()

        return self.is_owing and self.grace_until < today


class DuePayment(UUIDModel):
    """Money received against a due.

    Several may land on one due: a club that pays in two transfers must not read as unpaid,
    and the half that did arrive has to be recorded somewhere.
    """

    class Method(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", _("bank transfer")
        CARD = "card", _("card")
        CASH = "cash", _("cash")
        OTHER = "other", _("other")

    due = models.ForeignKey(Due, on_delete=models.CASCADE, related_name="payments", verbose_name=_("due"))
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(_("method"), max_length=20, choices=Method.choices, default=Method.BANK_TRANSFER)
    reference = models.CharField(_("reference"), max_length=255, blank=True, help_text=_("Bank reference, transaction id — whatever lets you find this again."))
    paid_at = models.DateTimeField(_("paid at"), default=timezone.now)
    note = models.TextField(_("note"), blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="recorded_due_payments", verbose_name=_("recorded by"))

    class Meta:
        verbose_name = _("due payment")
        verbose_name_plural = _("due payments")
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.amount} — {self.due}"


class Invoice(UUIDModel):
    """The bill for one period.

    Only the number and the issue date are stored: the money, the tier and the dates are
    already frozen on the Due, so the PDF is rendered from those snapshots on demand. The
    number, though, must be stable and gapless — it is the thing an accountant reconciles
    against, so it is allocated once and never recomputed.
    """

    due = models.OneToOneField(Due, on_delete=models.CASCADE, related_name="invoice", verbose_name=_("due"))
    number = models.CharField(_("number"), max_length=32, unique=True, blank=True)
    issued_at = models.DateTimeField(_("issued at"), default=timezone.now)

    class Meta:
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        ordering = ["-issued_at"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self.next_number(self.issued_at.year)
        super().save(*args, **kwargs)

    @classmethod
    def next_number(cls, year: int) -> str:
        """INV-2026-00001, restarting each year.

        Platform-wide, unlike the shop's order numbers, which are per club: these are OUR
        invoices, and one sequence has to cover every club we bill.
        """
        prefix = f"INV-{year}-"
        last = cls.objects.filter(number__startswith=prefix).order_by("-number").first()
        sequence = int(last.number.removeprefix(prefix)) + 1 if last else 1

        return f"{prefix}{sequence:05d}"
