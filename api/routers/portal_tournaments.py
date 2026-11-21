"""``/portal/tournaments``: volunteer shifts of the club's tournaments for the signed-in member. A shift is claimed for the member
themself (never a managed child); capacity is enforced by ``tournaments.services.tasks``. 404 when the club has no ``tournaments`` feature."""

import uuid

from django.db.models import Count
from django_tenants.utils import get_public_schema_name
from ninja import Router
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from events.services.tasks import claim_label_for
from formbuilder.services.management import Conflict
from members.models import Member
from tournaments.models import Tournament, TournamentTask
from tournaments.services import tasks as svc

router = Router(auth=[TokenAuth(door=False), CookieAuth(door=False)])

VISIBLE = (Tournament.Status.PUBLISHED, Tournament.Status.IN_PROGRESS)


def _member(request) -> Member:
    if request.tenant.schema_name == get_public_schema_name() or not request.features.has("tournaments"):
        raise HttpError(404, "Not found.")
    if not request.user.tenants.filter(pk=request.tenant.pk).exists():
        raise HttpError(403, "This account does not belong to this club.")
    member = Member.objects.filter(user=request.user).first()
    if member is None:
        raise HttpError(403, "This account has no member profile in this club.")
    return member


def _task_out(task: TournamentTask, me: Member) -> dict:
    claims = list(task.claims.all())
    return {
        "id": task.pk, "title": task.title, "description": task.description, "starts_at": task.starts_at, "ends_at": task.ends_at, "location_label": task.location_label,
        "needed": task.needed_quantity, "claimed": len(claims), "open_slots": max(task.needed_quantity - len(claims), 0), "is_full": len(claims) >= task.needed_quantity,
        "claimed_by_me": any(cl.member_id == me.pk for cl in claims), "claimed_by": [claim_label_for(cl.member) for cl in claims],
    }  # fmt: skip


def _task(task_id) -> TournamentTask:
    task = TournamentTask.objects.select_related("tournament").prefetch_related("claims__member").filter(pk=task_id, tournament__status__in=VISIBLE).first()
    if task is None:
        raise HttpError(404, "Not found.")
    return task


@router.get("", summary="Tournaments with volunteer shifts", operation_id="portal_tournaments")
def tournaments(request):
    _member(request)
    rows = Tournament.objects.filter(status__in=VISIBLE).annotate(shift_count=Count("tasks")).filter(shift_count__gt=0)
    return {"results": [{"slug": t.slug, "name": t.name, "start_date": t.start_date, "end_date": t.end_date, "shift_count": t.shift_count} for t in rows]}


@router.post("/tasks/{task_id}/claim", summary="Take a slot of a shift", operation_id="portal_tournament_task_claim")
def claim(request, task_id: uuid.UUID):
    me = _member(request)
    task = _task(task_id)
    try:
        svc.claim_task(task, me)
    except svc.TaskClaimError as error:
        raise Conflict(str(error), "cannot_claim") from error
    return _task_out(_task(task_id), me)


@router.delete("/tasks/{task_id}/claim", summary="Give a slot back", operation_id="portal_tournament_task_unclaim")
def unclaim(request, task_id: uuid.UUID):
    me = _member(request)
    task = _task(task_id)
    mine = task.claims.filter(member=me).first()
    if mine is not None:
        svc.unclaim_task(mine)
    return _task_out(_task(task_id), me)


@router.get("/{slug}/tasks", summary="The shifts of one tournament", operation_id="portal_tournament_tasks")
def tasks(request, slug: str):
    me = _member(request)
    tournament = Tournament.objects.filter(slug=slug, status__in=VISIBLE).first()
    if tournament is None:
        raise HttpError(404, "Not found.")
    rows = tournament.tasks.prefetch_related("claims__member").order_by("starts_at")
    return {"tournament": {"slug": tournament.slug, "name": tournament.name}, "results": [_task_out(t, me) for t in rows]}
