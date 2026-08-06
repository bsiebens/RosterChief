from django.contrib import admin

from .models import Flag, Maintenance


@admin.register(Flag)
class FlagAdmin(admin.ModelAdmin):
    list_display = ["name", "note", "everyone", "percent", "superusers", "staff"]
    list_filter = ["everyone", "superusers", "staff"]
    search_fields = ["name", "note"]
    filter_horizontal = ["clubs"]


@admin.register(Maintenance)
class MaintenanceAdmin(admin.ModelAdmin):
    list_display = ["__str__", "started_at", "started_by"]
    readonly_fields = ["started_at", "started_by"]
