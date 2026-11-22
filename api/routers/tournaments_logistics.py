"""``/tournaments/{id}/tasks|dressing-rooms`` and club-wide ``/dressing-rooms``: volunteer shifts of a tournament (list, create, generate a run
of shifts, edit, delete, assign a member, print overview) and dressing rooms (the club's rooms; booking them for tournament teams).
Reading needs either tournament permission; shifts and bookings need ``tournaments.operate_tournaments``; the club's own list of rooms
needs ``tournaments.manage_tournaments``. Members claim shifts themselves through ``/portal/tournaments``."""

import itertools
import uuid

from ninja import Router

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from api.schemas_tournaments import DressingAssignIn, DressingRoomIn, DressingRoomPatch, TaskAssignIn, TaskGenerateIn, TaskIn, TaskPatch
from events.services.tasks import claim_label_for
from formbuilder.services.management import Conflict
from members.models import Member
from members.services.errors import Invalid
from tournaments.models import DressingRoom, DressingRoomAssignment, TournamentTask, TournamentTaskClaim, TournamentTeam
from tournaments.services import dressing_rooms as rooms_svc
from tournaments.services import tasks as tasks_svc

router = Router(auth=AUTHENTICATORS)
rooms_router = Router(auth=AUTHENTICATORS)


# --- tasks -----------------------------------------------------------------------------------------------------------------


def task_out(task: TournamentTask) -> dict:
    claims = list(task.claims.all())
    return {
        "id": task.pk, "title": task.title, "description": task.description, "starts_at": task.starts_at, "ends_at": task.ends_at, "location_label": task.location_label,
        "needed_quantity": task.needed_quantity, "claimed_count": len(claims), "open_slots": max(task.needed_quantity - len(claims), 0), "is_full": len(claims) >= task.needed_quantity,
        "claims": [{"id": cl.pk, "member": c.ref(cl.member), "label": claim_label_for(cl.member), "claimed_at": cl.claimed_at} for cl in claims],
    }  # fmt: skip


def _tasks(tournament, order=("starts_at",)):
    return list(tournament.tasks.prefetch_related("claims__member").order_by(*order))


def _task(tournament, task_id) -> TournamentTask:
    return c.get_or_404(TournamentTask.objects.prefetch_related("claims__member"), pk=task_id, tournament=tournament)


def _creator(request):
    return Member.objects.filter(user=request.user).first()


@router.get("/{tournament_id}/tasks", summary="Volunteer shifts of a tournament, by time", operation_id="tournaments_tasks")
def list_tasks(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    return {"results": [task_out(t) for t in _tasks(c.get_tournament(tournament_id))]}


@router.get("/{tournament_id}/tasks/print", summary="Print overview: shifts per location with timeline positions", operation_id="tournaments_tasks_print")
def print_tasks(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    tasks = _tasks(c.get_tournament(tournament_id), order=("location_label", "starts_at"))
    start = min((t.starts_at for t in tasks), default=None)
    end = max((t.ends_at for t in tasks), default=None)
    window = (end - start).total_seconds() if start and end else 0
    docs = {}
    for task in tasks:
        doc = task_out(task)
        doc["timeline_left_pct"] = (task.starts_at - start).total_seconds() / window * 100 if window > 0 else 0
        doc["timeline_width_pct"] = (task.ends_at - task.starts_at).total_seconds() / window * 100 if window > 0 else 100
        docs[task.pk] = doc
    locations = [{"location_label": label, "tasks": [docs[t.pk] for t in group]} for label, group in itertools.groupby(tasks, key=lambda t: t.location_label)]
    return {"timeline_start": start, "timeline_end": end, "locations": locations}


@router.post("/{tournament_id}/tasks/generate", summary="Create a run of same-length shifts between two times", operation_id="tournaments_tasks_generate", response={201: dict})
def generate_tasks(request, tournament_id: uuid.UUID, payload: TaskGenerateIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    if payload.ends_at <= payload.starts_at:
        raise Invalid("Must be after the start time.", {"ends_at": ["Must be after the start time."]})
    tasks, extended = tasks_svc.generate_task_shifts(
        t, title=payload.title, location_label=payload.location_label, starts_at=payload.starts_at, ends_at=payload.ends_at, shift_minutes=payload.shift_minutes,
        needed_quantity=payload.needed_quantity, created_by=_creator(request),
    )  # fmt: skip
    return 201, {"created_count": len(tasks), "last_shift_extended": extended, "results": [task_out(_task(t, task.pk)) for task in tasks]}


@router.post("/{tournament_id}/tasks/clear", summary="Remove every shift of the tournament, with the volunteers signed up for them", operation_id="tournaments_tasks_clear")
def clear_tasks(request, tournament_id: uuid.UUID):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    removed = t.tasks.count()
    t.tasks.all().delete()  # the claims go with their shift
    return {"cleared_count": removed}


@router.post("/{tournament_id}/tasks", summary="Add one shift", operation_id="tournaments_task_create", response={201: dict})
def create_task(request, tournament_id: uuid.UUID, payload: TaskIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    task = TournamentTask(tournament=t, created_by=_creator(request), **payload.dict())
    c.validate(task)
    task.save()
    return 201, task_out(_task(t, task.pk))


@router.patch("/{tournament_id}/tasks/{task_id}", summary="Change a shift", operation_id="tournaments_task_update")
def update_task(request, tournament_id: uuid.UUID, task_id: uuid.UUID, payload: TaskPatch):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    task = _task(t, task_id)
    c.apply(task, payload.dict(exclude_unset=True))
    c.validate(task)
    task.save()
    return task_out(_task(t, task_id))


@router.delete("/{tournament_id}/tasks/{task_id}", summary="Delete a shift with its claims", operation_id="tournaments_task_delete", response={204: None})
def delete_task(request, tournament_id: uuid.UUID, task_id: uuid.UUID):
    c.gate_operate(request)
    _task(c.get_tournament(tournament_id), task_id).delete()
    return 204, None


@router.post("/{tournament_id}/tasks/{task_id}/assign", summary="Put a member on a shift", operation_id="tournaments_task_assign")
def assign_task(request, tournament_id: uuid.UUID, task_id: uuid.UUID, payload: TaskAssignIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    task = _task(t, task_id)
    member = Member.objects.filter(pk=payload.member_id).first()
    if member is None:
        raise Invalid("Unknown member.", {"member_id": ["Unknown member."]})
    try:
        tasks_svc.claim_task(task, member)
    except tasks_svc.TaskClaimError as error:
        raise Conflict(str(error), "cannot_assign") from error
    return task_out(_task(t, task_id))


@router.delete("/{tournament_id}/tasks/{task_id}/claims/{claim_id}", summary="Take someone off a shift", operation_id="tournaments_task_unassign")
def unassign_task(request, tournament_id: uuid.UUID, task_id: uuid.UUID, claim_id: uuid.UUID):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    task = _task(t, task_id)
    tasks_svc.unclaim_task(c.get_or_404(TournamentTaskClaim, pk=claim_id, task=task))
    return task_out(_task(t, task_id))


# --- dressing rooms (the club's rooms) -------------------------------------------------------------------------------------


def room_out(room: DressingRoom) -> dict:
    return {"id": room.pk, "name": room.name, "capacity": room.capacity, "notes": room.notes}


@rooms_router.get("", summary="The club's dressing rooms", operation_id="dressing_rooms_list")
def list_rooms(request):
    c.gate_view(request)
    return {"results": [room_out(r) for r in DressingRoom.objects.all()]}


@rooms_router.post("", summary="Add a dressing room", operation_id="dressing_rooms_create", response={201: dict})
def create_room(request, payload: DressingRoomIn):
    c.gate_manage(request)
    room = DressingRoom(name=payload.name.strip(), capacity=payload.capacity, notes=payload.notes)
    c.validate(room)
    room.save()
    return 201, room_out(room)


@rooms_router.patch("/{room_id}", summary="Change a dressing room", operation_id="dressing_rooms_update")
def update_room(request, room_id: uuid.UUID, payload: DressingRoomPatch):
    c.gate_manage(request)
    room = c.get_or_404(DressingRoom, pk=room_id)
    c.apply(room, payload.dict(exclude_unset=True))
    c.validate(room)
    room.save()
    return room_out(room)


@rooms_router.delete("/{room_id}", summary="Delete a dressing room", operation_id="dressing_rooms_delete", response={204: None})
def delete_room(request, room_id: uuid.UUID):
    c.gate_manage(request)
    c.get_or_404(DressingRoom, pk=room_id).delete()
    return 204, None


# --- bookings per tournament -----------------------------------------------------------------------------------------------


def assignment_out(a: DressingRoomAssignment, conflicts=()) -> dict:
    return {
        "id": a.pk, "room": c.ref(a.dressing_room), "team": c.team_ref(a.tournament_team), "assigned_from": a.assigned_from, "assigned_until": a.assigned_until, "note": a.note,
        "conflicts": [{"assignment_id": other.pk, "label": label} for other, label in conflicts],
    }  # fmt: skip


@router.get("/{tournament_id}/dressing-rooms", summary="Teams, rooms and bookings (overlaps flagged)", operation_id="tournaments_dressing_rooms")
def tournament_rooms(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    teams = list(t.teams.filter(withdrawn_at__isnull=True).select_related("team").prefetch_related("dressing_room_assignments__dressing_room"))
    everything = [a for team in teams for a in team.dressing_room_assignments.all()]
    out = []
    for team in teams:
        rows = []
        for a in team.dressing_room_assignments.all():
            conflicts = [
                (o, o.tournament_team.display_name if o.dressing_room_id == a.dressing_room_id else o.dressing_room.name)
                for o in everything
                if o.pk != a.pk
                and (o.dressing_room_id == a.dressing_room_id or o.tournament_team_id == a.tournament_team_id)
                and rooms_svc.windows_conflict(a.assigned_from, a.assigned_until, o.assigned_from, o.assigned_until)
            ]  # fmt: skip
            rows.append(assignment_out(a, conflicts))
        out.append({"team": c.team_ref(team), "assignments": rows})
    return {"teams": out, "rooms": [room_out(r) for r in DressingRoom.objects.all()]}


@router.post("/{tournament_id}/dressing-rooms/assignments", summary="Book a dressing room for a team", operation_id="tournaments_dressing_assign", response={201: dict})
def assign_room(request, tournament_id: uuid.UUID, payload: DressingAssignIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    room = DressingRoom.objects.filter(pk=payload.dressing_room_id).first()
    if room is None:
        raise Invalid("Unknown dressing room.", {"dressing_room_id": ["Unknown dressing room."]})
    team = TournamentTeam.objects.filter(pk=payload.tournament_team_id, tournament=t, withdrawn_at__isnull=True).select_related("team").first()
    if team is None:
        raise Invalid("Unknown team.", {"tournament_team_id": ["Unknown team."]})
    if payload.assigned_from and payload.assigned_until and payload.assigned_until <= payload.assigned_from:
        raise Invalid("Must be after the start.", {"assigned_until": ["Must be after the start."]})
    try:
        assignment = rooms_svc.assign_dressing_room(room, team, assigned_from=payload.assigned_from, assigned_until=payload.assigned_until, note=payload.note)
    except rooms_svc.DressingRoomAssignmentError as error:
        raise Conflict(str(error), "already_assigned") from error
    return 201, assignment_out(assignment)


@router.delete("/{tournament_id}/dressing-rooms/assignments/{assignment_id}", summary="Release a booking", operation_id="tournaments_dressing_release", response={204: None})
def release_room(request, tournament_id: uuid.UUID, assignment_id: uuid.UUID):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    rooms_svc.release_dressing_room(c.get_or_404(DressingRoomAssignment, pk=assignment_id, tournament_team__tournament=t))
    return 204, None
