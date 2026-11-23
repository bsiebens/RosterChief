"""Turning a flat roster (each entry with its own ``lunch_days``) into a
per-tournament-day summary -- shared by the management roster screen's own
Lunch section and both the roster/lunch PDF exports, so the "who's having
lunch, and what do they need catering to know" math lives in exactly one
place rather than three.
"""

from ..models import TournamentRosterRole


def daily_lunch_summary(tournament, entries) -> list[dict]:
    """One dict per ``tournament.date_range()`` day: ``date``, the
    ``player``/``staff`` entries having lunch that day, their counts, and
    the distinct, non-blank ``dietary_restrictions`` among just those
    people (not the whole roster -- someone not eating that day doesn't
    need catering to know their restriction)."""
    days = []
    for day in tournament.date_range():
        iso = day.isoformat()
        eating = [entry for entry in entries if iso in entry.lunch_days]
        players = [entry for entry in eating if entry.role == TournamentRosterRole.PLAYER]
        staff = [entry for entry in eating if entry.role == TournamentRosterRole.STAFF]
        dietary_notes = sorted({entry.dietary_restrictions.strip() for entry in eating if entry.dietary_restrictions.strip()})
        days.append({"date": day, "players": players, "staff": staff, "player_count": len(players), "staff_count": len(staff), "dietary_notes": dietary_notes})
    return days
