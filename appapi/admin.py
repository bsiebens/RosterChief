from django.contrib import admin

from .models import LiveActivity, PushDevice


@admin.register(PushDevice)
class PushDeviceAdmin(admin.ModelAdmin):
    list_display = ["user", "platform", "environment", "enabled", "app_version", "locale", "last_seen"]
    list_filter = ["platform", "environment", "enabled"]
    search_fields = ["user__email", "installation_id"]
    raw_id_fields = ["user"]
    readonly_fields = ["push_token", "push_to_start_token", "last_seen"]


@admin.register(LiveActivity)
class LiveActivityAdmin(admin.ModelAdmin):
    list_display = ["device", "event", "club", "created"]
    list_filter = ["club"]
    readonly_fields = ["club", "device", "event", "token", "created", "modified"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
