from django.contrib import admin

from .models import Notification, NotificationDelivery, OutboxEntry, PushAlert


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "member", "club", "sent_at", "content_type"]
    list_filter = ["club", "sent_at"]
    search_fields = ["title", "member__last_name", "member__first_name"]
    raw_id_fields = ["member"]
    readonly_fields = ["sent_at", "sent_to_emails"]


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    """The delivery log is written by the channels only: browse it, never edit it."""

    list_display = ["channel", "target", "status", "attempts", "club", "notification", "created", "next_attempt_at"]
    list_filter = ["status", "channel", "club"]
    search_fields = ["target", "last_error"]
    raw_id_fields = ["notification", "user", "club"]
    list_select_related = ["club"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(OutboxEntry)
class OutboxEntryAdmin(admin.ModelAdmin):
    """Pending dispatch work: browse it, never edit it."""

    list_display = ["kind", "status", "attempts", "available_at", "created", "last_error"]
    list_filter = ["status", "kind"]
    search_fields = ["last_error"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(PushAlert)
class PushAlertAdmin(admin.ModelAdmin):
    """Written by notifications.alerts only: browse it, never edit it."""

    list_display = ["key", "severity", "message", "first_seen", "notifications_sent", "resolved_at"]
    list_filter = ["severity"]
    search_fields = ["key", "message"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
