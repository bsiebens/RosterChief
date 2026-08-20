from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership, TeamPhoto


class TeamMembershipInline(admin.TabularInline):
    """Roster shown on the Team page."""

    model = TeamMembership
    extra = 0
    raw_id_fields = ("member",)


class StaffAssignmentInline(admin.TabularInline):
    """Coaching / staff shown on the Team page."""

    model = StaffAssignment
    extra = 0
    raw_id_fields = ("member",)


class TeamPhotoInline(admin.TabularInline):
    """One photo per season, shown on the Team page."""

    model = TeamPhoto
    extra = 0


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "short_name", "club", "referee_management"]
    list_filter = ["club", "referee_management"]
    search_fields = ["name", "short_name"]
    inlines = [TeamMembershipInline, StaffAssignmentInline, TeamPhotoInline]


@admin.register(TeamPhoto)
class TeamPhotoAdmin(admin.ModelAdmin):
    list_display = ["team", "season"]
    list_filter = ["team__club", "season"]
    search_fields = ["team__name"]


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ["name", "short_name", "club", "staff_position", "ordering"]
    list_filter = ["club", "staff_position"]
    search_fields = ["name", "short_name"]
    ordering = ["ordering", "name"]


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ["team", "member", "season", "position", "jersey_number", "is_captain", "is_alternate_captain"]
    list_filter = ["team", "season", "position", "is_captain"]
    search_fields = ["team__name", "member__first_name", "member__last_name"]
    raw_id_fields = ["member"]
    fieldsets = [
        [None, {"fields": ["team", "season", "member", "position"]}],
        [_("Squad"), {"fields": ["jersey_number", "is_captain", "is_alternate_captain"]}],
    ]


@admin.register(StaffAssignment)
class StaffAssignmentAdmin(admin.ModelAdmin):
    list_display = ["team", "member", "season", "position"]
    list_filter = ["team", "season", "position"]
    search_fields = ["team__name", "member__first_name", "member__last_name"]
    raw_id_fields = ["member"]


@admin.register(RefereeLevel)
class RefereeLevelAdmin(admin.ModelAdmin):
    list_display = ["name", "club", "ordering", "inherits_from", "team_list"]
    list_filter = ["club"]
    search_fields = ["name"]
    autocomplete_fields = ["teams", "inherits_from"]
    ordering = ["club", "ordering", "name"]

    @admin.display(description=_("teams"))
    def team_list(self, obj):
        return ", ".join(team.name for team in obj.teams.all())


@admin.register(RefereeProfile)
class RefereeProfileAdmin(admin.ModelAdmin):
    list_display = ["member", "level", "valid_until", "is_eligible"]
    list_filter = ["level"]
    search_fields = ["member__first_name", "member__last_name"]
    autocomplete_fields = ["member"]

    @admin.display(description=_("eligible"), boolean=True)
    def is_eligible(self, obj):
        return obj.is_eligible
