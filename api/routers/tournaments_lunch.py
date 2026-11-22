"""``/tournaments/{id}/lunch``: lunch settings and per-day windows, generating the sittings and clearing them. Reading needs either tournament
permission, changing anything ``tournaments.operate_tournaments``. The planning lives in ``tournaments.services.lunch_schedule``."""

import datetime
import uuid

from django.db import transaction
from django.utils import timezone
from ninja import Router

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from api.schemas_tournaments import LunchGenerateIn, LunchIn
from members.services.errors import Invalid
from tournaments.models import Tournament
from tournaments.services import lunch_schedule as svc

router = Router(auth=AUTHENTICATORS)

SETTINGS = {
    "duration_minutes": "lunch_duration_minutes", "gap_before_minutes": "lunch_gap_before_minutes", "gap_after_minutes": "lunch_gap_after_minutes",
    "capacity_teams": "lunch_capacity_teams", "interval_minutes": "lunch_interval_minutes",
}  # fmt: skip


def _hhmm(value: datetime.time | None) -> str | None:
    return None if value is None else f"{value:%H:%M}"


def lunch_doc(t: Tournament) -> dict:
    sittings: dict[datetime.date, list[dict]] = {}
    blocks = svc.lunch_blocks(t)
    for block in blocks:
        start, end = timezone.localtime(block.lunch_start), timezone.localtime(block.lunch_end)
        sittings.setdefault(start.date(), []).append({"start": f"{start:%H:%M}", "end": f"{end:%H:%M}", "teams": [{"id": team.pk, "name": team.display_name} for team in block.teams.all()]})
    days = [
        {
            "date": day.date, "lunch_start": _hhmm(day.lunch_start), "lunch_end": _hhmm(day.lunch_end), "day_start": _hhmm(day.starts_at), "day_end": _hhmm(day.ends_at),
            "teams_eating": len(svc.eating_teams(t, day.date)), "sittings": sittings.get(day.date, []),
        }
        for day in t.days.order_by("date")
    ]  # fmt: skip
    return {
        "enabled": t.lunch_mode != Tournament.LunchMode.NONE,
        "settings": {**{key: getattr(t, attr) for key, attr in SETTINGS.items()}, "location": t.lunch_location},
        "days": days,
        "generated": bool(blocks),
    }


@router.get("/{tournament_id}/lunch", summary="Lunch settings, per-day windows and the generated sittings", operation_id="tournaments_lunch")
def get_lunch(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    return lunch_doc(c.get_tournament(tournament_id))


@router.put("/{tournament_id}/lunch", summary="Set the lunch settings and the lunch window of days (regenerates nothing)", operation_id="tournaments_lunch_update")
def update_lunch(request, tournament_id: uuid.UUID, payload: LunchIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    days = {d.date: d for d in t.days.all()}
    seen = set()
    for row in payload.days:
        if row.date not in days:
            raise Invalid("Unknown date.", {"days": [f"{row.date} is not a day of this tournament."]})
        if row.date in seen:
            raise Invalid("Each date can only be given once.", {"days": ["Each date can only be given once."]})
        seen.add(row.date)
        if (row.lunch_start is None) != (row.lunch_end is None):
            raise Invalid("Give both a lunch start and end, or neither.", {"days": ["Give both a lunch start and end, or neither."]})
        if row.lunch_start is not None and row.lunch_end <= row.lunch_start:
            raise Invalid("Lunch must end after it starts.", {"days": ["Lunch must end after it starts."]})
    with transaction.atomic():
        for key, attr in SETTINGS.items():
            setattr(t, attr, getattr(payload.settings, key))
        t.lunch_location = payload.settings.location.strip()
        t.save(update_fields=[*SETTINGS.values(), "lunch_location"])
        for row in payload.days:
            day = days[row.date]
            day.lunch_start, day.lunch_end = row.lunch_start, row.lunch_end
            day.save(update_fields=["lunch_start", "lunch_end"])
    return lunch_doc(t)


@router.post("/{tournament_id}/lunch/generate", summary="Create the lunch sittings and re-plan the games around them", operation_id="tournaments_lunch_generate")
def generate_lunch(request, tournament_id: uuid.UUID, payload: LunchGenerateIn | None = None):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    minutes = payload.match_duration_minutes if payload else None
    try:
        return svc.generate_lunch_schedule(t, datetime.timedelta(minutes=minutes) if minutes else None)
    except svc.scheduling.SchedulingError as error:
        raise Invalid(str(error), {"detail": [str(error)]}, code="scheduling") from error


@router.post("/{tournament_id}/lunch/clear", summary="Delete the generated lunch sittings (games are left as they are)", operation_id="tournaments_lunch_clear", response={204: None})
def clear_lunch(request, tournament_id: uuid.UUID):
    c.gate_operate(request)
    svc.clear_lunch_schedule(c.get_tournament(tournament_id))
    return 204, None
