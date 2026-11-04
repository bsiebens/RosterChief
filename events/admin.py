from django.contrib import admin

from rosterchief.admin import ClubAdminMixin

from .models import Event, EventSeries, Location, Opponent, Season


class OccurrenceInline(admin.TabularInline):
    model = Event
    fk_name = "series"
    extra = 0
    max_num = 0
    can_delete = False
    fields = ["title", "start", "end", "cancelled", "detached"]
    readonly_fields = fields
    show_change_link = True
    ordering = ["start"]


@admin.register(Event)
class EventAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["title", "kind", "start", "location", "opponent", "season", "cancelled", "is_live"]
    list_filter = ["kind", "season", "cancelled", "is_friendly", "club_wide", "detached"]
    search_fields = ["title", "competition", "external_game_id", "opponent__name", "location__name"]
    date_hierarchy = "start"
    ordering = ["-start"]
    list_select_related = ["location", "opponent", "season"]
    raw_id_fields = ["series", "created_by"]
    autocomplete_fields = ["location", "opponent", "invited_members", "excluded_members"]
    filter_horizontal = ["teams", "groups"]
    readonly_fields = ["live_score_polling_done_at", "deadline_reminder_sent_at"]


@admin.register(EventSeries)
class EventSeriesAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["title", "kind", "rrule", "dtstart", "until", "location", "opponent"]
    list_filter = ["kind", "club_wide"]
    search_fields = ["title", "rrule"]
    date_hierarchy = "dtstart"
    list_select_related = ["location", "opponent"]
    autocomplete_fields = ["location", "opponent", "invited_members", "excluded_members"]
    filter_horizontal = ["teams", "groups"]
    readonly_fields = ["generated_until"]
    inlines = [OccurrenceInline]


@admin.register(Location)
class LocationAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "address", "zip_code", "city", "is_home"]
    list_filter = ["is_home", "city"]
    search_fields = ["name", "address", "city", "zip_code"]
    ordering = ["name"]


@admin.register(Opponent)
class OpponentAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "logo"]
    search_fields = ["name"]
    ordering = ["name"]
    readonly_fields = ["alternative_names"]


@admin.register(Season)
class SeasonAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "start_date", "end_date"]
    date_hierarchy = "start_date"
    ordering = ["-start_date"]
