from django import forms
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Attendance, Competition, Event, EventReferee, EventSeries, Lineup, LineupSelection, Location, Opponent, RefereeSignup


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


class EventRefereeInline(admin.TabularInline):
    model = EventReferee
    extra = 0
    raw_id_fields = ["member", "assigned_by"]


@admin.register(EventSeries)
class EventSeriesAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "rrule", "dtstart", "until", "generated_until", "club"]
    list_filter = ["kind", "club"]
    search_fields = ["title"]
    autocomplete_fields = ["location", "opponent", "teams", "groups", "invited_members", "excluded_members"]
    fieldsets = [
        [None, {"fields": ["title", "kind"]}],
        [_("Recurrence"), {"fields": ["rrule", "dtstart", "until", "excluded_dates", "generated_until"]}],
        [_("Timing"), {"fields": ["duration", "gathering_offset", "deadline_offset"]}],
        [_("Audience"), {"fields": ["teams", "groups", "club_wide", "invited_members", "excluded_members"]}],
        [_("Where"), {"fields": ["location", "opponent"]}],
    ]


class EventAdminForm(forms.ModelForm):
    """The `competition` field is a plain CharField on Event (it just stores a
    name), but the admin should only ever offer a competition this club is
    actually allowed to use -- one whose feature flag is active for it, set
    from the control panel's Features page. A competition with no flag never
    appears at all."""

    class Meta:
        model = Event
        fields = [
            "title", "kind", "season", "series", "detached", "cancelled", "teams", "groups", "club_wide", "invited_members", "excluded_members",
            "start", "end", "gathering", "deadline", "location", "opponent",
            "competition", "external_game_id", "score_for", "score_against", "is_live", "max_referees",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        club = self.instance.club if self.instance.club_id else None
        competitions = Competition.objects.filter(flag__isnull=False).select_related("flag")
        if club is not None:
            competitions = [competition for competition in competitions if competition.flag.is_active_for_club(club)]
        self.fields["competition"] = forms.ChoiceField(
            choices=[("", "---------"), *[(competition.name, competition.name) for competition in competitions]],
            required=False,
            label=self.fields["competition"].label,
            help_text=_("Only competitions whose feature flag is active for this club are offered here."),
        )


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    form = EventAdminForm
    list_display = ["title", "kind", "start", "season", "series", "detached", "club"]
    list_filter = ["kind", "club", "teams", "detached", "cancelled"]
    search_fields = ["title"]
    date_hierarchy = "start"
    autocomplete_fields = ["season", "series", "location", "opponent", "teams", "groups", "invited_members", "excluded_members"]
    inlines = [AttendanceInline, EventRefereeInline]
    fieldsets = [
        [None, {"fields": ["title", "kind", "season"]}],
        [_("Series"), {"fields": ["series", "detached", "cancelled"]}],
        [_("Audience"), {"fields": ["teams", "groups", "club_wide", "invited_members", "excluded_members"]}],
        [_("When"), {"fields": ["start", "end", "gathering", "deadline"]}],
        [_("Where"), {"fields": ["location", "opponent"]}],
        [_("Game"), {"fields": ["competition", "external_game_id", "score_for", "score_against", "is_live", "max_referees"]}],
    ]


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = ["name", "sport_type", "module", "flag"]
    list_filter = ["sport_type"]
    search_fields = ["name", "module"]
    raw_id_fields = ["flag"]


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ["event", "member", "status", "showed_up"]
    list_filter = ["status", "showed_up", "event__kind"]
    search_fields = ["event__title", "member__first_name", "member__last_name"]
    raw_id_fields = ["event", "member"]


@admin.register(Lineup)
class LineupAdmin(admin.ModelAdmin):
    list_display = ["event", "team", "published_at", "created_by"]
    list_filter = ["team"]
    search_fields = ["event__title", "team__name"]
    raw_id_fields = ["event", "team", "created_by"]


@admin.register(LineupSelection)
class LineupSelectionAdmin(admin.ModelAdmin):
    list_display = ["lineup", "member"]
    search_fields = ["lineup__event__title", "member__first_name", "member__last_name"]
    raw_id_fields = ["lineup", "member"]


@admin.register(EventReferee)
class EventRefereeAdmin(admin.ModelAdmin):
    list_display = ["event", "display_name", "fee", "km", "total_payable", "assigned_by"]
    search_fields = ["event__title", "member__first_name", "member__last_name", "external_name"]
    raw_id_fields = ["event", "member", "assigned_by"]

    @admin.display(description=_("referee"))
    def display_name(self, obj):
        return obj.display_name

    @admin.display(description=_("total payable"))
    def total_payable(self, obj):
        return obj.total_payable


@admin.register(RefereeSignup)
class RefereeSignupAdmin(admin.ModelAdmin):
    list_display = ["event", "member", "status", "responded_at"]
    list_filter = ["status"]
    search_fields = ["event__title", "member__first_name", "member__last_name"]
    raw_id_fields = ["event", "member"]
