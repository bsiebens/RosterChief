"""Schemas for the push endpoints (``/push`` and ``/users/{id}/push``). Credentials never appear: only whether they are configured."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import StringConstraints

DATA_LIMIT_BYTES = 2048
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ClubRef(Schema):
    id: UUID
    name: str


# --- monitoring -------------------------------------------------------------------------------------------------------


class AlertOut(Schema):
    id: UUID
    message: str
    first_seen: datetime
    notifications_sent: int


class ProviderHealthOut(Schema):
    provider: str
    label: str
    configured: bool | None = None
    enabled: bool | None = None
    requests: int
    sent: int
    failed: int
    dead: int
    retrying: int
    success_rate: float | None = None
    p50_ms: int | None = None
    p95_ms: int | None = None
    last_success: datetime | None = None
    last_failure: datetime | None = None
    state: Literal["ok", "degraded", "down", "quiet"]


class RetryQueueOut(Schema):
    waiting: int
    oldest_next_attempt: datetime | None = None
    overdue: int
    stuck: bool
    reasons: list[str]


class OutboxOut(Schema):
    pending: int
    failed: int
    oldest_pending: datetime | None = None


class SeriesPoint(Schema):
    bucket: datetime
    sent: int
    not_sent: int


class TopFailureOut(Schema):
    provider: str
    error: str
    count: int
    last_seen: datetime


class ProblemOut(Schema):
    id: UUID
    created: datetime
    provider: str
    club: ClubRef | None = None
    target_masked: str
    status: str
    attempts: int
    error: str
    user_id: UUID | None = None
    user_email_masked: str | None = None


class DeviceCount(Schema):
    enabled: int
    disabled: int
    stale: int


class WebCount(Schema):
    subscriptions: int


class DevicesOut(Schema):
    ios: DeviceCount
    android: DeviceCount
    web: WebCount


class SandboxBrief(Schema):
    enabled: bool
    expires_at: datetime | None = None


class MonitoringOut(Schema):
    window: str
    since: datetime
    club_id: UUID | None = None
    alerts: list[AlertOut]
    providers: list[ProviderHealthOut]
    retry_queue: RetryQueueOut
    outbox: OutboxOut
    series: list[SeriesPoint]
    top_failures: list[TopFailureOut]
    recent_problems: list[ProblemOut]
    devices: DevicesOut
    sandbox: SandboxBrief


# --- configuration ----------------------------------------------------------------------------------------------------


class ProviderConfigOut(Schema):
    provider: str
    label: str
    configured: bool
    missing: list[str]
    enabled: bool


class PushSettingsOut(Schema):
    apns_enabled: bool
    fcm_enabled: bool
    webpush_enabled: bool
    retry_delays_minutes: list[int]
    outbox_max_attempts: int
    sent_retention_days: int
    failed_retention_days: int
    stale_device_days: int
    disabled_device_retention_days: int
    unseen_device_retention_days: int
    degraded_below_percent: int
    down_below_percent: int
    health_min_requests: int
    alert_emails: str  # comma separated, normalised


class PushConfigOut(Schema):
    providers: list[ProviderConfigOut]
    settings: PushSettingsOut
    sandbox_default: bool


class PushSettingsIn(Schema):
    """Any subset. Loosely typed on purpose: the rules (and their messages) live in controlpanel.services.push_config."""

    apns_enabled: Any = None
    fcm_enabled: Any = None
    webpush_enabled: Any = None
    retry_delays_minutes: Any = None
    outbox_max_attempts: Any = None
    sent_retention_days: Any = None
    failed_retention_days: Any = None
    stale_device_days: Any = None
    disabled_device_retention_days: Any = None
    unseen_device_retention_days: Any = None
    degraded_below_percent: Any = None
    down_below_percent: Any = None
    health_min_requests: Any = None
    alert_emails: Any = None


class CredentialsCheckOut(Schema):
    ok: bool
    detail: str


# --- sandbox ----------------------------------------------------------------------------------------------------------


class SandboxStateOut(Schema):
    enabled: bool | None = None
    effective: bool
    expires_at: datetime | None = None
    scenario: str
    latency_ms: int
    default_from_settings: bool


class ScenarioOut(Schema):
    value: str
    label: str
    description: str


class SandboxEventOut(Schema):
    id: UUID
    created: datetime
    user_email: str | None = None
    action: str
    detail: str


class SandboxOut(Schema):
    state: SandboxStateOut
    scenarios: list[ScenarioOut]
    events: list[SandboxEventOut]


class SandboxModeIn(Schema):
    enabled: bool
    confirm: bool = False
    expires_in_minutes: Literal[30, 60, 240, 1440] | None = None


class SandboxScenarioIn(Schema):
    scenario: str
    latency_ms: int = 0


class SandboxMessageOut(Schema):
    id: UUID
    created: datetime
    channel: str
    provider: str
    club: ClubRef | None = None
    user_email: str | None = None
    target: str
    target_label: str
    title: str
    body: str
    request: dict
    outcome: str


class SandboxMessagesOut(Schema):
    items: list[SandboxMessageOut]
    outcomes: dict[str, int]
    providers: list[str]


class DeletedOut(Schema):
    deleted: int


# --- composer ---------------------------------------------------------------------------------------------------------


class SendTarget(Schema):
    type: Literal["user", "device", "web_subscription"]
    id: UUID


class SendIn(Schema):
    target: SendTarget
    title: Title
    body: Body
    data: dict | None = None
    category: str = "general"
    priority: Literal["normal", "critical"] = "normal"
    mode: Literal["live", "sandbox"] = "sandbox"
    confirm_live: bool = False


class SendResultOut(Schema):
    channel: str
    provider: str
    target_masked: str
    status: str
    error: str
    duration_ms: int | None = None


class SendOut(Schema):
    mode: str
    sandboxed: bool
    results: list[SendResultOut]


class TargetOut(Schema):
    type: Literal["user"]
    id: UUID
    label: str


# --- a user's push state ----------------------------------------------------------------------------------------------


class UserDeviceOut(Schema):
    id: UUID
    platform: str
    platform_label: str
    provider: str
    environment: str
    app_version: str
    locale: str
    enabled: bool
    disabled_at: datetime | None = None
    last_seen: datetime
    stale: bool


class UserWebSubscriptionOut(Schema):
    id: UUID
    club: ClubRef | None = None
    user_agent: str
    endpoint_masked: str
    last_seen: datetime


class QuietHoursOut(Schema):
    enabled: bool
    start: str
    end: str
    timezone: str
    active_now: bool
    until: datetime | None = None


class MasterSwitchOut(Schema):
    club: ClubRef | None = None
    channel: str
    enabled: bool


class CategoryOffOut(Schema):
    club: ClubRef | None = None
    category: str
    category_label: str
    channel: str


class UserPreferencesOut(Schema):
    email_mode: str
    email_mode_label: str
    quiet_hours: QuietHoursOut | None = None
    master_switches: list[MasterSwitchOut]
    categories_off: list[CategoryOffOut]


class UserDeliveryOut(Schema):
    id: UUID
    created: datetime
    provider: str
    status: str
    category: str
    title: str
    attempts: int
    error: str


class UserPushOut(Schema):
    devices: list[UserDeviceOut]
    web_subscriptions: list[UserWebSubscriptionOut]
    preferences: UserPreferencesOut
    recent_deliveries: list[UserDeliveryOut]


class FollowedTeamOut(Schema):
    id: UUID
    name: str


class FollowedGameOut(Schema):
    id: UUID
    title: str
    start: datetime


class FollowingClubOut(Schema):
    club: dict  # {id, name}
    teams: list[FollowedTeamOut]
    games: list[FollowedGameOut]


class UserFollowingOut(Schema):
    """Which teams (and single games) an account follows for live game pushes, per club."""

    clubs: list[FollowingClubOut]
    total_teams: int
    total_games: int
