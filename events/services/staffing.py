"""The event-staffing dashboard: which upcoming club-managed home games still need referees / officials.

One definition for the /manage ``referees/management/`` page (management.views.RefereeManagementDashboardView)
and the management API (api.panel.routers.referee_management), so the two cannot drift. The query helpers
(``upcoming_games_needing_*``, ``remaining_season_games_needing_*``, ``referee_workload_stats``) also feed the
nav badge (``games_missing_referee_or_official_count``).
"""

from datetime import timedelta

from django.db.models import Count, F, Sum
from django.utils import timezone

from club.services.access import current_season
from events.models import Event, EventReferee, OfficialSignup, RefereeSignup
from events.services.crew_panel import signup_groups
from events.services.officials import eligible_officials, needs_official_management, officials_enabled_for
from events.services.referees import conflicting_events_for_members, eligible_referees, needs_referee_management, referee_roster
from teams.models import Team

RANGE_CHOICES = ["week", "two_weeks", "10", "25", "50"]
DEFAULT_RANGE = "10"
NEED_CHOICES = ["all", "both", "referees", "officials"]
DEFAULT_NEED = "all"


def club_games_needing_management(club, lookup, *, upcoming_only=False, season=None):
    """The shared shape behind upcoming_games_needing_referee_management,
    upcoming_games_needing_official_management, remaining_season_games_needing_referee_management
    and remaining_season_games_needing_official_management below -- only which governance
    flag a team must have (`lookup`, e.g.
    {"teams__referee_management": Team.RefereeManagement.CLUB}) and the date
    scoping (from now on, or bounded to a season, or neither) differ between
    them; federation-managed teams never match `lookup` at all, see
    events.services.referees/officials.needs_*_management (the kind/home-ground
    condition mirrors Event.is_home_fixture -- a home tournament needs
    officiating arranged just as much as a home game)."""
    queryset = Event.objects.filter(
        club=club,
        kind__in=[Event.EventKind.GAME, Event.EventKind.TOURNAMENT],
        cancelled=False,
        location__is_home=True,
        **lookup,
    ).distinct()
    if upcoming_only:
        queryset = queryset.filter(start__gte=timezone.now())
    if season is not None:
        queryset = queryset.filter(start__date__gte=season.start_date, start__date__lte=season.end_date)
    return queryset.order_by("start")


def upcoming_games_needing_referee_management(club):
    """Upcoming home games/tournaments a club-arranged referee is needed for --
    the base query behind the dashboard's own game list (sliced to whatever range is selected there)."""
    return club_games_needing_management(club, {"teams__referee_management": Team.RefereeManagement.CLUB}, upcoming_only=True)


def referee_workload_stats(club):
    """Games refereed (and fees paid) per member this season -- external
    referees excluded (member is None for those, nothing to group by).
    Current season only, same scope as everything else on this dashboard
    being "the season we're in", not all-time history. Ordered by games
    descending -- the dashboard's own bar chart and table both read off
    this directly, no separate sort."""
    season = current_season(club)
    queryset = EventReferee.objects.filter(event__club=club, member__isnull=False)
    if season is not None:
        # By date range, not event__season=season -- most events are created
        # with that field left blank (help_text: "derived from the start
        # date when left blank"), so matching the FK directly would silently
        # exclude almost everything.
        queryset = queryset.filter(event__start__date__gte=season.start_date, event__start__date__lte=season.end_date)
    return list(queryset.values("member__id", "member__first_name", "member__last_name").annotate(games=Count("id"), total_fees=Sum("fee")).order_by("-games", "member__last_name"))


def upcoming_games_needing_official_management(club):
    """The officials counterpart to upcoming_games_needing_referee_management
    -- same shape, same reasoning, keyed off Team.official_management
    instead."""
    return club_games_needing_management(club, {"teams__official_management": Team.OfficialManagement.CLUB}, upcoming_only=True)


def remaining_season_games_needing_referee_management(club, season):
    """Every club-managed home game/tournament needing a referee from now
    until the end of `season` -- what the dashboard's status
    KPI cards and the nav badge (games_missing_referee_or_official_count)
    both count from, independent of the dashboard's range selector. Upcoming
    only: a past game nobody refereed isn't something left to arrange, and
    counting those is what made the cards read far higher than the list."""
    return club_games_needing_management(club, {"teams__referee_management": Team.RefereeManagement.CLUB}, upcoming_only=True, season=season)


def remaining_season_games_needing_official_management(club, season):
    """The officials counterpart to remaining_season_games_needing_referee_management."""
    return club_games_needing_management(club, {"teams__official_management": Team.OfficialManagement.CLUB}, upcoming_only=True, season=season)


def games_missing_referee_or_official_count(club):
    """Distinct upcoming games this season still short a referee or an
    official (nobody assigned *or* only partially staffed) -- the nav badge's
    own number. Same set the dashboard's status KPI cards count from, so the
    badge always equals the distinct games behind "Without a referee" +
    "Partially staffed" and their officials counterparts, a game short both
    counted once (a naive sum of the two would count it twice)."""
    season = current_season(club)
    missing_ids = set(remaining_season_games_needing_referee_management(club, season).annotate(referee_count=Count("referees", distinct=True)).filter(referee_count__lt=F("max_referees")).values_list("pk", flat=True))
    if officials_enabled_for(club):
        missing_ids |= set(remaining_season_games_needing_official_management(club, season).annotate(official_count=Count("officials", distinct=True)).filter(official_count__lt=F("max_officials")).values_list("pk", flat=True))
    return len(missing_ids)


def clean_range(value) -> str:
    return value if value in RANGE_CHOICES else DEFAULT_RANGE


def clean_need(value, officials_enabled) -> str:
    """With officials off for the club there's no officials bucket to switch to, and "either" is just "referees"."""
    choices = NEED_CHOICES if officials_enabled else ["all", "referees"]
    return value if value in choices else DEFAULT_NEED


def _apply_range(queryset, range_choice):
    """The "week/two_weeks -> date filter, else -> next N" window -- applied once, to
    every managed upcoming game, before the need chips narrow it down."""
    if range_choice in ("week", "two_weeks"):
        today = timezone.localdate()
        end_of_this_week = today + timedelta(days=6 - today.weekday())
        end_date = end_of_this_week + timedelta(days=7) if range_choice == "two_weeks" else end_of_this_week
        return queryset.filter(start__date__lte=end_date)
    return queryset[: int(range_choice)]


def _status_kpis(games_qs, related_name, max_field):
    """Counts every game in `games_qs` into none/some/full, plus how many of those have at least
    one assignment with no fee set. Kept light: `values_list` pulls two integers per game, and
    fees_pending is one DB-side existence count rather than a Python loop."""
    no_one = understaffed = fully_staffed = 0
    counts = games_qs.annotate(assigned_count=Count(related_name, distinct=True)).values_list("pk", "assigned_count", max_field)
    for _pk, assigned_count, max_allowed in counts:
        if assigned_count == 0:
            no_one += 1
        elif assigned_count < max_allowed:
            understaffed += 1
        else:
            fully_staffed += 1
    # fee's default is 0.00, never null: "no fee set yet" and "a real row with fee=0" are the same
    # value (EventReferee.fee_pending); a row marked no_fee is deliberately unpaid, not pending.
    fees_pending = games_qs.filter(**{f"{related_name}__fee": 0, f"{related_name}__no_fee": False}).distinct().count()
    return no_one, understaffed, fully_staffed, fees_pending


def _annotate_candidates(candidates, event):
    conflicts_by_member = conflicting_events_for_members(candidates, event)
    for candidate in candidates:
        conflicts = conflicts_by_member[candidate.pk]
        candidate.has_conflict = bool(conflicts)
        candidate.conflict_titles = ", ".join(conflict.title for conflict in conflicts)


def staffing_dashboard(club, *, range_choice: str, need_choice: str, officials_enabled: bool) -> dict:
    """Everything the dashboard shows. ``range_choice`` / ``need_choice`` must already be cleaned
    (``clean_range`` / ``clean_need``). The range picks which games are in view (every club-managed
    upcoming home game, fully staffed ones included); the need bucket then narrows that same set, so
    each bucket's count is a subset of "all". The KPI cards are season-wide, not range/need-bound."""
    # Not a queryset `|` union (different distinct/join shapes): combine by pk.
    referee_managed = upcoming_games_needing_referee_management(club)
    official_managed = upcoming_games_needing_official_management(club) if officials_enabled else Event.objects.none()
    managed_ids = set(referee_managed.values_list("pk", flat=True)) | set(official_managed.values_list("pk", flat=True))
    in_view_ids = list(_apply_range(Event.objects.filter(pk__in=managed_ids).order_by("start").values_list("pk", flat=True), range_choice))

    # "Short" means not yet fully staffed (count < max): nobody assigned *or* understaffed.
    short_referee_ids = set(referee_managed.filter(pk__in=in_view_ids).annotate(referee_count=Count("referees", distinct=True)).filter(referee_count__lt=F("max_referees")).values_list("pk", flat=True))
    short_official_ids = set(official_managed.filter(pk__in=in_view_ids).annotate(official_count=Count("officials", distinct=True)).filter(official_count__lt=F("max_officials")).values_list("pk", flat=True))
    buckets = {"all": set(in_view_ids), "referees": short_referee_ids, "officials": short_official_ids, "both": short_referee_ids | short_official_ids}
    need_counts = {key: len(ids) for key, ids in buckets.items()}

    queryset = Event.objects.filter(pk__in=buckets[need_choice]).order_by("start").select_related("location", "opponent").prefetch_related("teams", "referees__member", "referees__assigned_by", "officials__member", "officials__assigned_by")
    games = list(queryset)

    referee_roster_cache = referee_roster(club)
    for game in games:
        game.needs_referees = needs_referee_management(game)
        game.referee_rows = list(game.referees.all())
        game.referees_full = len(game.referee_rows) >= game.max_referees
        game.referee_candidates = []
        game.fees_pending = any(referee.fee_pending for referee in game.referee_rows)
        game.pending_signups = signup_groups(game.referee_signups.exclude(status=RefereeSignup.Status.CONFIRMED).select_related("member"), RefereeSignup)
        if game.needs_referees and not game.referees_full:
            game.referee_candidates = list(eligible_referees(game, include_expired=True, roster=referee_roster_cache))
            _annotate_candidates(game.referee_candidates, game)

        game.needs_officials = officials_enabled and needs_official_management(game)
        game.official_rows = list(game.officials.all())
        game.officials_full = len(game.official_rows) >= game.max_officials
        game.official_candidates = []
        game.official_fees_pending = any(official.fee_pending for official in game.official_rows)
        game.pending_official_signups = signup_groups(game.official_signups.exclude(status=OfficialSignup.Status.CONFIRMED).select_related("member"), OfficialSignup)
        if game.needs_officials and not game.officials_full:
            game.official_candidates = list(eligible_officials(game))
            _annotate_candidates(game.official_candidates, game)

    # Every upcoming game left this season, not range/need-bound: a stable "what's still ahead"
    # picture that also matches the nav badge (games_missing_referee_or_official_count).
    season = current_season(club)
    kpi_no_referee, kpi_understaffed, kpi_fully_staffed, kpi_fees_pending = _status_kpis(remaining_season_games_needing_referee_management(club, season), "referees", "max_referees")
    if officials_enabled:
        kpi_no_official, kpi_officials_understaffed, kpi_officials_fully_staffed, kpi_official_fees_pending = _status_kpis(remaining_season_games_needing_official_management(club, season), "officials", "max_officials")
    else:
        kpi_no_official = kpi_officials_understaffed = kpi_officials_fully_staffed = kpi_official_fees_pending = 0

    stats = referee_workload_stats(club)
    kpi_active_referees = len(stats)
    kpi_total_assignments = sum(row["games"] for row in stats)
    kpi_avg_games_per_referee = round(kpi_total_assignments / kpi_active_referees, 1) if kpi_active_referees else 0

    return {
        "games": games,
        "range_choice": range_choice,
        "need_choice": need_choice,
        "need_counts": need_counts,
        "kpi_total": len(games),
        "kpi_no_referee": kpi_no_referee,
        "kpi_understaffed": kpi_understaffed,
        "kpi_fully_staffed": kpi_fully_staffed,
        "kpi_fees_pending": kpi_fees_pending,
        "kpi_active_referees": kpi_active_referees,
        "kpi_total_assignments": kpi_total_assignments,
        "kpi_avg_games_per_referee": kpi_avg_games_per_referee,
        "officials_enabled": officials_enabled,
        "kpi_no_official": kpi_no_official,
        "kpi_officials_understaffed": kpi_officials_understaffed,
        "kpi_officials_fully_staffed": kpi_officials_fully_staffed,
        "kpi_official_fees_pending": kpi_official_fees_pending,
        "workload": stats,
    }
