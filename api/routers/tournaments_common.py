"""Shared by the /tournaments routers: the gates (permission 403 first, then the ``tournaments`` feature / platform host 404), lookups,
model validation and the JSON documents the management and public endpoints share."""

import datetime
import itertools

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django_tenants.utils import get_public_schema_name
from ninja.errors import HttpError

from members.services import access
from members.services.errors import Invalid
from tournaments.models import Match, Tournament, TournamentField, TournamentRosterRole, TournamentStage, TournamentTeam


def _feature(request):
    if request.tenant.schema_name == get_public_schema_name() or not request.features.has("tournaments"):
        raise HttpError(404, "Not found.")


def gate_view(request):
    """Read access: the manage tier or the operate tier."""
    if not access.can_operate_tournaments(request.user):  # the operate check already includes the manage tier
        access.require_operate_tournaments(request.user)
    _feature(request)


def gate_operate(request):
    access.require_operate_tournaments(request.user)
    _feature(request)


def gate_manage(request):
    access.require_manage_tournaments(request.user)
    _feature(request)


def public_gate(request):
    """Anonymous endpoints: club hosts of a club with the feature only."""
    _feature(request)


def get_tournament(tournament_id) -> Tournament:
    tournament = Tournament.objects.filter(pk=tournament_id).first()
    if tournament is None:
        raise HttpError(404, "Not found.")
    return tournament


def get_or_404(model, **filters):
    qs = model if hasattr(model, 'filter') else model.objects
    obj = qs.filter(**filters).first()
    if obj is None:
        raise HttpError(404, "Not found.")
    return obj


def get_team(tournament, team_id) -> TournamentTeam:
    return get_or_404(TournamentTeam.objects.select_related("team", "tournament"), pk=team_id, tournament=tournament)


def get_stage(tournament, stage_id) -> TournamentStage:
    return get_or_404(TournamentStage, pk=stage_id, tournament=tournament)


def get_field(tournament, field_id) -> TournamentField:
    return get_or_404(TournamentField, pk=field_id, tournament=tournament)


def field_or_invalid(tournament, field_id, key="field_id"):
    field = TournamentField.objects.filter(pk=field_id, tournament=tournament).first()
    if field is None:
        raise Invalid("Unknown field.", {key: ["Unknown field."]})
    return field


def validate(instance, exclude=None):
    """Run the model's own validation (``clean`` and field rules) and raise ``Invalid`` (422) with per-field messages."""
    try:
        instance.full_clean(exclude=["slug", *(exclude or [])])
    except ValidationError as error:
        errors = {("detail" if key == "__all__" else key): [str(m) for m in messages] for key, messages in error.message_dict.items()}
        raise Invalid(next(iter(errors.values()))[0], errors) from error


def apply(instance, data: dict, mapping: dict | None = None):
    """Set ``data`` (a payload dump) on ``instance``; ``mapping`` renames payload keys to model attributes (``home_team_id`` stays as is)."""
    for key, value in data.items():
        setattr(instance, (mapping or {}).get(key, key), value)


def url(file) -> str | None:
    try:
        return file.url if file else None
    except Exception:
        return None


def ref(obj):
    return {"id": obj.pk, "name": str(obj)} if obj is not None else None


def count_label(items, key):
    return [{"key": k, "items": list(g)} for k, g in itertools.groupby(items, key=key)]


# --- documents -----------------------------------------------------------------------------------------------------------


def team_ref(team) -> dict | None:
    if team is None:
        return None
    logo = team.display_logo
    return {"id": team.pk, "name": team.display_name, "slug": team.slug, "logo_url": url(logo), "primary_color": team.foreign_primary_color, "secondary_color": team.foreign_secondary_color}


def team_out(team: TournamentTeam, tournament: Tournament, entries=None) -> dict:
    entries = list(team.roster_entries.select_related("member")) if entries is None else entries
    return {
        **team_ref(team),
        "club_team": ref(team.team),
        "is_foreign": team.team_id is None,
        "is_home_team": team.team_id is not None and team.team_id == tournament.home_team_id,
        "contact_name": team.foreign_contact_name, "contact_email": team.foreign_contact_email, "contact_phone": team.foreign_contact_phone,
        "seed": team.seed, "payment_status": team.payment_status, "withdrawn_at": team.withdrawn_at, "withdrawal_reason": team.withdrawal_reason,
        "registration_id": team.registration_id, "event_id": team.event_id,
        "player_count": sum(1 for e in entries if e.role == TournamentRosterRole.PLAYER),
        "staff_count": sum(1 for e in entries if e.role == TournamentRosterRole.STAFF),
        "has_age_flag": any(tournament.birth_year_out_of_range(effective_dob(e)) for e in entries),
    }


def effective_dob(row):
    """A roster entry's date of birth, else (for a club member playing) the one on their member record. Staff has none."""
    if row.date_of_birth is not None or not getattr(row, "member_id", None) or row.role != TournamentRosterRole.PLAYER:
        return row.date_of_birth
    return row.member.date_of_birth


def person_out(row, tournament: Tournament) -> dict:
    """A roster entry or a registration player row (same shape; ``member_id`` is only set on roster entries of club members)."""
    is_entry = hasattr(row, "member_id")
    member = row.member if is_entry and row.member_id else None
    if is_entry:  # a club member's name lives on their member record, a guest's on the entry
        first, last = (member.first_name, member.last_name) if member else (row.foreign_first_name, row.foreign_last_name)
    else:
        first, last = row.first_name, row.last_name
    dob = effective_dob(row) if is_entry else row.date_of_birth
    license_number = row.license_number or ((member.license_number or "") if member else "")
    return {
        "id": row.pk, "member_id": row.member_id if is_entry else None, "name": row.display_name if is_entry else f"{first} {last}".strip(),
        "first_name": first, "last_name": last, "jersey_number": row.jersey_number, "role": row.role, "position_label": row.position_label,
        "date_of_birth": dob, "license_number": license_number, "dietary_restrictions": row.dietary_restrictions,
        "lunch_days": row.lunch_days, "age_flagged": tournament.birth_year_out_of_range(dob),
    }


def match_out(match: Match, *, with_field=True) -> dict:
    return {
        "id": match.pk, "stage_id": match.stage_id, "group": ref(match.group), "round_number": match.round_number, "round_label": match.round_label, "sequence": match.sequence,
        "field": ref(match.field) if with_field else None, "scheduled_start": match.scheduled_start, "auto_scheduled": match.auto_scheduled, "status": match.status,
        "score_home": match.score_home, "score_away": match.score_away, "went_to_overtime": match.went_to_overtime, "penalty_home": match.penalty_home, "penalty_away": match.penalty_away,
        "home_team": team_ref(match.home_team), "away_team": team_ref(match.away_team), "home_display": match.home_display, "away_display": match.away_display,
    }


def round_label(round_number, matches) -> str:
    return next((m.round_label for m in matches if m.round_label), "") or _("Round %(number)d") % {"number": round_number}


def round_groups(matches) -> list[dict]:
    rounds = []
    for number, group in itertools.groupby(matches, key=lambda m: m.round_number):
        group = list(group)
        rounds.append({"round_number": number, "label": round_label(number, group), "all_complete": all(m.status == Match.Status.COMPLETE for m in group), "matches": [match_out(m) for m in group]})
    return rounds


def stage_matches(tournament) -> list:
    return list(
        Match.objects.filter(stage__tournament=tournament)
        .select_related("stage", "group", "field", "home_team__team", "away_team__team", "home_source_match", "away_source_match", "home_source_group", "away_source_group")
        .order_by("stage__sequence", "round_number", "sequence")
    )


def stage_summary(stage: TournamentStage) -> dict:
    config, standings = stage.config or {}, stage.standings_config or {}
    points, tiebreakers = standings.get("points", {}), standings.get("tiebreakers", [])
    return {
        "id": stage.pk, "name": stage.name, "sequence": stage.sequence, "format": stage.format, "status": stage.status,
        "rounds": config.get("rounds"), "num_groups": config.get("num_groups"),
        "points_win": points.get("win", 3), "points_draw": points.get("draw", 1), "points_loss": points.get("loss", 0),
        "tiebreaker_1": tiebreakers[0] if tiebreakers else "goal_diff", "tiebreaker_2": tiebreakers[1] if len(tiebreakers) > 1 else None,
    }


def date_window(value: datetime.time) -> str:
    return value.strftime("%H:%M")


def days_out(t: Tournament) -> list[dict]:
    return [{"date": d.date, "starts_at": f"{d.starts_at:%H:%M}", "ends_at": f"{d.ends_at:%H:%M}"} for d in t.days.order_by("date")]
