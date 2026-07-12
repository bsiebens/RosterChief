from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Attendance, Event, EventSeries, Location, Opponent


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


@admin.register(EventSeries)
class EventSeriesAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "rrule", "dtstart", "generated_until", "club"]
    list_filter = ["kind", "club"]
    search_fields = ["title"]
    autocomplete_fields = ["location", "opponent", "teams", "invited_members", "excluded_members"]
    fieldsets = [
        [None, {"fields": ["title", "kind"]}],
        [_("Recurrence"), {"fields": ["rrule", "dtstart", "duration", "excluded_dates", "generated_until"]}],
        [_("Audience"), {"fields": ["teams", "invited_members", "excluded_members"]}],
        [_("Where"), {"fields": ["location", "opponent"]}],
    ]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "start", "season", "series", "detached", "club"]
    list_filter = ["kind", "club", "teams", "detached", "cancelled"]
    search_fields = ["title"]
    date_hierarchy = "start"
    autocomplete_fields = ["season", "series", "location", "opponent", "teams", "invited_members", "excluded_members"]
    inlines = [AttendanceInline]
    fieldsets = [
        [None, {"fields": ["title", "kind", "season"]}],
        [_("Series"), {"fields": ["series", "detached", "cancelled"]}],
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
