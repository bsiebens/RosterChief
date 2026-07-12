from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Attendance, Event, Location, Opponent


@admin.register(Opponent)
class OpponentAdmin(admin.ModelAdmin):
    list_display = ["name", "club"]
    list_filter = ["club"]
    search_fields = ["name"]


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ["name", "city", "club"]
    list_filter = ["club"]
    search_fields = ["name", "city"]


class AttendanceInline(admin.TabularInline):
    model = Attendance
    extra = 0
    raw_id_fields = ["member"]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "start", "season", "club"]
    list_filter = ["kind", "club", "teams"]
    search_fields = ["title"]
    date_hierarchy = "start"
    autocomplete_fields = ["season", "location", "opponent", "teams", "invited_members", "excluded_members"]
    inlines = [AttendanceInline]
    fieldsets = [
        [None, {"fields": ["title", "kind", "season"]}],
        [_("Audience"), {"fields": ["teams", "invited_members", "excluded_members"]}],
        [_("When"), {"fields": ["start", "end", "gathering", "deadline"]}],
        [_("Where"), {"fields": ["location", "opponent"]}],
    ]


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ["event", "member", "status"]
    list_filter = ["status", "event__kind"]
    search_fields = ["event__title", "member__first_name", "member__last_name"]
    raw_id_fields = ["event", "member"]
