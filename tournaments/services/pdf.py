"""PDF exports for staff: a team's roster (staff-only fields included), a team's lunch overview and the whole tournament's lunch summary
for the caterer. Rendered with WeasyPrint like the shop invoices; ``PDFUnavailable`` when it cannot be loaded on this server."""

from django.template.loader import render_to_string
from django.utils import timezone

from shop.services.invoices import PDFUnavailable

from ..models import TournamentRosterEntry, TournamentRosterRole
from .lunch import daily_lunch_summary

__all__ = ["PDFUnavailable", "lunch_summary_days", "lunch_summary_pdf", "roster_pdf", "tasks_overview_context", "tasks_pdf", "team_lunch_pdf"]


def _write_pdf(template: str, context: dict) -> bytes:
    html = render_to_string(template, {**context, "generated_at": timezone.now()})
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise PDFUnavailable("PDF rendering is not available on this server.") from error
    return HTML(string=html).write_pdf()


def _roster_row(entry: TournamentRosterEntry) -> dict:
    """One line of the roster sheet. A club member's date of birth (players only) and licence number fall back to their member record."""
    member = entry.member if entry.member_id else None
    dob = entry.date_of_birth or (member.date_of_birth if member and entry.role == TournamentRosterRole.PLAYER else None)
    return {
        "name": entry.display_name, "jersey_number": entry.jersey_number, "position": entry.position_label,
        "date_of_birth": dob, "license_number": entry.license_number or ((member.license_number or "") if member else ""),
    }  # fmt: skip


def roster_pdf(club, tournament, team) -> bytes:
    entries = list(team.roster_entries.select_related("member"))
    players = sorted((e for e in entries if e.role == TournamentRosterRole.PLAYER), key=lambda e: (e.jersey_number is None, e.jersey_number or 0, e.display_name.lower()))
    staff = sorted((e for e in entries if e.role == TournamentRosterRole.STAFF), key=lambda e: e.display_name.lower())
    context = {"club": club, "tournament": tournament, "team": team, "players": [_roster_row(e) for e in players], "staff": [_roster_row(e) for e in staff]}
    return _write_pdf("tournaments/pdf/roster.html", context)


def team_lunch_pdf(club, tournament, team) -> bytes:
    days = daily_lunch_summary(tournament, list(team.roster_entries.all()))
    return _write_pdf("tournaments/pdf/team_lunch.html", {"club": club, "tournament": tournament, "team": team, "days": days})


def lunch_summary_days(tournament) -> list[dict]:
    """The whole-tournament lunch summary: per day the totals plus, per team still in, the headcount and who has which dietary restriction."""
    teams = list(tournament.teams.filter(withdrawn_at__isnull=True))
    entries = list(TournamentRosterEntry.objects.filter(tournament_team__in=teams).select_related("tournament_team"))
    days = daily_lunch_summary(tournament, entries)
    for day in days:
        iso = day["date"].isoformat()
        day["team_counts"] = []
        for team in teams:
            eating = [e for e in entries if e.tournament_team_id == team.id and iso in e.lunch_days]
            people = [{"name": e.display_name, "restriction": e.dietary_restrictions.strip()} for e in eating if e.dietary_restrictions.strip()]
            day["team_counts"].append({"team": team, "count": len(eating), "dietary_people": people})
    return days


def lunch_summary_pdf(club, tournament) -> bytes:
    return _write_pdf("tournaments/pdf/lunch_summary.html", {"club": club, "tournament": tournament, "days": lunch_summary_days(tournament)})


#: Block colours of the task overview, by task title: (fill, border, text).
_TASK_COLOURS = [
    ("#dbeafe", "#3b82f6", "#1e3a8a"), ("#dcfce7", "#22c55e", "#14532d"), ("#fef3c7", "#f59e0b", "#78350f"), ("#fce7f3", "#ec4899", "#831843"),
    ("#ede9fe", "#8b5cf6", "#4c1d95"), ("#ccfbf1", "#14b8a6", "#134e4a"), ("#ffedd5", "#f97316", "#7c2d12"), ("#e0f2fe", "#0ea5e9", "#0c4a6e"),
]


def tasks_overview_context(tournament) -> dict:
    """The volunteer overview as drawn blocks: per tournament day a time axis and one row per task title and location, each shift a block
    positioned on the axis (left/width in percent) with its volunteers inside, and below it the same shifts as a list."""
    import itertools

    tasks = list(tournament.tasks.prefetch_related("claims__member").order_by("starts_at", "location_label", "title"))
    titles = list(dict.fromkeys(t.title for t in tasks))
    colour = {title: _TASK_COLOURS[i % len(_TASK_COLOURS)] for i, title in enumerate(titles)}
    days = []
    for date, day_tasks in itertools.groupby(tasks, key=lambda t: timezone.localtime(t.starts_at).date()):
        day_tasks = list(day_tasks)
        first = min(timezone.localtime(t.starts_at) for t in day_tasks)
        last = max(timezone.localtime(t.ends_at) for t in day_tasks)
        start_min = (first.hour * 60 + first.minute) // 60 * 60
        end_min = -(-(last.hour * 60 + last.minute) // 60) * 60 if last.date() == date else 24 * 60
        end_min = max(end_min, start_min + 60)
        span = end_min - start_min

        def minute(dt, date=date, start_min=start_min):
            local = timezone.localtime(dt)
            return (local.date() - date).days * 24 * 60 + local.hour * 60 + local.minute - start_min

        def block(task, span=span, minute=minute):
            names = [cl.member.get_full_name() for cl in task.claims.all()]
            left = max(0, minute(task.starts_at)) / span * 100
            width = min(span - max(0, minute(task.starts_at)), minute(task.ends_at) - max(0, minute(task.starts_at))) / span * 100
            return {
                "title": task.title, "location": task.location_label, "description": task.description, "start": timezone.localtime(task.starts_at), "end": timezone.localtime(task.ends_at),
                "left": round(left, 3), "width": round(max(width, 1.5), 3), "needed": task.needed_quantity, "claimed": len(names), "open": max(task.needed_quantity - len(names), 0),
                "names": names, "full": len(names) >= task.needed_quantity, "colour": colour[task.title],
            }  # fmt: skip

        rows = []
        for (location, title), group in itertools.groupby(sorted(day_tasks, key=lambda t: (t.location_label, t.title, t.starts_at)), key=lambda t: (t.location_label, t.title)):
            blocks = [block(t) for t in group]
            lanes: list = []  # blocks of one row that overlap are stacked
            for b in blocks:
                lane = next((i for i, end in enumerate(lanes) if end <= b["left"] + 0.001), None)
                if lane is None:
                    lane = len(lanes)
                    lanes.append(0)
                lanes[lane] = b["left"] + b["width"]
                b["lane"] = lane
            rows.append({"title": title, "location": location, "blocks": blocks, "lanes": max(1, len(lanes)), "height_mm": max(1, len(lanes)) * 15 + 2, "colour": colour[title]})
        ticks = [{"label": f"{m // 60 % 24:02d}:00", "left": round((m - start_min) / span * 100, 3)} for m in range(start_min, end_min + 1, 60)]
        flat = sorted((b for r in rows for b in r["blocks"]), key=lambda b: (b["location"], b["start"], b["title"]))
        days.append({"date": date, "rows": rows, "ticks": ticks, "list": flat, "open_slots": sum(b["open"] for b in flat), "total_slots": sum(b["needed"] for b in flat)})
    return {"days": days, "titles": [{"title": t, "colour": colour[t]} for t in titles], "task_count": len(tasks), "open_slots": sum(d["open_slots"] for d in days), "total_slots": sum(d["total_slots"] for d in days)}


def tasks_pdf(club, tournament) -> bytes:
    return _write_pdf("tournaments/pdf/tasks.html", {"club": club, "tournament": tournament, **tasks_overview_context(tournament)})
