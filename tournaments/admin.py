from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    DressingRoom,
    DressingRoomAssignment,
    Match,
    MatchOfficial,
    MatchReferee,
    Tournament,
    TournamentField,
    TournamentGroup,
    TournamentRegistration,
    TournamentRosterEntry,
    TournamentStage,
    TournamentTask,
    TournamentTaskClaim,
    TournamentTeam,
)


class TournamentStageInline(admin.TabularInline):
    """Format phases shown on the Tournament page."""

    model = TournamentStage
    extra = 0


class TournamentFieldInline(admin.TabularInline):
    """Bookable surfaces shown on the Tournament page."""

    model = TournamentField
    extra = 0
    raw_id_fields = ["location"]


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ["name", "club", "season", "status", "start_date", "end_date"]
    list_filter = ["club", "status", "season"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    raw_id_fields = ["event"]
    inlines = [TournamentStageInline, TournamentFieldInline]


@admin.register(TournamentField)
class TournamentFieldAdmin(admin.ModelAdmin):
    list_display = ["name", "tournament", "location"]
    list_filter = ["tournament__club"]
    search_fields = ["name", "tournament__name"]
    raw_id_fields = ["tournament", "location"]


class TournamentGroupInline(admin.TabularInline):
    """Groups shown on the Tournament Stage page -- only populated for
    group-flavoured stages."""

    model = TournamentGroup
    extra = 0


@admin.register(TournamentStage)
class TournamentStageAdmin(admin.ModelAdmin):
    # Not club-scoped itself -- club reached through `tournament`, same idiom
    # as club.admin.MemberRequirementStatusAdmin's `requirement__club`.
    list_display = ["name", "tournament", "sequence", "format", "status"]
    list_filter = ["tournament__club", "format", "status"]
    search_fields = ["name", "tournament__name"]
    raw_id_fields = ["tournament"]
    inlines = [TournamentGroupInline]


@admin.register(TournamentGroup)
class TournamentGroupAdmin(admin.ModelAdmin):
    # Not club-scoped itself -- club reached through `stage__tournament`.
    list_display = ["name", "stage", "sequence"]
    list_filter = ["stage__tournament__club"]
    search_fields = ["name", "stage__name", "stage__tournament__name"]
    raw_id_fields = ["stage"]


@admin.register(TournamentRegistration)
class TournamentRegistrationAdmin(admin.ModelAdmin):
    # Not club-scoped itself -- club reached through `tournament`.
    list_display = ["tournament", "display_name", "status", "submitted_at", "reviewed_by"]
    list_filter = ["tournament__club", "status"]
    search_fields = ["tournament__name", "team__name", "foreign_name", "foreign_contact_email"]
    raw_id_fields = ["tournament", "team", "reviewed_by"]

    @admin.display(description=_("team"))
    def display_name(self, obj):
        return obj.display_name


class TournamentRosterEntryInline(admin.TabularInline):
    """Roster shown on the Tournament Team page."""

    model = TournamentRosterEntry
    extra = 0
    raw_id_fields = ["member"]


@admin.register(TournamentTeam)
class TournamentTeamAdmin(admin.ModelAdmin):
    list_display = ["display_name", "tournament", "club", "seed", "withdrawn_at"]
    list_filter = ["club", "tournament", "withdrawn_at"]
    search_fields = ["team__name", "foreign_name"]
    raw_id_fields = ["tournament", "registration", "team"]
    inlines = [TournamentRosterEntryInline]

    @admin.display(description=_("team"))
    def display_name(self, obj):
        return obj.display_name


@admin.register(TournamentRosterEntry)
class TournamentRosterEntryAdmin(admin.ModelAdmin):
    # Not club-scoped itself -- club reached through `tournament_team`.
    list_display = ["display_name", "tournament_team", "jersey_number", "position_label"]
    list_filter = ["tournament_team__tournament__club"]
    search_fields = ["member__first_name", "member__last_name", "foreign_first_name", "foreign_last_name"]
    raw_id_fields = ["tournament_team", "member"]

    @admin.display(description=_("player"))
    def display_name(self, obj):
        return obj.display_name


class MatchRefereeInline(admin.TabularInline):
    """Per-match referees shown on the Match page -- mirrors
    events.admin.EventRefereeInline (small numbers per match/event)."""

    model = MatchReferee
    extra = 0
    raw_id_fields = ["member", "assigned_by"]


class MatchOfficialInline(admin.TabularInline):
    """Per-match officials shown on the Match page -- mirrors
    events.admin.EventOfficialInline."""

    model = MatchOfficial
    extra = 0
    raw_id_fields = ["member", "assigned_by"]


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    # Not club-scoped itself -- club reached through `stage__tournament`. Every
    # FK gets raw_id_fields: a tournament can have many teams/matches, and
    # home_source_match/away_source_match are self-referential, so plain
    # <select> dropdowns would be unusable (same reasoning as
    # teams.admin.TeamMembershipInline's raw_id_fields=("member",)).
    list_display = ["stage", "round_number", "round_label", "home_display", "away_display", "status", "score_home", "score_away"]
    list_filter = ["stage__tournament__club", "status", "went_to_overtime"]
    search_fields = ["stage__name", "stage__tournament__name"]
    raw_id_fields = [
        "stage",
        "group",
        "field",
        "home_team",
        "away_team",
        "home_source_match",
        "away_source_match",
        "home_source_group",
        "away_source_group",
    ]
    inlines = [MatchRefereeInline, MatchOfficialInline]

    @admin.display(description=_("home"))
    def home_display(self, obj):
        return obj.home_display

    @admin.display(description=_("away"))
    def away_display(self, obj):
        return obj.away_display


@admin.register(MatchReferee)
class MatchRefereeAdmin(admin.ModelAdmin):
    # Not club-scoped itself -- club reached through `match__stage__tournament`.
    list_display = ["match", "display_name", "status", "assigned_by"]
    list_filter = ["status"]
    search_fields = ["match__stage__name", "member__first_name", "member__last_name", "external_name"]
    raw_id_fields = ["match", "member", "assigned_by"]

    @admin.display(description=_("referee"))
    def display_name(self, obj):
        return obj.display_name


@admin.register(MatchOfficial)
class MatchOfficialAdmin(admin.ModelAdmin):
    # Not club-scoped itself -- club reached through `match__stage__tournament`.
    list_display = ["match", "display_name", "role_label", "assigned_by"]
    search_fields = ["match__stage__name", "member__first_name", "member__last_name", "external_name", "role_label"]
    raw_id_fields = ["match", "member", "assigned_by"]

    @admin.display(description=_("official"))
    def display_name(self, obj):
        return obj.display_name


class DressingRoomAssignmentInline(admin.TabularInline):
    """Bookings shown on the Dressing Room page."""

    model = DressingRoomAssignment
    extra = 0
    raw_id_fields = ["tournament_team"]


@admin.register(DressingRoom)
class DressingRoomAdmin(admin.ModelAdmin):
    list_display = ["name", "club", "location", "capacity"]
    list_filter = ["club"]
    search_fields = ["name"]
    raw_id_fields = ["location"]
    inlines = [DressingRoomAssignmentInline]


@admin.register(DressingRoomAssignment)
class DressingRoomAssignmentAdmin(admin.ModelAdmin):
    # Not club-scoped itself -- club reached through `dressing_room`.
    list_display = ["dressing_room", "tournament_team", "assigned_from", "assigned_until"]
    list_filter = ["dressing_room__club"]
    search_fields = ["dressing_room__name", "tournament_team__team__name", "tournament_team__foreign_name"]
    raw_id_fields = ["dressing_room", "tournament_team"]


class TournamentTaskClaimInline(admin.TabularInline):
    """Volunteer sign-ups shown on the Tournament Task page."""

    model = TournamentTaskClaim
    extra = 0
    raw_id_fields = ["member"]


@admin.register(TournamentTask)
class TournamentTaskAdmin(admin.ModelAdmin):
    # Not club-scoped itself -- club reached through `tournament`.
    list_display = ["tournament", "title", "starts_at", "ends_at", "needed_quantity", "claim_count", "created_by"]
    list_filter = ["tournament__club"]
    search_fields = ["title", "tournament__name", "location_label"]
    raw_id_fields = ["tournament", "created_by"]
    inlines = [TournamentTaskClaimInline]

    @admin.display(description=_("claims"))
    def claim_count(self, obj):
        return obj.claims.count()


@admin.register(TournamentTaskClaim)
class TournamentTaskClaimAdmin(admin.ModelAdmin):
    list_display = ["task", "member", "claimed_at"]
    search_fields = ["task__title", "member__first_name", "member__last_name"]
    raw_id_fields = ["task", "member"]
