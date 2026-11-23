from django.contrib import admin

from rosterchief.admin import ClubAdminMixin

from .models import (
    BreakRule,
    DressingRoom,
    DressingRoomAssignment,
    Match,
    MatchOfficial,
    MatchReferee,
    ScheduleBlock,
    Tournament,
    TournamentField,
    TournamentGroup,
    TournamentRegistration,
    TournamentRegistrationPlayer,
    TournamentRosterEntry,
    TournamentStage,
    TournamentTask,
    TournamentTaskClaim,
    TournamentTeam,
)


@admin.register(Tournament)
class TournamentAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "status", "start_date", "end_date", "season", "registration_open"]
    list_filter = ["status", "season", "registration_open"]
    search_fields = ["name", "slug"]
    date_hierarchy = "start_date"
    raw_id_fields = ["event", "home_team"]


@admin.register(TournamentRegistration)
class TournamentRegistrationAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["foreign_name", "tournament", "team", "status"]
    list_filter = ["status"]
    search_fields = ["foreign_name", "foreign_contact_name", "foreign_contact_email"]
    raw_id_fields = ["tournament", "team", "reviewed_by"]


@admin.register(TournamentTeam)
class TournamentTeamAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["__str__", "tournament", "payment_status", "withdrawn_at"]
    list_filter = ["payment_status"]
    search_fields = ["foreign_name", "team__name"]
    raw_id_fields = ["tournament", "team", "registration"]


@admin.register(Match)
class MatchAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["__str__", "stage", "scheduled_start", "field", "status"]
    list_filter = ["status"]
    raw_id_fields = ["stage", "group", "field", "home_team", "away_team", "home_source_match", "away_source_match"]


def _register_plain(*models):
    """The remaining models: plain admin pages with every relation as a raw-id lookup (they can be large)."""
    for model in models:
        relations = [f.name for f in model._meta.get_fields() if f.many_to_one and f.concrete]
        admin.site.register(model, type(f"{model.__name__}Admin", (ClubAdminMixin, admin.ModelAdmin), {"raw_id_fields": relations}))


_register_plain(
    TournamentField,
    BreakRule,
    TournamentStage,
    TournamentGroup,
    TournamentRegistrationPlayer,
    TournamentRosterEntry,
    ScheduleBlock,
    MatchReferee,
    MatchOfficial,
    DressingRoom,
    DressingRoomAssignment,
    TournamentTask,
    TournamentTaskClaim,
)
