"""``/tournaments/{id}/setup|fields|stages|break-rules``: what a tournament needs before a schedule exists: playing fields with their
availability, stages (format, points, tiebreakers), generating or seeding a stage's matches, "start next phase" for a finished groups
stage, and break rules. Reading needs either tournament permission, changing needs ``tournaments.operate_tournaments``."""

import uuid

from django.db import transaction
from django.db.models import Max, Q
from ninja import Router

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from api.routers.tournaments_competition import _scheduling
from api.schemas_tournaments import BreakRuleIn, BreakRulePatch, FieldIn, FieldPatch, GenerateMatchesIn, NextPhaseIn, SeedIn, StageIn, StagePatch
from formbuilder.services.management import Conflict
from members.services.errors import Invalid
from tournaments.models import BreakRule, Match, TournamentField, TournamentGroup, TournamentStage, TournamentTeam
from tournaments.services import scheduling as svc

router = Router(auth=AUTHENTICATORS)

SEEDABLE = (TournamentStage.Format.SINGLE_ELIM, TournamentStage.Format.DOUBLE_ELIM)


def _hm(value) -> str:
    return value.strftime("%H:%M")


def field_out(field: TournamentField) -> dict:
    windows = [{"date": w.get("date"), "start": w.get("start"), "end": w.get("end")} for w in (field.availability or [])]
    return {"id": field.pk, "name": field.name, "availability": windows}


def _windows(windows) -> list[dict]:
    """Availability windows as stored (ISO date, ``HH:MM``), sorted; end after start and no overlap on the same date."""
    rows = sorted(windows, key=lambda w: (w.date, w.start))
    for index, w in enumerate(rows):
        if w.end <= w.start:
            raise Invalid("Must be after the start time.", {f"availability.{index}.end": ["Must be after the start time."]})
    for index, w in enumerate(rows):
        if index and rows[index - 1].date == w.date and w.start < rows[index - 1].end:
            raise Invalid("Overlaps another window on the same date.", {f"availability.{index}.start": ["Overlaps another window on the same date."]})
    return [{"date": w.date.isoformat(), "start": _hm(w.start), "end": _hm(w.end)} for w in rows]


def rule_out(rule: BreakRule) -> dict:
    return {
        "id": rule.pk, "field": c.ref(rule.field), "trigger_type": rule.trigger_type, "trigger_every_n_games": rule.trigger_every_n_games,
        "trigger_every_n_minutes": rule.trigger_every_n_minutes, "trigger_after_round_number": rule.trigger_after_round_number,
        "trigger_window_start": rule.trigger_window_start and _hm(rule.trigger_window_start), "trigger_window_end": rule.trigger_window_end and _hm(rule.trigger_window_end),
        "gap_minutes": rule.gap_minutes, "replaces_default_gap": rule.replaces_default_gap, "priority": rule.priority, "summary": rule.display_summary,
    }  # fmt: skip


def _stage_doc(stage: TournamentStage, stage_count: int) -> dict:
    seedable = stage.format in SEEDABLE
    return {
        **c.stage_summary(stage), "match_count": stage.matches.count(), "groups": [c.ref(g) for g in stage.groups.order_by("sequence")],
        # single/double elimination can be seeded by hand ("Nth place of a group"); only worth asking when there is another stage to seed from
        "needs_seed_choice": seedable and stage_count > 1, "supports_placement": stage.format == TournamentStage.Format.SINGLE_ELIM,
    }  # fmt: skip


def _stage_values(stage: TournamentStage, payload: dict):
    """Apply the stage form fields: name/sequence/format, ``config`` (rounds, num_groups), ``standings_config`` (points, tiebreakers)."""
    current = c.stage_summary(stage) if stage.pk else {}
    merged = {**current, **payload}
    if merged.get("format") == TournamentStage.Format.SWISS and not merged.get("rounds"):
        raise Invalid("Required for a Swiss stage.", {"rounds": ["Required for a Swiss stage."]})
    if merged.get("format") == TournamentStage.Format.GROUPS and not merged.get("num_groups"):
        raise Invalid("Required for a groups stage.", {"num_groups": ["Required for a groups stage."]})
    stage.name, stage.format = merged["name"], merged["format"]
    stage.sequence = merged["sequence"] if merged.get("sequence") is not None else stage.sequence
    stage.config = {k: merged[k] for k in ("rounds", "num_groups") if merged.get(k)}
    stage.standings_config = {
        "points": {"win": merged.get("points_win", 3), "draw": merged.get("points_draw", 1), "loss": merged.get("points_loss", 0)},
        "tiebreakers": [v for v in (merged.get("tiebreaker_1"), merged.get("tiebreaker_2")) if v] or ["goal_diff", "goals_for"],
    }  # fmt: skip


@router.get("/{tournament_id}/setup", summary="Fields, stages and break rules of a tournament", operation_id="tournaments_setup")
def setup(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    stages = list(t.stages.order_by("sequence", "pk"))  # in the order they are played
    return {
        "fields": [field_out(f) for f in t.fields.all()], "stages": [_stage_doc(s, len(stages)) for s in stages],
        "break_rules": [rule_out(r) for r in t.break_rules.select_related("field").order_by("priority", "pk")]  # the order the scheduler tries them in, "dates": t.date_range(),
    }  # fmt: skip


# --- fields ----------------------------------------------------------------------------------------------------------------


@router.post("/{tournament_id}/fields", summary="Add a playing field", operation_id="tournaments_field_create", response={201: dict})
def create_field(request, tournament_id: uuid.UUID, payload: FieldIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    field = TournamentField(tournament=t, name=payload.name.strip(), availability=_windows(payload.availability))
    c.validate(field)
    field.save()
    return 201, field_out(field)


@router.patch("/{tournament_id}/fields/{field_id}", summary="Rename a field or change when it is available", operation_id="tournaments_field_update")
def update_field(request, tournament_id: uuid.UUID, field_id: uuid.UUID, payload: FieldPatch):
    c.gate_operate(request)
    field = c.get_field(c.get_tournament(tournament_id), field_id)
    data = payload.dict(exclude_unset=True)
    if data.get("name") is not None:
        field.name = data["name"].strip()
    if data.get("availability") is not None:
        field.availability = _windows(payload.availability)
    c.validate(field)
    field.save()
    return field_out(field)


@router.delete("/{tournament_id}/fields/{field_id}", summary="Delete a field (its matches become unscheduled)", operation_id="tournaments_field_delete", response={204: None})
def delete_field(request, tournament_id: uuid.UUID, field_id: uuid.UUID):
    c.gate_operate(request)
    c.get_field(c.get_tournament(tournament_id), field_id).delete()
    return 204, None


# --- stages ----------------------------------------------------------------------------------------------------------------


@router.post("/{tournament_id}/stages", summary="Add a stage", operation_id="tournaments_stage_create", response={201: dict})
def create_stage(request, tournament_id: uuid.UUID, payload: StageIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    stage = TournamentStage(tournament=t)
    data = payload.dict()
    if data.get("sequence") is None:
        last = t.stages.aggregate(Max("sequence"))["sequence__max"]
        data["sequence"] = 0 if last is None else last + 1
    _stage_values(stage, data)
    c.validate(stage)
    stage.save()
    return 201, _stage_doc(stage, t.stages.count())


@router.patch("/{tournament_id}/stages/{stage_id}", summary="Change a stage", operation_id="tournaments_stage_update")
def update_stage(request, tournament_id: uuid.UUID, stage_id: uuid.UUID, payload: StagePatch):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    stage = c.get_stage(t, stage_id)
    _stage_values(stage, payload.dict(exclude_unset=True))
    c.validate(stage)
    stage.save()
    return _stage_doc(stage, t.stages.count())


@router.delete("/{tournament_id}/stages/{stage_id}", summary="Delete a stage with its groups and matches", operation_id="tournaments_stage_delete", response={204: None})
def delete_stage(request, tournament_id: uuid.UUID, stage_id: uuid.UUID):
    c.gate_operate(request)
    c.get_stage(c.get_tournament(tournament_id), stage_id).delete()
    return 204, None


def _activate(stage):
    stage.status = TournamentStage.Status.ACTIVE
    stage.save(update_fields=["status"])


def _clear_stage(stage):
    """Removes a stage's generated matches and groups so it can be generated again. Refused once a match has been played, or when a later
    stage takes its teams from this one's matches or groups (seeded or advanced), because those would silently lose their source."""
    if stage.matches.filter(status__in=[Match.Status.COMPLETE, Match.Status.IN_PROGRESS, Match.Status.WALKOVER]).exists():
        raise Conflict("This stage has played matches, so it can't be regenerated.", "has_results")
    depends = Match.objects.exclude(stage=stage).filter(
        Q(home_source_match__stage=stage) | Q(away_source_match__stage=stage) | Q(home_source_group__stage=stage) | Q(away_source_group__stage=stage)
    )
    if depends.exists():
        raise Conflict("A later stage takes its teams from this one: clear that stage first.", "has_dependents")
    stage.matches.all().delete()
    stage.groups.all().delete()


@router.post("/{tournament_id}/stages/{stage_id}/generate-matches", summary="Generate a stage's matches from the teams in seed order", operation_id="tournaments_stage_generate")
def generate_matches(request, tournament_id: uuid.UUID, stage_id: uuid.UUID, payload: GenerateMatchesIn | None = None):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    stage = c.get_stage(t, stage_id)
    with transaction.atomic():
        if payload and payload.regenerate:
            _clear_stage(stage)
        matches = _scheduling(svc.generate_stage_matches, stage, with_placement=bool(payload and payload.with_placement))
    _activate(stage)
    return {"generated_count": len(matches)}


def _seedable_stage(t, stage_id):
    stage = c.get_stage(t, stage_id)
    if stage.format not in SEEDABLE:
        raise Invalid("Only knockout stages can be seeded by hand.", {"detail": ["Only knockout stages can be seeded by hand."]}, code="not_seedable")
    return stage


def _live_teams(t):
    return TournamentTeam.objects.filter(tournament=t, withdrawn_at__isnull=True).select_related("team")


@router.get("/{tournament_id}/stages/{stage_id}/seed", summary="Starting point for seeding a knockout stage by hand", operation_id="tournaments_stage_seed_get")
def seed_form(request, tournament_id: uuid.UUID, stage_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    stage = _seedable_stage(t, stage_id)
    teams = list(_live_teams(t).order_by("seed"))
    slots = 2
    while slots < len(teams):
        slots *= 2
    groups = TournamentGroup.objects.filter(stage__tournament=t).select_related("stage").order_by("stage__sequence", "sequence")
    return {
        "already_generated": stage.matches.exists(), "slot_count": slots, "slots": [{"type": "team", "team_id": team.pk} for team in teams],
        "teams": [c.team_ref(team) for team in teams], "groups": [{"id": g.pk, "name": f"{g.stage.name} - {g.name}", "stage_id": g.stage_id} for g in groups],
    }  # fmt: skip


@router.post("/{tournament_id}/stages/{stage_id}/seed", summary="Generate a knockout stage from hand-picked slots (teams or 'Nth of a group')", operation_id="tournaments_stage_seed")
def seed(request, tournament_id: uuid.UUID, stage_id: uuid.UUID, payload: SeedIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    stage = _seedable_stage(t, stage_id)
    teams = {team.pk: team for team in _live_teams(t)}
    groups = {g.pk: g for g in TournamentGroup.objects.filter(stage__tournament=t)}
    entries, seen_teams, seen_ranks = [], set(), set()
    for index, slot in enumerate(payload.slots):
        key = f"slots.{index}"
        if slot.type == "team":
            team = teams.get(slot.team_id)
            if team is None:
                raise Invalid("Pick a team for this slot.", {f"{key}.team_id": ["Pick a team for this slot, or switch it to a group standing or skip."]})
            if team.pk in seen_teams:
                raise Invalid("Team used twice.", {f"{key}.team_id": [f"{team.display_name} is already used in another slot."]})
            seen_teams.add(team.pk)
            entries.append(team)
        elif slot.type == "group":
            group = groups.get(slot.group_id)
            if group is None:
                raise Invalid("Pick a group.", {f"{key}.group_id": ["Pick a group for this slot."]})
            if not slot.rank:
                raise Invalid("Enter a rank.", {f"{key}.rank": ["Enter which place in the group this slot is (1 for first, 2 for second, ...)."]})
            if (group.pk, slot.rank) in seen_ranks:
                raise Invalid("Rank used twice.", {f"{key}.rank": [f"Rank {slot.rank} of {group.name} is already used in another slot."]})
            seen_ranks.add((group.pk, slot.rank))
            entries.append(svc.GroupRankSeed(group=group, rank=slot.rank))
    matches = _scheduling(svc.generate_stage_matches, stage, entries=entries, with_placement=payload.with_placement)
    _activate(stage)
    return {"generated_count": len(matches)}


def _finished_stage(t, stage_id):
    stage = c.get_stage(t, stage_id)
    if not stage.matches.exists() or stage.matches.exclude(status=Match.Status.COMPLETE).exists():
        raise Invalid("Every match in this stage must be complete first.", {"detail": ["Every match in this stage must be complete first."]}, code="not_ready")
    return stage


@router.get("/{tournament_id}/stages/{stage_id}/next-phase", summary="Review each group's final standings and the slots they feed", operation_id="tournaments_stage_next_phase_get")
def next_phase_preview(request, tournament_id: uuid.UUID, stage_id: uuid.UUID):
    c.gate_view(request)
    stage = _finished_stage(c.get_tournament(tournament_id), stage_id)
    groups = []
    for group in stage.groups.order_by("sequence"):
        rows = []
        for row in svc.group_standings_preview(group):
            feeds = [
                {"match_id": f["match"].pk, "side": f["side"], "home_display": f["match"].home_display, "away_display": f["match"].away_display, "field": c.ref(f["match"].field), "scheduled_start": f["match"].scheduled_start}
                for f in row["feeds"]
            ]  # fmt: skip
            rows.append({**{k: v for k, v in row.items() if k not in ("team", "feeds")}, "team": c.team_ref(row["team"]), "feeds": feeds})
        groups.append({"group": c.ref(group), "standings": rows})
    return {"stage": c.stage_summary(stage), "groups": groups}


@router.post("/{tournament_id}/stages/{stage_id}/next-phase", summary="Confirm the group standings: fill the downstream slots with the real teams", operation_id="tournaments_stage_next_phase")
def next_phase(request, tournament_id: uuid.UUID, stage_id: uuid.UUID, payload: NextPhaseIn | None = None):
    c.gate_operate(request)
    stage = _finished_stage(c.get_tournament(tournament_id), stage_id)
    order = (payload.order if payload else None) or {}
    updated_count, conflicts = 0, []
    for group in stage.groups.all():
        standings = svc.group_standings_preview(group)
        given = {team_id: index for index, team_id in enumerate(order.get(group.pk, []))}
        ordered = sorted(standings, key=lambda row: (given.get(row["team"].pk, row["rank"]), row["rank"]))
        updated, group_conflicts = svc.resolve_group_slots(group, team_order=[row["team"] for row in ordered])
        updated_count += len(updated)
        conflicts.extend(group_conflicts)
    stage.status = TournamentStage.Status.COMPLETE
    stage.save(update_fields=["status"])
    return {"updated_count": updated_count, "conflicts": [str(x) for x in conflicts]}


# --- break rules -----------------------------------------------------------------------------------------------------------


def _rule_data(t, data: dict) -> dict:
    if "field_id" in data:
        field_id = data.pop("field_id")
        data["field"] = c.field_or_invalid(t, field_id) if field_id else None
    return data


@router.post("/{tournament_id}/break-rules", summary="Add a break rule", operation_id="tournaments_break_rule_create", response={201: dict})
def create_rule(request, tournament_id: uuid.UUID, payload: BreakRuleIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    rule = BreakRule(tournament=t)
    c.apply(rule, _rule_data(t, payload.dict()))
    c.validate(rule)
    rule.save()
    return 201, rule_out(rule)


@router.patch("/{tournament_id}/break-rules/{rule_id}", summary="Change a break rule", operation_id="tournaments_break_rule_update")
def update_rule(request, tournament_id: uuid.UUID, rule_id: uuid.UUID, payload: BreakRulePatch):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    rule = c.get_or_404(BreakRule, pk=rule_id, tournament=t)
    c.apply(rule, _rule_data(t, payload.dict(exclude_unset=True)))
    c.validate(rule)
    rule.save()
    return rule_out(rule)


@router.delete("/{tournament_id}/break-rules/{rule_id}", summary="Delete a break rule", operation_id="tournaments_break_rule_delete", response={204: None})
def delete_rule(request, tournament_id: uuid.UUID, rule_id: uuid.UUID):
    c.gate_operate(request)
    c.get_or_404(BreakRule, pk=rule_id, tournament=c.get_tournament(tournament_id)).delete()
    return 204, None
