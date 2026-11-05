from django.contrib import admin

from rosterchief.admin import ClubAdminMixin

from .models import (
    Attendance,
    Event,
    EventAttachment,
    EventFollow,
    EventSeries,
    EventTask,
    EventTaskClaim,
    GameFollow,
    GameMoment,
    GameStat,
    GameTimeline,
    Lineup,
    LineupSelection,
    Location,
    Opponent,
    Season,
)


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

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """``competition`` only offers the competitions this club's features allow (events.services.competitions)."""
        if db_field.name == "competition":
            from django import forms

            from .services.competitions import available_competitions

            names = list(available_competitions(getattr(request, "tenant", None)).values_list("name", flat=True))
            return forms.TypedChoiceField(choices=[("", "---------"), *[(name, name) for name in names]], required=False, label=db_field.verbose_name, help_text=db_field.help_text)
        return super().formfield_for_dbfield(db_field, request, **kwargs)


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


@admin.register(Attendance)
class AttendanceAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["event", "member", "status", "showed_up", "answered_by_coach"]
    list_filter = ["status", "showed_up", "answered_by_coach"]
    search_fields = ["event__title", "member__first_name", "member__last_name"]
    list_select_related = ["event", "member"]
    raw_id_fields = ["event", "member", "coach_set_by"]


class LineupSelectionInline(admin.TabularInline):
    model = LineupSelection
    extra = 0
    raw_id_fields = ["member"]


@admin.register(Lineup)
class LineupAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["event", "team", "published_at", "scheduled_publish_at"]
    search_fields = ["event__title", "team__name"]
    list_select_related = ["event", "team"]
    raw_id_fields = ["event", "team", "created_by"]
    inlines = [LineupSelectionInline]


class EventTaskClaimInline(admin.TabularInline):
    model = EventTaskClaim
    extra = 0
    raw_id_fields = ["member"]


@admin.register(EventTask)
class EventTaskAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["title", "event", "needed_quantity"]
    search_fields = ["title", "event__title"]
    list_select_related = ["event"]
    raw_id_fields = ["event", "created_by"]
    inlines = [EventTaskClaimInline]


@admin.register(EventAttachment)
class EventAttachmentAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "event", "size", "uploaded_by", "created"]
    search_fields = ["name", "event__title"]
    list_select_related = ["event", "uploaded_by"]
    raw_id_fields = ["event", "uploaded_by"]
    readonly_fields = ["size"]


@admin.register(GameTimeline)
class GameTimelineAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["event", "source", "status", "fetched_at", "changed_at", "finalized_at"]
    list_filter = ["status", "source"]
    search_fields = ["event__title"]
    list_select_related = ["event"]
    raw_id_fields = ["event"]
    readonly_fields = ["data_hash", "fetched_at", "changed_at", "finalized_at"]


@admin.register(GameMoment)
class GameMomentAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["event", "kind", "game_seconds", "notifiable", "delivered_at"]
    list_filter = ["kind", "notifiable"]
    search_fields = ["event__title", "key"]
    list_select_related = ["event"]
    raw_id_fields = ["event"]


@admin.register(GameStat)
class GameStatAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["event", "team", "stat_type", "value", "is_ours", "member", "player_name"]
    list_filter = ["stat_type", "is_ours", "season"]
    search_fields = ["event__title", "player_name", "member__first_name", "member__last_name"]
    list_select_related = ["event", "team", "member"]
    raw_id_fields = ["event", "team", "season", "member"]


@admin.register(GameFollow)
class GameFollowAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["user", "team", "created"]
    search_fields = ["user__email", "team__name"]
    list_select_related = ["user", "team"]
    raw_id_fields = ["user", "team"]


@admin.register(EventFollow)
class EventFollowAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["user", "event", "created"]
    search_fields = ["user__email", "event__title"]
    list_select_related = ["user", "event"]
    raw_id_fields = ["user", "event"]


@admin.register(LineupSelection)
class LineupSelectionAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["lineup", "member", "created"]
    search_fields = ["lineup__event__title", "member__first_name", "member__last_name"]
    list_select_related = ["lineup__event", "lineup__team", "member"]
    raw_id_fields = ["lineup", "member"]


@admin.register(EventTaskClaim)
class EventTaskClaimAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["task", "member", "created"]
    search_fields = ["task__title", "member__first_name", "member__last_name"]
    list_select_related = ["task__event", "member"]
    raw_id_fields = ["task", "member"]
