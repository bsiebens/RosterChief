from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Position, StaffAssignment, Team, TeamMembership


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


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "short_name", "club"]
    list_filter = ["club"]
    search_fields = ["name", "short_name"]
    inlines = [TeamMembershipInline, StaffAssignmentInline]


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
