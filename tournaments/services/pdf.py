"""PDF exports for staff: a team's roster (staff-only fields included), a team's lunch overview and the whole tournament's lunch summary
for the caterer. Rendered with WeasyPrint like the shop invoices; ``PDFUnavailable`` when it cannot be loaded on this server."""

from django.template.loader import render_to_string
from django.utils import timezone

from shop.services.invoices import PDFUnavailable

from ..models import TournamentRosterEntry, TournamentRosterRole
from .lunch import daily_lunch_summary

__all__ = ["PDFUnavailable", "lunch_summary_days", "lunch_summary_pdf", "roster_pdf", "team_lunch_pdf"]


def _write_pdf(template: str, context: dict) -> bytes:
    html = render_to_string(template, {**context, "generated_at": timezone.now()})
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise PDFUnavailable("PDF rendering is not available on this server.") from error
    return HTML(string=html).write_pdf()


def roster_pdf(club, tournament, team) -> bytes:
    entries = list(team.roster_entries.select_related("member"))
    context = {
        "club": club, "tournament": tournament, "team": team, "entries": entries,
        "player_count": sum(1 for e in entries if e.role == TournamentRosterRole.PLAYER), "staff_count": sum(1 for e in entries if e.role == TournamentRosterRole.STAFF),
    }  # fmt: skip
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
