from django.contrib import admin

from rosterchief.admin import PlatformAdminMixin

from .models import AccountBlock, ClubProvisioning, EmailSuppression, IdempotencyRecord, Maintenance, PlatformAuditLog


class ReadOnlyMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SingletonAdmin(PlatformAdminMixin, admin.ModelAdmin):
    """One-row switch: no add once the row exists, never deleted. The control panel normally flips it."""

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not self.model.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Maintenance)
class MaintenanceAdmin(SingletonAdmin):
    list_display = ["__str__", "is_active", "started_at", "started_by"]
    raw_id_fields = ["started_by"]


@admin.register(EmailSuppression)
class EmailSuppressionAdmin(SingletonAdmin):
    list_display = ["__str__", "is_active", "started_at", "started_by"]
    raw_id_fields = ["started_by"]


@admin.register(AccountBlock)
class AccountBlockAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["user", "blocked_at", "blocked_by", "unblocked_at", "unblocked_by"]
    list_filter = ["unblocked_at"]
    search_fields = ["user__email", "user__first_name", "user__last_name", "reason"]
    raw_id_fields = ["user", "blocked_by", "unblocked_by"]
    list_select_related = ["user", "blocked_by", "unblocked_by"]
    date_hierarchy = "blocked_at"


@admin.register(ClubProvisioning)
class ClubProvisioningAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    """Tracks the background creation of a club; kept as a record."""

    list_display = ["name", "status", "owner", "club", "requested_by", "created", "finished_at"]
    list_filter = ["status"]
    search_fields = ["name", "owner__email", "error"]
    list_select_related = ["owner", "club", "requested_by"]
    date_hierarchy = "created"


@admin.register(PlatformAuditLog)
class PlatformAuditLogAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["created", "action", "actor", "target_type", "target_id", "club"]
    list_filter = ["action", "target_type"]
    search_fields = ["action", "target_id", "actor__email", "club__name"]
    list_select_related = ["actor", "club"]
    date_hierarchy = "created"


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["created", "user", "endpoint", "state", "status_code"]
    list_filter = ["state"]
    search_fields = ["key", "endpoint", "user__email"]
    list_select_related = ["user"]
    date_hierarchy = "created"
