"""Entitlements: what a club may use. Shared (public schema), written only by the platform.

A club's entitlements = the features of its current ``Subscription.plan``, then ``FeatureOverride`` rows on top.
Billing (prices, invoices) is deliberately absent: it can later be added next to ``Plan``/``Subscription`` without
changing how entitlements are resolved.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel

from .catalog import FLAG, LIMIT, duplicate_limit_scopes


class Feature(UUIDModel):
    """Mirror of the code catalog (see ``sync_catalog``), so database rows can reference features."""

    code = models.SlugField(_("code"), unique=True)
    name = models.CharField(_("name"), max_length=150)
    description = models.TextField(_("description"), blank=True)
    kind = models.CharField(_("kind"), max_length=10, choices=[(FLAG, _("On/off")), (LIMIT, _("Numeric limit"))], default=FLAG)
    is_active = models.BooleanField(_("in catalog"), default=True, help_text=_("Cleared when the code no longer defines this feature."))

    class Meta:
        verbose_name = _("feature")
        verbose_name_plural = _("features")
        ordering = ["code"]

    def __str__(self) -> str:
        return self.name


class Plan(UUIDModel):
    code = models.SlugField(_("code"), unique=True)
    name = models.CharField(_("name"), max_length=100)
    description = models.TextField(_("description"), blank=True)
    is_active = models.BooleanField(_("active"), default=True, help_text=_("Inactive plans cannot be chosen for new subscriptions."))
    features = models.ManyToManyField(Feature, verbose_name=_("features"), through="PlanFeature", related_name="plans")

    class Meta:
        verbose_name = _("plan")
        verbose_name_plural = _("plans")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class PlanFeature(UUIDModel):
    plan = models.ForeignKey(Plan, verbose_name=_("plan"), on_delete=models.CASCADE, related_name="plan_features")
    feature = models.ForeignKey(Feature, verbose_name=_("feature"), on_delete=models.PROTECT, related_name="plan_features")
    limit = models.PositiveIntegerField(_("limit"), null=True, blank=True, help_text=_("Only for limit features. Empty = unlimited."))

    class Meta:
        verbose_name = _("plan feature")
        verbose_name_plural = _("plan features")
        constraints = [models.UniqueConstraint(fields=["plan", "feature"], name="uniq_plan_feature")]

    def __str__(self) -> str:
        return f"{self.plan}: {self.feature}"

    def clean(self):
        super().clean()
        if self.plan_id and self.feature_id:
            others = PlanFeature.objects.filter(plan_id=self.plan_id).exclude(pk=self.pk).values_list("feature__code", flat=True)
            if duplicate_limit_scopes([*others, self.feature.code]):
                raise ValidationError(_("A plan can only have one feature limiting the same thing (e.g. the number of members)."))


class Subscription(UUIDModel):
    """A club's plan over time. ``ends_at`` empty = the current subscription; a club has at most one."""

    TRIAL, ACTIVE, PAST_DUE, CANCELED = "trial", "active", "past_due", "canceled"
    STATUS_CHOICES = [(TRIAL, _("Trial")), (ACTIVE, _("Active")), (PAST_DUE, _("Past due")), (CANCELED, _("Canceled"))]

    club = models.ForeignKey(settings.TENANT_MODEL, verbose_name=_("club"), on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(Plan, verbose_name=_("plan"), on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(_("status"), max_length=10, choices=STATUS_CHOICES, default=ACTIVE)
    starts_at = models.DateTimeField(_("starts at"), default=timezone.now)
    ends_at = models.DateTimeField(_("ends at"), null=True, blank=True, help_text=_("Access stops at this moment. Empty = no end."))
    trial_ends_at = models.DateTimeField(_("trial ends at"), null=True, blank=True)

    class Meta:
        verbose_name = _("subscription")
        verbose_name_plural = _("subscriptions")
        ordering = ["-starts_at"]
        constraints = [
            models.UniqueConstraint(fields=["club"], condition=models.Q(ends_at__isnull=True), name="one_open_subscription_per_club"),
        ]

    def __str__(self) -> str:
        return f"{self.club} - {self.plan} ({self.status})"

    def grants_access(self, now=None) -> bool:
        """Whether the plan's features apply right now. ``status`` is informational (billing); only dates decide."""
        now = now or timezone.now()
        if self.starts_at > now or (self.ends_at is not None and self.ends_at <= now):
            return False
        return not (self.status == self.TRIAL and self.trial_ends_at is not None and self.trial_ends_at <= now)


class FeatureOverride(UUIDModel):
    """Platform exception for one club: grant or revoke a feature, optionally with a different limit and an expiry."""

    club = models.ForeignKey(settings.TENANT_MODEL, verbose_name=_("club"), on_delete=models.CASCADE, related_name="feature_overrides")
    feature = models.ForeignKey(Feature, verbose_name=_("feature"), on_delete=models.PROTECT, related_name="overrides")
    enabled = models.BooleanField(_("enabled"), default=True, help_text=_("Untick to revoke a feature the plan would give."))
    limit = models.PositiveIntegerField(_("limit"), null=True, blank=True, help_text=_("Only for limit features. Empty = unlimited."))
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    reason = models.CharField(_("reason"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("feature override")
        verbose_name_plural = _("feature overrides")
        constraints = [models.UniqueConstraint(fields=["club", "feature"], name="uniq_override_per_club_feature")]

    def __str__(self) -> str:
        return f"{self.club}: {self.feature} ({'on' if self.enabled else 'off'})"

    def is_current(self, now=None) -> bool:
        return self.expires_at is None or self.expires_at > (now or timezone.now())


class EntitlementLog(UUIDModel):
    """Append-only audit trail of every change to plans, subscriptions and overrides."""

    club = models.ForeignKey(settings.TENANT_MODEL, verbose_name=_("club"), null=True, blank=True, on_delete=models.CASCADE, related_name="entitlement_log")
    plan = models.ForeignKey(Plan, verbose_name=_("plan"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    feature = models.ForeignKey(Feature, verbose_name=_("feature"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(_("action"), max_length=40)
    old = models.JSONField(_("old"), null=True, blank=True)
    new = models.JSONField(_("new"), null=True, blank=True)
    reason = models.CharField(_("reason"), max_length=255, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("actor"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = _("entitlement log entry")
        verbose_name_plural = _("entitlement log")
        ordering = ["-created"]

    def __str__(self) -> str:
        return f"{self.created:%Y-%m-%d %H:%M} {self.action}"
