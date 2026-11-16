from django.contrib import admin

from rosterchief.admin import ClubAdminMixin

from .models import (
    NumberPool,
    NumberReservation,
    OfficialLevel,
    OfficialProfile,
    Position,
    RefereeLevel,
    RefereeProfile,
    StaffAssignment,
    Team,
    TeamMembership,
    TeamPhoto,
)


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 0
    autocomplete_fields = ["member"]
    fields = ["member", "season", "position", "jersey_number", "is_captain", "is_alternate_captain"]
    show_change_link = True


class StaffAssignmentInline(admin.TabularInline):
    model = StaffAssignment
    extra = 0
    autocomplete_fields = ["member"]
    fields = ["member", "season", "position"]


class TeamPhotoInline(admin.TabularInline):
    model = TeamPhoto
    extra = 0
    fields = ["season", "image"]


class NumberReservationInline(admin.TabularInline):
    model = NumberReservation
    extra = 0
    fields = ["number", "note", "reserved_by"]
    raw_id_fields = ["reserved_by"]


@admin.register(Team)
class TeamAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "short_name", "ordering", "age_min", "age_max", "pool", "feeds_into", "tournament_only"]
    list_filter = ["tournament_only", "pool"]
    search_fields = ["name", "short_name"]
    list_select_related = ["pool", "feeds_into"]
    autocomplete_fields = ["feeds_into"]
    inlines = [TeamMembershipInline, StaffAssignmentInline, TeamPhotoInline]


@admin.register(TeamMembership)
class TeamMembershipAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["member", "team", "season", "position", "jersey_number", "is_captain", "is_alternate_captain"]
    list_filter = ["season", "team", "position", "is_captain", "is_alternate_captain"]
    search_fields = ["member__first_name", "member__last_name", "team__name"]
    list_select_related = ["member", "team", "season", "position"]
    autocomplete_fields = ["member", "team"]


@admin.register(StaffAssignment)
class StaffAssignmentAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["member", "team", "season", "position"]
    list_filter = ["season", "team", "position"]
    search_fields = ["member__first_name", "member__last_name", "team__name"]
    list_select_related = ["member", "team", "season", "position"]
    autocomplete_fields = ["member", "team"]


@admin.register(Position)
class PositionAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "short_name", "ordering", "staff_position", "management_position"]
    list_filter = ["staff_position", "management_position"]
    search_fields = ["name", "short_name"]
    ordering = ["ordering", "name"]


@admin.register(NumberPool)
class NumberPoolAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "min_number", "max_number", "min_age_gap_years"]
    search_fields = ["name"]
    inlines = [NumberReservationInline]


@admin.register(NumberReservation)
class NumberReservationAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["pool", "number", "note", "reserved_by"]
    list_filter = ["pool"]
    search_fields = ["note", "pool__name"]
    list_select_related = ["pool", "reserved_by"]
    raw_id_fields = ["reserved_by"]


class _LevelAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "inherits_from"]
    search_fields = ["name"]
    ordering = ["name"]
    list_select_related = ["inherits_from"]
    filter_horizontal = ["teams"]


@admin.register(OfficialLevel)
class OfficialLevelAdmin(_LevelAdmin):
    pass


@admin.register(RefereeLevel)
class RefereeLevelAdmin(_LevelAdmin):
    pass


class _ProfileAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["member", "level", "valid_until", "currently_valid"]
    list_filter = ["level", "valid_until"]
    search_fields = ["member__first_name", "member__last_name"]
    list_select_related = ["member", "level"]
    autocomplete_fields = ["member"]
    date_hierarchy = "valid_until"

    @admin.display(boolean=True, description="Currently valid")
    def currently_valid(self, obj):
        return obj.is_currently_valid


@admin.register(OfficialProfile)
class OfficialProfileAdmin(_ProfileAdmin):
    pass


@admin.register(RefereeProfile)
class RefereeProfileAdmin(_ProfileAdmin):
    filter_horizontal = ["excluded_teams"]


@admin.register(TeamPhoto)
class TeamPhotoAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["team", "season", "image"]
    list_filter = ["season", "team"]
    search_fields = ["team__name"]
    list_select_related = ["team", "season"]
    autocomplete_fields = ["team"]
