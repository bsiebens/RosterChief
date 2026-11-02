"""Platform-level records (public schema)."""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


class PlatformAuditLog(UUIDModel):
    """Append-only record of who did what in the control panel (entitlement changes also land in EntitlementLog)."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("actor"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(_("action"), max_length=60, db_index=True)
    target_type = models.CharField(_("target type"), max_length=40, blank=True)
    target_id = models.CharField(_("target id"), max_length=64, blank=True)
    club = models.ForeignKey(settings.TENANT_MODEL, verbose_name=_("club"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    data = models.JSONField(_("data"), default=dict, blank=True)

    class Meta:
        verbose_name = _("platform audit log entry")
        verbose_name_plural = _("platform audit log")
        ordering = ["-created"]
        indexes = [models.Index(fields=["target_type", "target_id"])]

    def __str__(self) -> str:
        return f"{self.created:%Y-%m-%d %H:%M} {self.action}"


class AccountBlock(UUIDModel):
    """One period during which an account was blocked (``User.is_active`` is the actual switch).

    Blocking creates a row; unblocking closes the open one. At most one row per user is open at a time, so the
    rows form the account's block history.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("user"), on_delete=models.CASCADE, related_name="account_blocks")
    blocked_at = models.DateTimeField(_("blocked at"), default=timezone.now)
    blocked_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("blocked by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.TextField(_("reason"), blank=True)
    unblocked_at = models.DateTimeField(_("unblocked at"), null=True, blank=True)
    unblocked_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("unblocked by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    unblock_reason = models.TextField(_("unblock reason"), blank=True)

    class Meta:
        verbose_name = _("account block")
        verbose_name_plural = _("account blocks")
        ordering = ["-blocked_at"]
        constraints = [models.UniqueConstraint(fields=["user"], condition=models.Q(unblocked_at__isnull=True), name="uniq_open_account_block")]

    def __str__(self) -> str:
        return f"{self.user_id} blocked {self.blocked_at:%Y-%m-%d}"


class IdempotencyRecord(UUIDModel):
    """Remembers the result of a write request sent with an ``Idempotency-Key`` header, so a retry replays it."""

    IN_PROGRESS, DONE = "in_progress", "done"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("user"), on_delete=models.CASCADE, related_name="+")
    key = models.CharField(_("key"), max_length=128)
    endpoint = models.CharField(_("endpoint"), max_length=200)
    fingerprint = models.CharField(_("fingerprint"), max_length=64, help_text=_("Hash of method, path and body, to detect a key reused for a different request."))
    state = models.CharField(_("state"), max_length=12, choices=[(IN_PROGRESS, "In progress"), (DONE, "Done")], default=IN_PROGRESS)
    status_code = models.PositiveSmallIntegerField(_("status code"), null=True, blank=True)
    response = models.JSONField(_("response"), null=True, blank=True)

    class Meta:
        verbose_name = _("idempotency record")
        verbose_name_plural = _("idempotency records")
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="uniq_idempotency_key_per_user")]


class SingletonSwitch(UUIDModel):
    """A platform-wide on/off switch stored as a single row, cached for a few seconds."""

    CACHE_SECONDS = 10

    is_active = models.BooleanField(_("is active"), default=False)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("started by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        abstract = True

    @classmethod
    def _cache_key(cls) -> str:
        return f"controlpanel:{cls.__name__.lower()}"

    @classmethod
    def current(cls):
        """The single row (created on first use). Cached briefly; writes refresh the cache."""
        from django.core.cache import cache

        row = cache.get(cls._cache_key())
        if row is None:
            row = cls.objects.first() or cls.objects.create()
            cache.set(cls._cache_key(), row, cls.CACHE_SECONDS)
        return row

    @classmethod
    def is_on(cls) -> bool:
        return cls.current().is_active

    def save(self, *args, **kwargs):
        from django.core.cache import cache

        super().save(*args, **kwargs)
        cache.set(self._cache_key(), self, self.CACHE_SECONDS)

    def switch(self, active: bool, user=None):
        from django.utils import timezone

        self.is_active = active
        self.started_at = timezone.now() if active else None
        self.started_by = user if active else None
        self.save()
        return self


class Maintenance(SingletonSwitch):
    """While active, the platform answers 503 everywhere except the control panel and sign-in."""

    message = models.TextField(_("message"), blank=True)

    class Meta:
        verbose_name = _("maintenance")
        verbose_name_plural = _("maintenance")

    def __str__(self) -> str:
        return "Maintenance (on)" if self.is_active else "Maintenance (off)"


class EmailSuppression(SingletonSwitch):
    """While active, automated email is not sent (password reset and account email are exempt)."""

    class Meta:
        verbose_name = _("email suppression")
        verbose_name_plural = _("email suppression")

    def __str__(self) -> str:
        return "Email paused" if self.is_active else "Email flowing"


class ClubProvisioning(UUIDModel):
    """A request to create a club. Building the club's schema is slow, so a Celery task does it and this row tracks it."""

    PENDING, RUNNING, SUCCEEDED, FAILED = "pending", "running", "succeeded", "failed"
    STATUS_CHOICES = [(PENDING, _("Pending")), (RUNNING, _("Running")), (SUCCEEDED, _("Succeeded")), (FAILED, _("Failed"))]

    name = models.CharField(_("name"), max_length=100)
    sport = models.ForeignKey("club.Sport", verbose_name=_("sport"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("owner"), on_delete=models.CASCADE, related_name="+")
    club_fields = models.JSONField(_("club fields"), default=dict, blank=True, help_text=_("Extra club fields (legal name, VAT id, ...)."))
    status = models.CharField(_("status"), max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    error = models.TextField(_("error"), blank=True)
    club = models.ForeignKey(settings.TENANT_MODEL, verbose_name=_("club"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("requested by"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)

    class Meta:
        verbose_name = _("club provisioning")
        verbose_name_plural = _("club provisioning")
        ordering = ["-created"]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"
