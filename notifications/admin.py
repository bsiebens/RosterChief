from django.contrib import admin

from rosterchief.admin import PlatformAdminMixin

from .models import (
    MasterSwitch,
    NotificationDelivery,
    NotificationPreference,
    OutboxEntry,
    PushAlert,
    PushDevice,
    PushSettings,
    QuietHours,
    SandboxEvent,
    SandboxMessage,
    SandboxState,
    UserNotificationSettings,
    WebPushSubscription,
)


class ReadOnlyAdmin(PlatformAdminMixin, admin.ModelAdmin):
    """Logs and registries are written by the engine, not by hand."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PushDevice)
class PushDeviceAdmin(ReadOnlyAdmin):
    list_display = ["user", "platform", "environment", "app_version", "enabled", "last_seen"]
    list_filter = ["platform", "environment", "enabled"]
    search_fields = ["user__email", "installation_id"]
    exclude = ["push_token", "push_to_start_token"]


@admin.register(WebPushSubscription)
class WebPushSubscriptionAdmin(ReadOnlyAdmin):
    list_display = ["user", "club", "last_seen"]
    search_fields = ["user__email"]
    exclude = ["p256dh", "auth"]


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(ReadOnlyAdmin):
    list_display = ["created", "provider", "status", "category", "title", "attempts", "user"]
    list_filter = ["provider", "status", "category"]
    search_fields = ["title", "user__email"]
    date_hierarchy = "created"


@admin.register(OutboxEntry)
class OutboxEntryAdmin(ReadOnlyAdmin):
    list_display = ["created", "kind", "status", "attempts", "available_at"]
    list_filter = ["status", "kind"]


@admin.register(PushAlert)
class PushAlertAdmin(ReadOnlyAdmin):
    list_display = ["key", "message", "first_seen", "resolved_at", "notifications_sent"]


@admin.register(SandboxMessage)
class SandboxMessageAdmin(ReadOnlyAdmin):
    list_display = ["created", "channel", "provider", "title", "outcome"]
    list_filter = ["provider", "outcome"]


@admin.register(SandboxEvent)
class SandboxEventAdmin(ReadOnlyAdmin):
    list_display = ["created", "user", "action", "detail"]


@admin.register(SandboxState)
class SandboxStateAdmin(ReadOnlyAdmin):
    list_display = ["enabled", "scenario", "latency_ms", "expires_at"]


@admin.register(PushSettings)
class PushSettingsAdmin(PlatformAdminMixin, admin.ModelAdmin):
    def has_add_permission(self, request):
        return not PushSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NotificationPreference, MasterSwitch, UserNotificationSettings, QuietHours)
class PreferenceAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["user", "modified"]
    search_fields = ["user__email"]
