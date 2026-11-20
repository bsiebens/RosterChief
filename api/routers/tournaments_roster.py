"""``/tournaments/{id}/teams/{team}/roster``: a team's roster (people with date of birth, licence, dietary needs and lunch days) and the
PDF exports (roster, lunch per team, lunch for the whole tournament). Reading and the PDFs need either tournament permission, editing
a roster needs ``tournaments.operate_tournaments``. Staff can always edit a roster, whatever the tournament's status."""

import datetime
import uuid

from django.http import HttpResponse
from ninja import Router

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from api.schemas_tournaments import LunchDaysIn, RosterEntryIn, RosterEntryPatch
from members.services.errors import Invalid
from tournaments.models import Tournament, TournamentRosterEntry, TournamentRosterRole
from tournaments.services import pdf as pdf_svc
from tournaments.services.lunch import daily_lunch_summary

router = Router(auth=AUTHENTICATORS)


def lunch_days_for(tournament: Tournament, requested) -> list[str]:
    """What ``lunch_days`` an entry gets: none without lunch, every day when it is mandatory, else the requested days inside the tournament."""
    if tournament.lunch_mode == Tournament.LunchMode.NONE:
        return []
    days = tournament.date_range()
    if tournament.lunch_mode == Tournament.LunchMode.MANDATORY:
        return [d.isoformat() for d in days]
    return [d.isoformat() for d in days if d in set(requested or [])]


def check_birth_date(value: datetime.date | None, key="date_of_birth"):
    if value is not None and not (datetime.date(1900, 1, 1) <= value <= datetime.date.today()):
        raise Invalid("Not a valid date of birth.", {key: ["Enter a date of birth in the past."]})


def lunch_out(tournament: Tournament, entries) -> list[dict]:
    if tournament.lunch_mode == Tournament.LunchMode.NONE:
        return []
    return [{"date": d["date"], "player_count": d["player_count"], "staff_count": d["staff_count"], "dietary_notes": d["dietary_notes"]} for d in daily_lunch_summary(tournament, entries)]


def roster_doc(tournament: Tournament, team) -> dict:
    entries = list(team.roster_entries.select_related("member"))
    return {
        "team": c.team_out(team, tournament, entries), "entries": [c.person_out(e, tournament) for e in entries], "lunch_mode": tournament.lunch_mode,
        "lunch_price_per_day": None if tournament.lunch_price_per_day is None else f"{tournament.lunch_price_per_day:.2f}", "dates": tournament.date_range(),
        "min_age": tournament.min_age, "max_age": tournament.max_age, "lunch": lunch_out(tournament, entries),
    }  # fmt: skip


def _entry(team, entry_id) -> TournamentRosterEntry:
    return c.get_or_404(TournamentRosterEntry.objects.select_related("member"), pk=entry_id, tournament_team=team)


def _scope(tournament_id, team_id):
    tournament = c.get_tournament(tournament_id)
    return tournament, c.get_team(tournament, team_id)


@router.get("/{tournament_id}/teams/{team_id}/roster", summary="A team's roster", operation_id="tournaments_roster")
def roster(request, tournament_id: uuid.UUID, team_id: uuid.UUID):
    c.gate_view(request)
    tournament, team = _scope(tournament_id, team_id)
    return roster_doc(tournament, team)


@router.post("/{tournament_id}/teams/{team_id}/roster", summary="Add a person to a roster", operation_id="tournaments_roster_add", response={201: dict})
def add_entry(request, tournament_id: uuid.UUID, team_id: uuid.UUID, payload: RosterEntryIn):
    c.gate_operate(request)
    tournament, team = _scope(tournament_id, team_id)
    errors = {k: ["This field is required."] for k in ("first_name", "last_name") if not getattr(payload, k).strip()}
    if errors:
        raise Invalid("Enter a name.", errors)
    check_birth_date(payload.date_of_birth)
    entry = TournamentRosterEntry(
        tournament_team=team, foreign_first_name=payload.first_name.strip(), foreign_last_name=payload.last_name.strip(), jersey_number=payload.jersey_number, role=payload.role,
        position_label=payload.position_label, date_of_birth=None if payload.role == TournamentRosterRole.STAFF else payload.date_of_birth, license_number=payload.license_number,
        dietary_restrictions=payload.dietary_restrictions, lunch_days=lunch_days_for(tournament, payload.lunch_days),
    )  # fmt: skip
    c.validate(entry)
    entry.save()
    return 201, c.person_out(entry, tournament)


# static paths first: ``roster/pdf`` must be registered before ``roster/{entry_id}``


def _pdf(render, filename):
    try:
        data = render()
    except pdf_svc.PDFUnavailable:
        return HttpResponse('{"detail": "PDF rendering is not available.", "code": "pdf_unavailable"}', status=503, content_type="application/json")
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    return response


@router.get("/{tournament_id}/teams/{team_id}/roster/pdf", summary="The roster as PDF (with date of birth and licence)", operation_id="tournaments_roster_pdf")
def roster_pdf(request, tournament_id: uuid.UUID, team_id: uuid.UUID):
    c.gate_view(request)
    tournament, team = _scope(tournament_id, team_id)
    return _pdf(lambda: pdf_svc.roster_pdf(request.tenant, tournament, team), f"{team.slug}-roster.pdf")


@router.patch("/{tournament_id}/teams/{team_id}/roster/{entry_id}", summary="Change a roster entry", operation_id="tournaments_roster_update")
def update_entry(request, tournament_id: uuid.UUID, team_id: uuid.UUID, entry_id: uuid.UUID, payload: RosterEntryPatch):
    c.gate_operate(request)
    tournament, team = _scope(tournament_id, team_id)
    entry = _entry(team, entry_id)
    data = payload.dict(exclude_unset=True)
    check_birth_date(data.get("date_of_birth"))
    names = {"first_name": "foreign_first_name", "last_name": "foreign_last_name"}
    for key, attr in names.items():
        if key in data and not entry.member_id:
            if not (data[key] or "").strip():
                raise Invalid("Enter a name.", {key: ["This field is required."]})
            setattr(entry, attr, data[key].strip())
    lunch = data.pop("lunch_days", None)
    c.apply(entry, {k: v for k, v in data.items() if k not in names})
    if entry.role == TournamentRosterRole.STAFF:
        entry.date_of_birth = None
    if lunch is not None or tournament.lunch_mode != Tournament.LunchMode.OPTIONAL:
        entry.lunch_days = lunch_days_for(tournament, lunch if lunch is not None else [d for d in tournament.date_range() if d.isoformat() in entry.lunch_days])
    c.validate(entry)
    entry.save()
    return c.person_out(entry, tournament)


@router.put("/{tournament_id}/teams/{team_id}/roster/{entry_id}/lunch", summary="Set the lunch days of one person (optional-lunch tournaments)", operation_id="tournaments_roster_lunch")
def set_lunch(request, tournament_id: uuid.UUID, team_id: uuid.UUID, entry_id: uuid.UUID, payload: LunchDaysIn):
    c.gate_operate(request)
    tournament, team = _scope(tournament_id, team_id)
    entry = _entry(team, entry_id)
    if tournament.lunch_mode != Tournament.LunchMode.OPTIONAL:
        raise Invalid("Lunch days are only chosen when lunch is optional.", {"lunch_days": ["Lunch days are only chosen when lunch is optional."]}, code="lunch_not_optional")
    entry.lunch_days = lunch_days_for(tournament, payload.lunch_days)
    entry.save(update_fields=["lunch_days"])
    return c.person_out(entry, tournament)


@router.delete("/{tournament_id}/teams/{team_id}/roster/{entry_id}", summary="Remove a person from a roster", operation_id="tournaments_roster_delete", response={204: None})
def delete_entry(request, tournament_id: uuid.UUID, team_id: uuid.UUID, entry_id: uuid.UUID):
    c.gate_operate(request)
    _tournament, team = _scope(tournament_id, team_id)
    _entry(team, entry_id).delete()
    return 204, None


@router.get("/{tournament_id}/teams/{team_id}/lunch/pdf", summary="A team's lunch overview as PDF", operation_id="tournaments_team_lunch_pdf")
def team_lunch_pdf(request, tournament_id: uuid.UUID, team_id: uuid.UUID):
    c.gate_view(request)
    tournament, team = _scope(tournament_id, team_id)
    return _pdf(lambda: pdf_svc.team_lunch_pdf(request.tenant, tournament, team), f"{team.slug}-lunch.pdf")


@router.get("/{tournament_id}/lunch-summary/pdf", summary="The lunch summary of every team as PDF, for the caterer", operation_id="tournaments_lunch_summary_pdf")
def lunch_summary_pdf(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    tournament = c.get_tournament(tournament_id)
    return _pdf(lambda: pdf_svc.lunch_summary_pdf(request.tenant, tournament), f"{tournament.slug}-lunch-summary.pdf")


@router.get("/{tournament_id}/lunch-summary", summary="The lunch summary of every team as data", operation_id="tournaments_lunch_summary")
def lunch_summary(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    tournament = c.get_tournament(tournament_id)
    days = pdf_svc.lunch_summary_days(tournament) if tournament.lunch_mode != Tournament.LunchMode.NONE else []
    return {
        "lunch_mode": tournament.lunch_mode,
        "days": [
            {
                "date": d["date"], "player_count": d["player_count"], "staff_count": d["staff_count"], "dietary_notes": d["dietary_notes"],
                "teams": [{"team": c.team_ref(t["team"]), "count": t["count"], "dietary_people": t["dietary_people"]} for t in d["team_counts"]],
            }
            for d in days
        ],
    }  # fmt: skip
