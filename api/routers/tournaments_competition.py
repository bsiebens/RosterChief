"""``/tournaments``: standings, the match schedule (list and per-field board), generating and clearing it, rescheduling, results, the
next Swiss round and schedule blocks. Reading needs either tournament permission; changing anything needs
``tournaments.operate_tournaments``. Scheduling and result rules live in ``tournaments.services.scheduling`` (``SchedulingError`` -> 422)."""

import datetime
import itertools
import uuid

from django.db import connection, transaction
from django.db.models import Max, Q
from django.utils import timezone
from ninja import Router, Schema

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from api.schemas_tournaments import BlockIn, BlockPatch, GenerateScheduleIn, MatchCreateIn, MatchTeamsPatch, ReorderIn, RescheduleIn, ResultIn
from formbuilder.services.management import Conflict
from members.services.errors import Invalid
from tournaments.models import Match, MatchAttribute, MatchAttributeValue, ScheduleBlock, Tournament, TournamentGroup, TournamentRosterEntry, TournamentStage, TournamentTeam
from tournaments.services import scheduling as svc

router = Router(auth=AUTHENTICATORS)


def _scheduling(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except svc.SchedulingError as error:
        raise Invalid(str(error), {"detail": [str(error)]}, code="scheduling") from error


def standing_row(row: dict) -> dict:
    return {
        "rank": row.get("rank"), "team": c.team_ref(row["team"]), "played": row["played"], "won": row["won"], "drawn": row["drawn"], "lost": row["lost"], "points": row["points"],
        "goals_for": row["goals_for"], "goals_against": row["goals_against"], "goal_diff": row.get("goal_diff", row["goals_for"] - row["goals_against"]),
        "attributes": row.get("attributes", {}),
    }  # fmt: skip


def _table_rows(stage, group=None) -> list[dict]:
    """The standings rows of a stage or group for display: the ranked teams, then every other team that is in its matches at zero, so a
    table is never empty just because nothing has been played yet. (The ranking service itself only knows teams with a finished match.)"""
    rows = svc.compute_standings(stage, group=group)
    seen = {r["team"].id for r in rows}
    matches = stage.matches.filter(group=group) if group is not None else stage.matches.all()
    idle = {}
    for match in matches.select_related("home_team__team", "away_team__team"):
        for team in (match.home_team, match.away_team):
            if team is not None and team.id not in seen:
                idle[team.id] = team
    keys = list(MatchAttribute.objects.filter(tournament_id=stage.tournament_id).values_list("key", flat=True))
    zero = [
        {"team": team, "played": 0, "won": 0, "drawn": 0, "lost": 0, "points": 0, "goals_for": 0, "goals_against": 0, "goal_diff": 0, "attributes": dict.fromkeys(keys, 0), "rank": len(rows) + i}
        for i, team in enumerate(sorted(idle.values(), key=lambda t: t.display_name.lower()), start=1)
    ]
    return [standing_row(r) for r in [*rows, *zero]]


def final_standings(tournament) -> list[dict]:
    """The final ranking, once every game is played: in the last knockout stage the trailing rounds with a single tie decide ranks in
    pairs (Final: 1st and 2nd, then 3rd place: 3rd and 4th, 5th place ...), best first. Empty until everything is complete."""
    matches = Match.objects.filter(stage__tournament=tournament).select_related("home_team__team", "away_team__team")
    if not matches.exists() or matches.exclude(status=Match.Status.COMPLETE).exists():
        return []
    stage = tournament.stages.filter(format__in=[TournamentStage.Format.SINGLE_ELIM, TournamentStage.Format.DOUBLE_ELIM]).order_by("-sequence").first()
    if stage is None:
        return []
    rounds: dict[int, list] = {}
    for m in matches.filter(stage=stage, leg_of__isnull=True):
        rounds.setdefault(m.round_number, []).append(m)
    out, rank = [], 1
    for number in sorted(rounds, reverse=True):
        ties = rounds[number]
        if len(ties) != 1 or ties[0].winner is None:
            break
        out += [{"rank": rank, "team": c.team_ref(ties[0].winner)}, {"rank": rank + 1, "team": c.team_ref(ties[0].loser)}]
        rank += 2
    return out


def standings_doc(tournament) -> dict:
    """``standings``: one entry per table-like stage (groups, round robin, Swiss ...) with its tables; ``brackets``: knockout stages as rounds of matches."""
    standings, brackets = [], []
    for stage in tournament.stages.order_by("sequence").prefetch_related("groups"):
        if stage.format == TournamentStage.Format.GROUPS:
            tables = [{"group": c.group_ref(g), "rows": _table_rows(stage, g)} for g in stage.groups.filter(matches__isnull=False).distinct().order_by("sequence")]
            standings.append({"stage": c.stage_summary(stage), "tables": tables, "attribute_columns": c.stage_attribute_columns(stage)})
        elif stage.format in (TournamentStage.Format.SINGLE_ELIM, TournamentStage.Format.DOUBLE_ELIM):
            matches = list(stage.matches.select_related("home_team__team", "away_team__team", "home_source_match", "away_source_match", "home_source_group", "away_source_group").order_by("round_number", "sequence"))
            brackets.append({"stage": c.stage_summary(stage), "rounds": c.round_groups(matches)})
        else:
            standings.append({"stage": c.stage_summary(stage), "tables": [{"group": None, "rows": _table_rows(stage)}], "attribute_columns": c.stage_attribute_columns(stage)})
    return {"standings": standings, "brackets": brackets, "final_standings": final_standings(tournament)}


def stages_doc(tournament, *, with_flags=False) -> list[dict]:
    """Every match grouped by stage then round. ``with_flags`` adds what the results page needs: a finished groups stage that others
    depend on can ``ready_for_next_phase``; a Swiss stage whose last round is in can ``swiss_ready_for_next_round``."""
    out = []
    for stage, matches in itertools.groupby(c.stage_matches(tournament), key=lambda m: m.stage):
        doc = {"stage": c.stage_summary(stage), "rounds": c.round_groups(list(matches))}
        if with_flags:
            has_dependents = Match.objects.filter(Q(home_source_group__stage=stage) | Q(away_source_group__stage=stage)).exists()
            doc["ready_for_next_phase"] = stage.format == TournamentStage.Format.GROUPS and has_dependents and stage.matches.exists() and not stage.matches.exclude(status=Match.Status.COMPLETE).exists()
            doc["swiss_ready_for_next_round"] = False
            if stage.format == TournamentStage.Format.SWISS and doc["rounds"]:
                last = doc["rounds"][-1]
                total = (stage.config or {}).get("rounds")
                doc["swiss_ready_for_next_round"] = last["all_complete"] and not (total and last["round_number"] + 1 > total)
        out.append(doc)
    return out


def block_out(block: ScheduleBlock) -> dict:
    """``starts_at``/``ends_at`` are the whole reserved window (for a lunch block that includes the padding); ``lunch_start``/``lunch_end`` are
    the real sitting, null for manual blocks."""
    return {
        "id": block.pk, "title": block.title, "kind": block.kind, "starts_at": block.starts_at, "ends_at": block.ends_at, "lunch_start": block.lunch_start, "lunch_end": block.lunch_end,
        "field": c.ref(block.field), "teams": [c.team_ref(t) for t in block.teams.all()],
        "location": block.location or (block.tournament.lunch_location if block.kind == ScheduleBlock.Kind.LUNCH else ""),
    }  # fmt: skip


def _blocks(tournament):
    return [block_out(b) for b in tournament.schedule_blocks.select_related("field").prefetch_related("teams")]


def _head(tournament) -> dict:
    return {"id": tournament.pk, "name": tournament.name, "default_match_duration_minutes": tournament.default_match_duration_minutes, "schedule_needs_regeneration": tournament.schedule_needs_regeneration}


@router.get("/{tournament_id}/standings", summary="Standings per stage and group, and the knockout brackets", operation_id="tournaments_standings")
def standings(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    return {"tournament": _head(t), **standings_doc(t)}


@router.get("/{tournament_id}/schedule", summary="Every match by stage and round, with the fields and schedule blocks", operation_id="tournaments_schedule")
def schedule(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    return {"tournament": _head(t), "stages": stages_doc(t), "fields": [c.ref(f) for f in t.fields.all()], "blocks": _blocks(t)}


@router.get("/{tournament_id}/schedule/board", summary="Per-field timeline: matches and the idle gaps between them", operation_id="tournaments_schedule_board")
def board(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    fields = []
    for entry in svc.field_timeline(t):
        days = []
        for day in entry["days"]:
            slots = []
            c.prime_matches([slot["match"] for slot in day["slots"] if slot["type"] == "match"])
            for slot in day["slots"]:
                doc = {k: slot[k] for k in ("type", "start", "end", "minutes", "height_px", "top_px")}
                if slot["type"] == "match":
                    doc["match"] = c.match_out(slot["match"], with_field=False)
                else:
                    rule = slot["rule"]
                    doc["rule"] = {"id": rule.pk, "summary": rule.display_summary} if rule is not None else None
                slots.append(doc)
            days.append({"date": day["date"], "start": day["start"], "end": day["end"], "height_px": day["height_px"], "hour_marks": day["hour_marks"], "slots": slots})
        rules = [{"id": r.pk, "summary": r.display_summary} for r in entry["rules"]]
        fields.append({"field": c.ref(entry["field"]), "rules": rules, "days": days})
    sport = getattr(connection.tenant, "sport", None)  # the club's sport decides how the playing surface is drawn (a full rink for ice hockey)
    return {"tournament": _head(t), "px_per_minute": svc.PX_PER_MINUTE, "sport": {"slug": sport.slug, "name": sport.name} if sport else None, "fields": fields}


@router.post("/{tournament_id}/schedule/generate", summary="Generate (re-plan) the schedule of every unplayed match", operation_id="tournaments_schedule_generate")
def generate(request, tournament_id: uuid.UUID, payload: GenerateScheduleIn | None = None):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    minutes = (payload.match_duration_minutes if payload else None) or t.default_match_duration_minutes
    scheduled = _scheduling(svc.allocate_schedule, t, match_duration=datetime.timedelta(minutes=minutes), reset=True)
    return {"scheduled_count": len(scheduled)}


@router.post("/{tournament_id}/schedule/clear", summary="Unschedule every match that is not played yet", operation_id="tournaments_schedule_clear")
def clear(request, tournament_id: uuid.UUID):
    c.gate_operate(request)
    return {"cleared_count": svc.clear_schedule(c.get_tournament(tournament_id))}


@router.get("/{tournament_id}/scores", summary="Every match with its result, plus what can be started next", operation_id="tournaments_scores")
def scores(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    return {"tournament": _head(t), "stages": stages_doc(t, with_flags=True)}


@router.post("/{tournament_id}/stages/{stage_id}/swiss/next-round", summary="Pair the next Swiss round from the standings", operation_id="tournaments_swiss_next_round")
def swiss_next_round(request, tournament_id: uuid.UUID, stage_id: uuid.UUID):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    stage = c.get_stage(t, stage_id)
    matches = _scheduling(svc.generate_next_swiss_round, stage)
    return {"generated_count": len(matches)}


# --- one match -------------------------------------------------------------------------------------------------------------


def _match(match_id) -> Match:
    return c.get_or_404(Match.objects.select_related("stage__tournament", "field", "home_team__team", "away_team__team", "group", "home_source_match", "away_source_match", "home_source_group", "away_source_group"), pk=match_id)


@router.post("/matches/{match_id}/reschedule", summary="Move a match to a field and time", operation_id="tournaments_match_reschedule")
def reschedule(request, match_id: uuid.UUID, payload: RescheduleIn):
    c.gate_operate(request)
    match = _match(match_id)
    field = c.field_or_invalid(match.stage.tournament, payload.field_id)
    start = payload.scheduled_start
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    _scheduling(svc.reschedule_match, match, field=field, scheduled_start=start)
    return c.match_out(_match(match_id))


@router.post("/{tournament_id}/schedule/reorder", summary="Hand the same times and fields to the matches in a new order", operation_id="tournaments_schedule_reorder")
def reorder(request, tournament_id: uuid.UUID, payload: ReorderIn):
    """The scheduled matches listed keep the set of (field, start) slots they occupy now; the slots are dealt out again in the order
    given (earliest slot to the first match). Unscheduled matches in the list are left alone. All or nothing: a placement that breaks a
    rule rolls everything back."""
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    found = {m.pk: m for m in Match.objects.select_related("field", "stage__tournament").filter(stage__tournament=t, pk__in=payload.match_ids)}
    if len(found) != len(set(payload.match_ids)):
        raise Invalid("Unknown match.", {"match_ids": ["Unknown match."]})
    ordered = [found[i] for i in payload.match_ids]
    if any(m.status == Match.Status.COMPLETE for m in ordered):
        raise Invalid("A played match cannot be moved.", {"detail": ["A played match cannot be moved."]}, code="match_played")
    scheduled = [m for m in ordered if m.scheduled_start is not None and m.field_id is not None]
    slots = sorted(((m.scheduled_start, m.field) for m in scheduled), key=lambda slot: (slot[0], slot[1].name))
    with transaction.atomic():
        Match.objects.filter(pk__in=[m.pk for m in scheduled]).update(scheduled_start=None, field=None)  # free the slots first, so swapping two matches never clashes with itself
        for match, (start, field) in zip(scheduled, slots, strict=True):
            match.refresh_from_db()
            _scheduling(svc.reschedule_match, match, field=field, scheduled_start=start)
    return {"matches": [c.match_out(_match(m.pk)) for m in ordered]}


def _manual_values(tournament, submitted) -> list:
    """``[(attribute, value)]`` for the submitted manual attribute values; an unknown key or a timeline attribute is a 422."""
    out = []
    by_key = {a.key: a for a in tournament.match_attributes.all()}
    for key, value in (submitted or {}).items():
        attribute = by_key.get(key)
        if attribute is None or attribute.source != MatchAttribute.Source.MANUAL:
            message = "Unknown attribute." if attribute is None else "This attribute comes from the timeline and cannot be entered by hand."
            raise Invalid(message, {f"attributes.{key}": [message]})
        out.append((attribute, value))
    return out


def _decided_result(match, payload) -> tuple[int | None, int | None]:
    """The penalty shots to store for a result, after checking the stage's draw rule: a level score is only accepted when the stage allows
    draws, or when penalty shots (filled in and different) decide it. Shots are dropped from a result that is not level. Back-and-forth ties:
    the first leg may end level, the second leg is judged on the two scores added together."""
    stage = match.stage
    if match.leg_of_id is not None:
        return None, None  # a first leg never needs a winner
    home, away = payload.score_home, payload.score_away
    if match.first_leg is not None:
        first = match.first_leg
        if first.score_home is None or first.score_away is None:
            message = "Enter the first leg before the second."
            raise Invalid(message, {"detail": [message]}, code="first_leg_missing")
        home, away = home + first.score_home, away + first.score_away
    if home != away:
        return None, None
    if stage.allows_draws:
        return payload.penalty_home, payload.penalty_away
    if not stage.uses_penalty_shots:
        message = "This stage does not allow draws: enter the final score including any overtime goals."
        raise Invalid(message, {"score_away": [message]}, code="draw_not_allowed")
    if payload.penalty_home is None or payload.penalty_away is None:
        message = "The tie is level: enter the penalty shots score that decided it." if match.first_leg is not None else "The game is level: enter the penalty shots score that decided it."
        raise Invalid(message, {"penalty_home": [message]}, code="penalty_shots_required")
    if payload.penalty_home == payload.penalty_away:
        message = "Penalty shots must have a winner."
        raise Invalid(message, {"penalty_away": [message]}, code="penalty_shots_level")
    return payload.penalty_home, payload.penalty_away


@router.post("/matches/{match_id}/result", summary="Enter the final score; later bracket slots fill in from it", operation_id="tournaments_match_result")
def result(request, match_id: uuid.UUID, payload: ResultIn):
    c.gate_operate(request)
    match = _match(match_id)
    if match.home_team_id is None or match.away_team_id is None:
        raise Invalid("Both teams must be known before a score can be recorded.", {"detail": ["Both teams must be known before a score can be recorded."]}, code="teams_unknown")
    values = _manual_values(match.stage.tournament, payload.attributes)
    penalty_home, penalty_away = _decided_result(match, payload)
    with transaction.atomic():
        updated = svc.record_result(
            match, score_home=payload.score_home, score_away=payload.score_away, went_to_overtime=payload.went_to_overtime, penalty_home=penalty_home, penalty_away=penalty_away
        )  # fmt: skip
        for attribute, value in values:
            MatchAttributeValue.objects.update_or_create(match=match, attribute=attribute, defaults={"home_value": value.home, "away_value": value.away})
    return {"match": c.match_out(_match(match_id)), "updated_match_ids": [m.pk for m in updated]}


class GoaliesIn(Schema):
    home_goalie_id: uuid.UUID | None = None
    away_goalie_id: uuid.UUID | None = None


def _set_goalies(match, payload: GoaliesIn) -> None:
    """The goalies in goal at the start: players of the two teams (None clears)."""
    for side in ("home", "away"):
        entry_id = getattr(payload, f"{side}_goalie_id")
        if entry_id is None:
            setattr(match, f"{side}_goalie", None)
            continue
        team = match.home_team if side == "home" else match.away_team
        entry = TournamentRosterEntry.objects.filter(pk=entry_id, tournament_team=team).first() if team is not None else None
        if entry is None:
            message = "Choose a player of this team."
            raise Invalid(message, {f"{side}_goalie_id": [message]})
        setattr(match, f"{side}_goalie", entry)


@router.post("/matches/{match_id}/start", summary="Start a game: it goes live; the goalies in goal can be marked", operation_id="tournaments_match_start")
def start(request, match_id: uuid.UUID, payload: GoaliesIn | None = None):
    c.gate_operate(request)
    match = _match(match_id)
    if match.status != Match.Status.SCHEDULED:
        raise Conflict("This game has already been started.", "already_started")
    if match.home_team_id is None or match.away_team_id is None:
        raise Invalid("Both teams must be known before a game can start.", {"detail": ["Both teams must be known before a game can start."]}, code="teams_unknown")
    if payload is not None:
        _set_goalies(match, payload)
    match.status = Match.Status.IN_PROGRESS
    match.save(update_fields=["status", "home_goalie", "away_goalie"])
    return c.match_out(_match(match_id))


@router.put("/matches/{match_id}/goalies", summary="Mark the goalies in goal", operation_id="tournaments_match_goalies")
def goalies(request, match_id: uuid.UUID, payload: GoaliesIn):
    c.gate_operate(request)
    match = _match(match_id)
    _set_goalies(match, payload)
    match.save(update_fields=["home_goalie", "away_goalie"])
    return c.match_out(_match(match_id))


@router.post("/matches/{match_id}/reopen", summary="Reopen a completed game so its timeline can be changed again", operation_id="tournaments_match_reopen")
def reopen(request, match_id: uuid.UUID):
    c.gate_operate(request)
    match = _match(match_id)
    if match.status != Match.Status.COMPLETE:
        raise Conflict("This game is not completed.", "not_complete")
    played = Match.objects.filter(Q(home_source_match=match) | Q(away_source_match=match), status__in=[Match.Status.COMPLETE, Match.Status.IN_PROGRESS, Match.Status.WALKOVER]).exists()
    if played:
        raise Conflict("A later game that uses this result has already been played.", "has_dependents_played")
    match.status = Match.Status.IN_PROGRESS  # live again: the score, shots and timeline stay; completing again records the result and advances the bracket again
    match.save(update_fields=["status"])
    return c.match_out(_match(match_id))


# --- editing generated matches ---------------------------------------------------------------------------------------------


def _slot_team(tournament, team_id, key):
    team = TournamentTeam.objects.select_related("team").filter(pk=team_id, tournament=tournament).first()
    if team is None:
        raise Invalid("Unknown team.", {key: ["Unknown team."]})
    if team.withdrawn_at is not None:
        raise Invalid("That team has withdrawn.", {key: ["That team has withdrawn."]})
    return team


def _teams_differ(home_id, away_id):
    if home_id is not None and home_id == away_id:
        raise Invalid("Home and away must be different teams.", {"away_team_id": ["Home and away must be different teams."]})


def _clear_source(match, side):
    for suffix, empty in (("source_match", None), ("source_is_winner", True), ("source_group", None), ("source_rank", None)):
        setattr(match, f"{side}_{suffix}", empty)


@router.patch("/matches/{match_id}/teams", summary="Set, swap or clear the teams of a match that is not played yet", operation_id="tournaments_match_teams")
def set_teams(request, match_id: uuid.UUID, payload: MatchTeamsPatch):
    c.gate_operate(request)
    match = _match(match_id)
    if match.leg_of_id is not None:
        match = _match(match.leg_of_id)  # the teams of a tie are set on the tie; both legs follow
    if match.status == Match.Status.COMPLETE or any(leg.status == Match.Status.COMPLETE for leg in match.first_legs.all()):
        raise Conflict("A played match cannot be changed.", "match_complete")
    tournament = match.stage.tournament
    data = payload.dict(exclude_unset=True)
    for side in ("home", "away"):
        key = f"{side}_team_id"
        if key not in data:
            continue
        team = _slot_team(tournament, data[key], key) if data[key] is not None else None
        setattr(match, f"{side}_team", team)
        _clear_source(match, side)  # a manual choice replaces whatever used to feed the slot
    _teams_differ(match.home_team_id, match.away_team_id)
    match.save()
    svc.sync_first_leg(match)
    return c.match_out(_match(match.pk))


@router.delete("/matches/{match_id}", summary="Delete a match that is not played and feeds no other match", operation_id="tournaments_match_delete", response={204: None})
def delete_match(request, match_id: uuid.UUID):
    c.gate_operate(request)
    match = _match(match_id)
    if match.leg_of_id is not None:
        match = _match(match.leg_of_id)  # a tie goes as a whole, both legs
    if match.status == Match.Status.COMPLETE or any(leg.status == Match.Status.COMPLETE for leg in match.first_legs.all()):
        raise Conflict("A played match cannot be deleted.", "match_complete")
    dependents = list(Match.objects.filter(Q(home_source_match=match) | Q(away_source_match=match)).select_related("home_team__team", "away_team__team", "home_source_match", "away_source_match", "home_source_group", "away_source_group"))
    if dependents:
        names = ", ".join(f"{d.round_label or 'Round ' + str(d.round_number)} #{d.sequence + 1} ({d})" for d in dependents)
        raise Conflict(f"Other matches take their team from this one: {names}.", "has_dependents")
    tournament, was_scheduled = match.stage.tournament, match.scheduled_start is not None
    match.delete()
    if was_scheduled and Match.objects.filter(stage__tournament=tournament, scheduled_start__isnull=False).exists():
        Tournament.objects.filter(pk=tournament.pk, schedule_needs_regeneration=False).update(schedule_needs_regeneration=True)
    return 204, None


@router.post("/{tournament_id}/stages/{stage_id}/matches", summary="Add a match to a stage by hand", operation_id="tournaments_match_create", response={201: dict})
def create_match(request, tournament_id: uuid.UUID, stage_id: uuid.UUID, payload: MatchCreateIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    stage = c.get_stage(t, stage_id)
    if (payload.field_id is None) != (payload.scheduled_start is None):
        key = "scheduled_start" if payload.scheduled_start is None else "field_id"
        raise Invalid("Give both a field and a start time, or neither.", {key: ["Give both a field and a start time, or neither."]})
    home = _slot_team(t, payload.home_team_id, "home_team_id") if payload.home_team_id else None
    away = _slot_team(t, payload.away_team_id, "away_team_id") if payload.away_team_id else None
    _teams_differ(payload.home_team_id, payload.away_team_id)
    group = None
    if payload.group_id is not None:
        group = TournamentGroup.objects.filter(pk=payload.group_id, stage=stage).first()
        if group is None:
            raise Invalid("Unknown group.", {"group_id": ["Unknown group."]})
    field = c.field_or_invalid(t, payload.field_id) if payload.field_id else None
    round_number = payload.round_number or stage.matches.aggregate(n=Max("round_number"))["n"] or 1
    siblings = stage.matches.filter(round_number=round_number)
    if group is not None:
        siblings = siblings.filter(group=group)
    last = siblings.aggregate(n=Max("sequence"))["n"]
    with transaction.atomic():
        match = Match(stage=stage, group=group, round_number=round_number, round_label=payload.round_label, sequence=0 if last is None else last + 1, home_team=home, away_team=away)
        c.validate(match)
        match.save()
        if stage.is_two_legged:
            svc.add_first_leg(match)
        if field is not None:
            start = payload.scheduled_start
            if timezone.is_naive(start):
                start = timezone.make_aware(start)
            _scheduling(svc.reschedule_match, match, field=field, scheduled_start=start)
    return 201, c.match_out(_match(match.pk))


# --- schedule blocks -------------------------------------------------------------------------------------------------------


def _block_teams(tournament, team_ids):
    teams = list(TournamentTeam.objects.filter(tournament=tournament, withdrawn_at__isnull=True, pk__in=team_ids))
    if len(teams) != len(set(team_ids)):
        raise Invalid("Unknown team.", {"team_ids": ["Unknown team."]})
    return teams


@router.post("/{tournament_id}/schedule-blocks", summary="Reserve time on a field and/or for teams (lunch, ceremony)", operation_id="tournaments_block_create", response={201: dict})
def create_block(request, tournament_id: uuid.UUID, payload: BlockIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    field = c.field_or_invalid(t, payload.field_id) if payload.field_id else None
    teams = _block_teams(t, payload.team_ids)
    block = ScheduleBlock(tournament=t, title=payload.title, starts_at=payload.starts_at, ends_at=payload.ends_at, field=field, location=payload.location.strip())
    c.validate(block)
    block.save()
    block.teams.set(teams)
    return 201, block_out(block)


@router.patch("/{tournament_id}/schedule-blocks/{block_id}", summary="Change a schedule block", operation_id="tournaments_block_update")
def update_block(request, tournament_id: uuid.UUID, block_id: uuid.UUID, payload: BlockPatch):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    block = c.get_or_404(ScheduleBlock, pk=block_id, tournament=t)
    data = payload.dict(exclude_unset=True)
    if "field_id" in data:
        field_id = data.pop("field_id")
        block.field = c.field_or_invalid(t, field_id) if field_id else None
    team_ids = data.pop("team_ids", None)
    teams = _block_teams(t, team_ids) if team_ids is not None else None
    if data.get("location") is not None:
        data["location"] = data["location"].strip()
    c.apply(block, data)
    c.validate(block)
    block.save()
    if teams is not None:
        block.teams.set(teams)
    return block_out(block)


@router.delete("/{tournament_id}/schedule-blocks/{block_id}", summary="Delete a schedule block", operation_id="tournaments_block_delete", response={204: None})
def delete_block(request, tournament_id: uuid.UUID, block_id: uuid.UUID):
    c.gate_operate(request)
    c.get_or_404(ScheduleBlock, pk=block_id, tournament=c.get_tournament(tournament_id)).delete()
    return 204, None
