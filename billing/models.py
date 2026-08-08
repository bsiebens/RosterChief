"""What the platform charges a club.

Deliberately NOT club-scoped. `shop` is a club charging its members — tenant data, owned by
the club. This is RosterChief charging the club: platform-owned, and no club user ever sees
it. Nothing here inherits ClubScopedModel: these rows reference a Club, they are not owned
by one, and a tenant-scoped manager would be exactly the wrong default.
"""

from datetime import date, timedelta
from decimal import Decimal

from dateutil import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel, unique_slugify

ZERO = Decimal("0.00")

#: Conservative lower bound on the number of days in a month, used to express the plan's
#: clock invariants as CheckConstraints — month arithmetic is not available in SQL, and
#: under-counting is the safe direction for a guard rail.
DAYS_PER_MONTH_FLOOR = 28

# Defaults for a new plan, chosen to reproduce the annual billing the platform started with.
DEFAULT_DURATION_MONTHS = 12
DEFAULT_RENEWAL_LEAD_DAYS = 30
DEFAULT_GRACE_DAYS = 30


def add_months(day: date, months: int) -> date:
    return day + relativedelta.relativedelta(months=months)


class PlanQuerySet(models.QuerySet):
    def visible(self):
        """Excludes soft-deleted plans -- see billing.services.plans.delete_plan.

        Opt-in, same shape as club.models.ClubManager.active(): the default manager stays
        unfiltered (Django admin, and anything reading historical data, sees everything),
        and every picker/listing a platform admin actually chooses from calls this.
        """
        return self.filter(deleted_at__isnull=True)


class Plan(UUIDModel):
    """What a club is billed on: a duration, a set of clocks, and a dated price.

    The price itself lives in PlanPrice, which is dated. The three day/month numbers here
    are the plan's *clocks*, and they are named for what they measure from — see BILLING.md
    §3, because confusing them is the easy mistake:

    * ``duration_months``   — how long a period runs, from its start.
    * ``renewal_lead_days`` — how far BEFORE a period starts its invoice is raised.
    * ``grace_days``        — how long AFTER a period starts it may remain unpaid.
    """

    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True)
    description = models.TextField(_("description"), blank=True)
    is_active = models.BooleanField(_("active"), default=True, help_text=_("Inactive plans keep billing existing subscriptions but cannot be chosen for new ones."))

    duration_months = models.PositiveSmallIntegerField(_("duration (months)"), default=DEFAULT_DURATION_MONTHS, validators=[MinValueValidator(1)], help_text=_("How long one billing period runs."))
    renewal_lead_days = models.PositiveSmallIntegerField(_("renewal lead (days)"), default=DEFAULT_RENEWAL_LEAD_DAYS, help_text=_("Raise the next period's invoice this many days before that period starts."))
    grace_days = models.PositiveSmallIntegerField(_("grace (days)"), default=DEFAULT_GRACE_DAYS, help_text=_("Days after a period starts before an unpaid club is archived."))

    is_trial = models.BooleanField(
        _("trial plan"),
        default=False,
        help_text=_("Offered as a trial rather than as a paid plan. A trial converts to the plan chosen on the subscription once it runs out."),
    )

    # Not user-editable: set by billing.services.plans.delete_plan. Due.plan is PROTECT, so
    # a plan that has ever billed anyone can never actually be removed -- deleting it hides
    # it (and clears every club currently on it) instead, so past invoices still say what
    # they were billed under. See that module's docstring for the full reasoning.
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True, editable=False)

    objects = PlanQuerySet.as_manager()

    class Meta:
        verbose_name = _("plan")
        verbose_name_plural = _("plans")
        ordering = ["name"]
        constraints = [
            # Lead longer than the period itself would raise the next invoice before the
            # current period had even started, and periods would run away from the calendar.
            models.CheckConstraint(
                condition=Q(renewal_lead_days__lt=F("duration_months") * DAYS_PER_MONTH_FLOOR),
                name="renewal_lead_shorter_than_duration",
            ),
            # Grace longer than the period means the next period is issued while this one is
            # still in grace: unpaid periods stack and the club is never archived.
            models.CheckConstraint(
                condition=Q(grace_days__lte=F("duration_months") * DAYS_PER_MONTH_FLOOR),
                name="grace_no_longer_than_duration",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def clean(self):
        """The same two invariants the CheckConstraints enforce, as form errors.

        Without this a form would hand the database an impossible plan and get back an
        IntegrityError -- a 500 rather than "that lead is longer than the period".
        """
        if not self.duration_months:
            return

        period_days = self.duration_months * DAYS_PER_MONTH_FLOOR
        errors = {}
        if self.renewal_lead_days is not None and self.renewal_lead_days >= period_days:
            errors["renewal_lead_days"] = _("Must be shorter than the period itself (under %(days)s days for this duration), or the next invoice would be raised before the current period starts.") % {"days": period_days}
        if self.grace_days is not None and self.grace_days > period_days:
            errors["grace_days"] = _("Must not be longer than the period itself (at most %(days)s days for this duration), or unpaid periods stack up and the club is never archived.") % {"days": period_days}

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)

    def price_on(self, day: date | None = None) -> Decimal | None:
        """The price in force on ``day`` — the latest one that had started by then.

        None means the plan had no price yet on that date. Callers must treat that as
        "cannot bill", never as free.
        """
        day = day or timezone.localdate()
        price = self.prices.filter(active_from__lte=day).order_by("-active_from").first()

        return price.amount if price else None


class PlanPrice(UUIDModel):
    """A dated price for a plan.

    Dated rather than keyed by year: a rate change is one new row with a future
    ``active_from``, and every period already opened keeps the amount it was billed at.
    """

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="prices", verbose_name=_("plan"))
    active_from = models.DateField(_("active from"), help_text=_("Periods opening on or after this date are billed at this amount."))
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2, validators=[MinValueValidator(ZERO)])

    class Meta:
        verbose_name = _("plan price")
        verbose_name_plural = _("plan prices")
        ordering = ["plan__name", "-active_from"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "active_from"], name="unique_plan_price_per_start_date"),
        ]

    def __str__(self):
        return f"{self.plan} — {self.amount} from {self.active_from}"


class Subscription(UUIDModel):
    """A club's current plan. The periods it is billed for are Dues."""

    club = models.OneToOneField("club.Club", on_delete=models.CASCADE, related_name="subscription", verbose_name=_("club"))
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions", verbose_name=_("plan"))
    auto_renew = models.BooleanField(_("auto renew"), default=True, help_text=_("Issue the next period automatically before this one ends. Off means you invoice this club by hand."))
    auto_archive = models.BooleanField(_("auto archive"), default=True, help_text=_("Archive this club when a period goes unpaid past its grace period."))
    notes = models.TextField(_("notes"), blank=True)

    trial_ends_at = models.DateField(_("trial ends at"), null=True, blank=True, help_text=_("Set while this club is on a trial. The plan switches to the post-trial plan the next time a period is opened after this date."))
    post_trial_plan = models.ForeignKey(Plan, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("post-trial plan"), help_text=_("The plan this club switches to automatically once its trial ends."))

    class Meta:
        verbose_name = _("subscription")
        verbose_name_plural = _("subscriptions")
        ordering = ["club__name"]
        constraints = [
            # Both set together or neither -- a trial with no target plan (or a target
            # plan with no trial end date) is a half-configured state nothing should read.
            models.CheckConstraint(
                condition=Q(trial_ends_at__isnull=True, post_trial_plan__isnull=True) | Q(trial_ends_at__isnull=False, post_trial_plan__isnull=False),
                name="trial_fields_set_together",
            ),
        ]

    def __str__(self):
        return f"{self.club} — {self.plan}"


class Due(UUIDModel):
    """One billing period for one club.

    ``plan`` and ``amount`` are snapshots taken when the period opens, never read back
    through the plan at display time: raise the price and last year's period must still say
    what was actually charged. A live lookup would rewrite financial history.

    ``period_end`` and ``grace_until`` are snapshots for the same reason. They are stored as
    *dates* rather than as the plan's duration/grace *numbers*, which is what makes editing a
    plan afterwards leave every period already running exactly where it was.
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
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="dues", verbose_name=_("plan"))

    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2, validators=[MinValueValidator(ZERO)])
    amount_paid = models.DecimalField(_("amount paid"), max_digits=10, decimal_places=2, default=ZERO, help_text=_("Kept in step with the payments by the billing service."))

    period_start = models.DateField(_("period start"))
    period_end = models.DateField(_("period end"), blank=True)
    grace_until = models.DateField(_("grace until"), blank=True, help_text=_("Past this date an unpaid club is archived. Measured from the period start, not its end."))

    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.UNPAID)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)

    is_trial = models.BooleanField(_("trial period"), default=False, help_text=_("This period was opened as a trial. A durable marker on the row itself -- the subscription's own trial fields are cleared once it converts."))

    # Reminders are sent once per escalation level, not once per run: the cron job runs daily,
    # and a club that owes money for a month must not get thirty identical emails. Storing the
    # level last sent (rather than a date) means an escalation always gets through, and nothing
    # else does. See billing/services/reminders.py.
    last_reminder_level = models.CharField(_("last reminder level"), max_length=20, blank=True, editable=False)
    last_reminder_sent_at = models.DateTimeField(_("last reminder sent at"), null=True, blank=True, editable=False)

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
        # A period runs for the plan's duration from its start, and the grace runs from that
        # same start -- NOT from the period end. Measured from the end, a club would get the
        # whole unpaid period plus the grace on top (~410 days on an annual plan) before
        # anything switched it off. Derived here so no caller can open a period without them.
        if not self.period_end:
            self.period_end = add_months(self.period_start, self.plan.duration_months) - timedelta(days=1)
        if not self.grace_until:
            self.grace_until = self.period_start + timedelta(days=self.plan.grace_days)
        super().save(*args, **kwargs)

    @property
    def balance(self) -> Decimal:
        return self.amount - self.amount_paid

    @property
    def is_owing(self) -> bool:
        return self.status in self.OWING

    def is_issued_ahead(self, today: date | None = None) -> bool:
        """Billed and owing, but the period it covers has not started yet.

        The gentlest of the three owing states: the invoice was raised during the plan's
        renewal lead window, and nothing is late yet.
        """
        today = today or timezone.localdate()

        return self.is_owing and today < self.period_start

    def is_in_grace(self, today: date | None = None) -> bool:
        """The period has started and is still unpaid, but is not archivable yet."""
        today = today or timezone.localdate()

        return self.is_owing and self.period_start <= today <= self.grace_until

    def is_overdue(self, today: date | None = None) -> bool:
        """Unpaid past grace — this is what makes a club archivable."""
        today = today or timezone.localdate()

        return self.is_owing and self.grace_until < today

    def days_until_archive(self, today: date | None = None) -> int:
        """Days left before this period makes the club archivable. Negative once past."""
        today = today or timezone.localdate()

        return (self.grace_until - today).days


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

    Only the number and the issue date are stored: the money, the plan and the dates are
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
