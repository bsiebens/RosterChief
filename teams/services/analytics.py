from urllib.parse import urlencode

from django.template.defaultfilters import slugify
from django.utils.translation import gettext_lazy as _

from club.models import Season
from events.services.game_stats import sync_game_stats, team_analytics
from events.services.strength_check import STRENGTH_LABELS, STRENGTHS

ANALYTICS_FLAG = "analytics"


def analytics_enabled(club) -> bool:
    """Whether the "analytics" feature flag is on for ``club``."""
    from features.models import Flag  # deferred: features imports back into club/events models

    flag = Flag.objects.filter(name=ANALYTICS_FLAG).first()
    return flag is not None and flag.is_active_for_club(club)


def analytics_for(team, season, scope="all", friendlies=True, competition=""):
    """``(analytics, competition)`` for the team analytics page. A competition no longer among the (friendly-filtered)
    games is dropped rather than leaving an empty page, so the competition actually applied is returned too."""
    if season is None:
        return None, competition
    analytics = team_analytics(team, season, scope, friendlies, competition)
    if competition and competition not in analytics["competitions"]:
        competition = ""
        analytics = team_analytics(team, season, scope, friendlies)
    return analytics, competition


def set_goal_strength(game, timeline, goal: str, strength: str) -> bool:
    """Fixes (or confirms) the strength of one goal of ``game``'s timeline: stored on the timeline's
    ``strength_overrides`` (which a re-fetch of the timeline leaves alone), then the game's stats are re-synced.
    False, changing nothing, when ``strength`` is unknown or ``goal`` isn't a goal of the timeline."""
    if strength not in STRENGTHS or not any(item.get("id") == goal and item["type"] == "goal" for item in (timeline.data or {}).get("events", [])):
        return False
    timeline.strength_overrides = {**timeline.strength_overrides, goal: strength}
    timeline.save(update_fields=["strength_overrides"])
    sync_game_stats(game, timeline.data)
    return True


def analytics_page_context(team, club, season, scope="all", friendlies=True, competition="") -> dict:
    """The context of the team analytics page (and its PDF): the season switcher, the applied filters, the numbers
    and the scope links. ``scope`` is assumed valid (one of ``events.services.game_stats.SCOPES``)."""
    analytics, competition = analytics_for(team, season, scope, friendlies, competition)

    def query(**changes):
        params = {"season": season.pk if season else "", "games": scope, "friendlies": "" if friendlies else "0", "competition": competition, **changes}
        return urlencode({key: value for key, value in params.items() if value})

    return {
        "seasons": Season.objects.filter(club=club).order_by("-start_date"),
        "selected_season": season,
        "strengths": [(key, label) for key, label in STRENGTH_LABELS.items()],
        "scope": scope,
        "friendlies": friendlies,
        "analytics": analytics,
        "competition": competition,
        "scopes": [(key, label, query(games=key)) for key, label in (("all", _("All games")), ("home", _("Home")), ("away", _("Away")), ("last5", _("Last 5")))],
        "friendlies_query": query(friendlies="0" if friendlies else ""),
    }


def analytics_pdf_context(team, club, season, scope, friendlies, competition, generated_at) -> dict | None:
    """The context of the analytics PDF, or None when there are no finished games with a score to export."""
    context = analytics_page_context(team, club, season, scope, friendlies, competition) | {"team": team, "object": team, "club": club, "generated_at": generated_at}
    analytics = context["analytics"]
    if not analytics or not analytics["games"]:
        return None
    context["scope_label"] = next((label for key, label, _query in context["scopes"] if key == context["scope"]), "")
    context["leaderboards"] = [(_("Top scorers"), analytics["top_scorers"]), (_("Top assists"), analytics["top_assists"]), (_("Top points"), analytics["top_points"]), (_("In the box (penalty min.)"), analytics["top_offenders"])]
    return context


def analytics_pdf_filename(team, season) -> str:
    return f"analytics-{slugify(team.name)}-{season.start_date:%Y}.pdf"
