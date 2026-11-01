"""Resolve what a club may use. Read-only; all writes live in ``features.services``."""

from dataclasses import dataclass

from django.db import models
from django.utils import timezone
from django_tenants.utils import get_public_schema_name

from .catalog import CATALOG


class UnknownFeature(KeyError):
    """A feature code that is not in the catalog (almost always a typo in calling code)."""


class LimitExceeded(Exception):
    def __init__(self, code: str, limit: int):
        super().__init__(f"The limit for '{code}' ({limit}) has been reached.")
        self.code, self.limit = code, limit


@dataclass(frozen=True)
class Entitlement:
    enabled: bool = False
    limit: int | None = None  # None = unlimited (only meaningful when enabled)


class Entitlements:
    """The effective features of one club. ``bypass`` grants everything (platform owner, public tenant)."""

    def __init__(self, data: dict[str, Entitlement] | None = None, *, bypass: bool = False):
        self._data = data or {}
        self.bypass = bypass

    @staticmethod
    def _check(code: str) -> None:
        if code not in CATALOG:
            raise UnknownFeature(code)

    def has(self, code: str) -> bool:
        self._check(code)
        return self.bypass or self._data.get(code, Entitlement()).enabled

    def limit(self, code: str) -> int | None:
        """The numeric limit, or ``None`` for unlimited. Only meaningful if ``has(code)``."""
        self._check(code)
        return None if self.bypass else self._data.get(code, Entitlement()).limit

    def check_limit(self, code: str, current: int, *, adding: int = 1) -> None:
        """Raise ``LimitExceeded`` if ``current + adding`` would pass the limit (or the feature is off)."""
        if not self.has(code):
            raise LimitExceeded(code, 0)
        limit = self.limit(code)
        if limit is not None and current + adding > limit:
            raise LimitExceeded(code, limit)

    def enabled_codes(self) -> set[str]:
        return set(CATALOG) if self.bypass else {c for c, e in self._data.items() if e.enabled}

    # Template access: {% if features.sponsoring %}. Unknown names are False rather than an error in templates.
    def __getattr__(self, name: str) -> bool:
        if name.startswith("_") or name not in CATALOG:
            raise AttributeError(name)
        return self.has(name)


def resolve_entitlements(club, *, now=None, bypass: bool = False) -> Entitlements:
    """Plan features of the current subscription, with unexpired overrides applied on top."""
    from .models import FeatureOverride, PlanFeature, Subscription

    if bypass or club is None or club.schema_name == get_public_schema_name():
        return Entitlements(bypass=True)

    now = now or timezone.now()
    data: dict[str, Entitlement] = {}

    subscription = (
        Subscription.objects.filter(club=club, starts_at__lte=now)
        .filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now))
        .select_related("plan")
        .order_by("-starts_at")
        .first()
    )
    if subscription is not None and subscription.grants_access(now) and subscription.plan.is_active:
        for pf in PlanFeature.objects.filter(plan=subscription.plan, feature__is_active=True).select_related("feature"):
            data[pf.feature.code] = Entitlement(True, pf.limit)

    for ov in FeatureOverride.objects.filter(club=club, feature__is_active=True).select_related("feature"):
        if not ov.is_current(now):
            continue
        data[ov.feature.code] = Entitlement(True, ov.limit) if ov.enabled else Entitlement(False)

    return Entitlements(data)


def entitlements_for_request(request) -> Entitlements:
    from authorization.permissions import is_platform_owner

    club = getattr(request, "tenant", None)
    user = getattr(request, "user", None)
    bypass = bool(user is not None and user.is_authenticated and is_platform_owner(user))
    return resolve_entitlements(club, bypass=bypass)
