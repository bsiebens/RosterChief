"""Read-only attendance reports for the management app's attendance boxes (team page, member profile, overview).

Built on the statistics in ``events.services.attendance`` (turnout = present / (present + absent) over past events).
Nothing here exposes ``coach_note``. A *session* is a past, non-cancelled event in the season (for a team: listing
that team)."""

from __future__ import annotations

from django.utils import timezone

from events.models import Attendance, Event, Season
from events.services import attendance as att

TOP = 3
RECENT = 10
SPARKLINE = 12
NO_SHOWS = 10


def _person(member, **extra) -> dict:
    return {"id": member.pk, "name": member.get_full_name(), **extra}


def _event_ref(event) -> dict:
    return {"id": event.pk, "title": event.title, "kind": event.kind, "start": event.start}


def sessions(season: Season | None, **filters) -> int:
    """Past non-cancelled events in ``season`` matching ``filters``."""
    if season is None:
        return 0
    return Event.objects.filter(att._in_season(season, prefix=""), start__lt=timezone.now(), cancelled=False, **filters).distinct().count()


def team_report(team, season: Season | None) -> dict:
    season = season if season is not None else Season.get_current()
    rankings = att.player_attendance_rankings(team, season)
    top = rankings[:TOP]
    top_ids = {entry["member"].pk for entry in top}
    attention = [entry for entry in reversed(rankings) if entry["member"].pk not in top_ids][:TOP]
    no_shows = list(att.team_no_shows(team, season)[:NO_SHOWS]) if season else []
    return {
        "season": {"id": season.pk, "name": season.name} if season else None,
        "rate": att.team_attendance_rate(team, season),
        "sessions": sessions(season, teams=team),
        "top_attenders": [_person(e["member"], rate=e["rate"], responses=e["responses"]) for e in top],
        "needs_attention": [_person(e["member"], rate=e["rate"], responses=e["responses"]) for e in attention],
        "missed_recent_practices": [_person(m) for m in att.players_who_missed_recent_practices(team, season)],
        "no_shows": [{"member": _person(row.member), "event": _event_ref(row.event)} for row in no_shows],
        "no_show_count": att.team_no_shows(team, season).count() if season else 0,
    }


def member_report(member, season: Season | None) -> dict:
    season = season if season is not None else Season.get_current()
    counts = att.member_attendance_counts(member, season)
    bars = att.member_attendance_sparkline(member, season, limit=SPARKLINE)
    recent = []
    if season is not None:
        rows = Attendance.objects.filter(att._in_season(season), member=member, event__start__lt=timezone.now()).select_related("event").order_by("-event__start")[:RECENT]
        recent = [{"event": _event_ref(row.event), "status": row.status, "showed_up": row.showed_up, "cancelled": row.event.cancelled} for row in rows]
    return {
        "season": {"id": season.pk, "name": season.name} if season else None,
        "counts": counts,
        "rate": att._rate(counts["present"], counts["absent"]),
        "sparkline": [{**_event_ref(bar["event"]), "state": bar["state"]} for bar in bars],
        "recent": recent,
    }


def club_report(season: Season | None) -> dict | None:
    """``{"rate", "sessions"}`` for the overview, or ``None`` when the season has no past events yet."""
    count = sessions(season)
    if not count:
        return None
    return {"rate": att.club_attendance_rate(season), "sessions": count}
