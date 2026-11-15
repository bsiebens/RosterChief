"""Club app endpoints scoped to the signed-in member and their children."""

from datetime import timedelta
from typing import Literal
from uuid import UUID

from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django_tenants.utils import get_public_schema_name
from ninja import Field, Router, Schema
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from api.routers.public_forms import _field as form_field
from api.routers.public_news import public_news_item
from events.models import Attendance, Event, EventAttachment, EventTask, Lineup, LineupSelection, Season
from events.services import attachments as attachment_svc
from events.services import attendance, officiating_invitations, referees
from events.services import tasks as task_svc
from events.services.attachments import ALLOWED_EXTENSIONS, MAX_ATTACHMENT_BYTES
from formbuilder.models import FormSend
from formbuilder.services import audience
from formbuilder.services.submission import submit_form
from members.models import ClubMembership, Member
from members.services import access, memberships
from members.services.errors import Invalid
from news.services import member_news
from registration.models import RegistrationBatch, RegistrationSettings
from registration.services import installments, notifications
from shop.services.invoices import PDFUnavailable
from sponsoring.models import Sponsor
from teams.models import Team, TeamMembership

router = Router(auth=[TokenAuth(door=False), CookieAuth(door=False)])


class Reply(Schema):
    status: Literal["present", "absent", "maybe", "dropout"]
    note: str = Field("", max_length=2000)


class OfficiatingReply(Schema):
    member_id: UUID
    available: bool


def _viewer(request):
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")
    if not request.user.tenants.filter(pk=request.tenant.pk).exists():
        raise HttpError(403, "This account does not belong to this club.")
    member = Member.objects.filter(user=request.user).first()
    if member is None:
        raise HttpError(403, "This account has no member profile in this club.")
    return member


def _people(viewer):
    return {member.pk: member for member in (viewer, *audience.managed_children(viewer))}


def _event_display(event):
    return {
        "event_id": event.pk,
        "title": event.title,
        "kind": event.kind,
        "start": event.start,
        "end": event.end,
        "gathering": event.gathering,
        "deadline": event.deadline,
        "location": event.location.name if event.location else "",
        "opponent": event.opponent.name if event.opponent else "",
        "opponent_logo": event.opponent.logo.url if event.opponent and event.opponent.logo else None,
        "is_home": event.is_home_game,
        "score_for": event.score_for,
        "score_against": event.score_against,
        "cancelled": event.cancelled,
    }


def _attendance(row, blocked=None):
    code, detail = blocked if blocked else (None, None)
    return {**_event_display(row.event), "id": row.pk, "member_id": row.member_id, "status": row.status, "note": row.note, "blocked": code, "blocked_detail": detail}


def _blockers(rows, now):
    """``{attendance id: (code, detail)}`` for the rows a person cannot answer right now, so the app says why instead of offering buttons that fail.
    ``not_invited`` carries the reason: an open requirement that blocks this kind of event (``requirements``, with their names), an explicit
    exclusion (``excluded``) or no longer being in the event's team or group (``not_in_audience``). ``late_installment`` (an overdue registration
    instalment) still lets them decline. A fixed number of queries however many events there are."""
    open_rows = [row for row in rows if not row.event.cancelled and row.event.start > now]
    if not open_rows:
        return {}
    events = list({row.event_id: row.event for row in open_rows}.values())
    audience = attendance.audience_check(events, {row.member_id for row in open_rows})
    policy = RegistrationSettings.objects.first()
    today = timezone.localdate(now)
    overdue = {}  # (member, season) -> bool: the instalment schedule does not depend on the event, only the policy by kind does
    result = {}
    for row in open_rows:
        event, who = row.event, audience[row.event_id]
        if row.member_id not in who["invited"]:
            if who["requirements"].get(row.member_id):
                result[row.pk] = ("not_invited", {"reason": "requirements", "requirements": [str(name) for name in who["requirements"][row.member_id]]})
            elif row.member_id in who["excluded"]:
                result[row.pk] = ("not_invited", {"reason": "excluded", "requirements": []})
            else:
                result[row.pk] = ("not_invited", {"reason": "not_in_audience", "requirements": []})
        elif row.status not in (Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED) and policy is not None and event.kind in policy.installment_block_kinds:
            key = (row.member_id, event.season_id)
            if key not in overdue:
                overdue[key] = attendance.registration_payment_blocked(event, row.member, today=today)
            if overdue[key]:
                result[row.pk] = ("late_installment", None)
    return result


def _home_branding(request, today):
    season = Season.get_current(today)
    background = request.tenant.event_background
    return {"season": season.name if season else None, "event_background": background.url if background else None}


@router.get("/access")
def portal_access(request):
    _viewer(request)
    return {"can_manage": access.can_access_management(request.user), "shop_enabled": request.features.has("shop")}


@router.get("/home")
def portal_home(request):
    viewer = _viewer(request)
    people = _people(viewer)
    now = timezone.now()
    team_ids = TeamMembership.objects.filter(member_id__in=people, season__end_date__gte=timezone.localdate()).values("team_id")
    news = member_news().filter(Q(teams__isnull=True) | Q(teams__in=team_ids)).distinct().prefetch_related("teams", "photos")[:12]
    events = list(
        Attendance.objects.filter(member_id__in=people, event__start__range=(now - timedelta(days=90), now + timedelta(days=90)))
        .select_related("event__location", "event__opponent", "member")
        .order_by("event__start", "member_id", "id")
    )
    blockers = _blockers(events, now)
    fees = ClubMembership.objects.filter(member_id__in=people).select_related("season", "registration_details__batch").prefetch_related("payments")
    invoices = RegistrationBatch.objects.filter(contact=viewer, confirmed_at__isnull=False, cancelled_at__isnull=True, invoice_number__isnull=False)
    sends = list(
        FormSend.objects.filter(is_active=True)
        .filter(Q(closes_at__isnull=True) | Q(closes_at__gte=now))
        .select_related("form", "season")
    )
    recipients = audience.audience_ids_by_send(sends)
    return {
        "viewer": viewer.pk,
        **_home_branding(request, timezone.localdate(now)),
        "shop_enabled": request.features.has("shop"),
        "people": [{"id": member.pk, "name": str(member), "first_name": member.first_name, "photo_url": member.photo_url} for member in people.values()],
        "events": [_attendance(row, blockers.get(row.pk)) for row in events],
        "tasks": _open_tasks(viewer, people, events, now),
        "officiating": [
            {
                **_event_display(offer.event), "role": offer.kind, "member_id": offer.member_id,
                "status": offer.status, "signup_open": offer.signup_open,
            }
            for offer in officiating_invitations.opportunities(people, now=now)
        ] if request.features.has("referee_officials") else [],
        "news": [public_news_item(request, item) for item in news],
        "sponsors": [
            {"id": sponsor.pk, "name": sponsor.name, "url": sponsor.url or None, "logo_url": sponsor.logo.url if sponsor.logo else None}
            for sponsor in Sponsor.active()
        ],
        "fees": [
            {
                "member_id": fee.member_id, "season": fee.season.name, "paid": str(fee.amount_paid),
                "balance": str(memberships.remaining_balance(fee)), "next_installment": installments.membership_next(fee),
            }
            for fee in fees
        ],
        "invoices": [{"id": invoice.pk, "number": invoice.invoice_number, "due": invoice.invoice_due_date} for invoice in invoices],
        "forms": [
            {"id": send.pk, "title": send.form.title, "member_id": member_id, "opens_at": send.opens_at, "closes_at": send.closes_at}
            for send in sends
            for member_id in people.keys() & recipients[send.pk]
        ],
    }


@router.get("/invoices/{invoice_id}.pdf")
def portal_invoice(request, invoice_id: UUID):
    viewer = _viewer(request)
    batch = RegistrationBatch.objects.filter(pk=invoice_id, contact=viewer, confirmed_at__isnull=False, cancelled_at__isnull=True, invoice_number__isnull=False).first()
    if batch is None:
        raise HttpError(404, "Not found.")
    try:
        pdf = notifications.render_invoice_pdf(batch)
    except PDFUnavailable:
        raise HttpError(503, "PDF rendering is not available on this server.") from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Cache-Control"] = "private, no-store"
    return response


@router.post("/attendance/{attendance_id}")
def reply(request, attendance_id: UUID, payload: Reply):
    viewer = _viewer(request)
    row = Attendance.objects.select_related("event__location", "event__opponent", "member").filter(pk=attendance_id, member_id__in=_people(viewer)).first()
    if row is None:
        raise HttpError(404, "Not found.")
    row = attendance.set_answer_as_member(request.user, row, payload.status, payload.note)
    return _attendance(row)


@router.post("/officiating/{event_id}/{role}")
def reply_to_officiating(request, event_id: UUID, role: Literal["referee", "official"], payload: OfficiatingReply):
    viewer = _viewer(request)
    if not request.features.has("referee_officials"):
        raise HttpError(404, "Not found.")
    member = _people(viewer).get(payload.member_id)
    event = Event.objects.filter(pk=event_id).select_related("location").first()
    if member is None or event is None:
        raise HttpError(404, "Not found.")
    return {"status": officiating_invitations.respond(request.user, event, referees.kind_of(role), member, available=payload.available)}


def _form_for(request, send_id, member_id):
    viewer = _viewer(request)
    member = _people(viewer).get(member_id)
    send = FormSend.objects.select_related("form", "season").filter(pk=send_id, is_active=True).first()
    if member is None or send is None or not audience.addressed_to(send, member):
        raise HttpError(404, "Not found.")
    return viewer, member, send


@router.get("/forms/{send_id}/{member_id}")
def portal_form(request, send_id: UUID, member_id: UUID):
    _, member, send = _form_for(request, send_id, member_id)
    return {
        "title": send.form.title,
        "description": send.form.description,
        "member": str(member),
        "state": {"status": "not_open_yet" if audience.send_status(send) == "scheduled" else audience.send_status(send), "opens_at": send.opens_at, "closes_at": send.closes_at},
        "fields": [form_field(field) for field in send.form.fields.filter(is_active=True).order_by("order")],
        "max_file_mb": MAX_ATTACHMENT_BYTES // (1024 * 1024),
        "allowed_file_extensions": sorted(ALLOWED_EXTENSIONS),
        "submitted": send.submissions.filter(member=member).count(),
        "limit": send.max_submissions_per_user,
    }


@router.post("/forms/{send_id}/{member_id}", response={201: dict})
def portal_submit_form(request, send_id: UUID, member_id: UUID):
    viewer, member, send = _form_for(request, send_id, member_id)
    submit_form(send, member, request.POST, files=request.FILES, submitted_by=viewer)
    return 201, {"submitted": True}


def _task_out(task, me_id):
    claims = list(task.claims.all())
    return {
        "id": task.pk, "title": task.title, "description": task.description, "needed": task.needed_quantity, "claimed": len(claims),
        "open_slots": max(task.needed_quantity - len(claims), 0), "claimed_by_me": any(claim.member_id == me_id for claim in claims),
        "claimed_by": [task_svc.claim_label_for(claim.member) for claim in claims],
    }


def _open_tasks(viewer, people, rows, now):
    """Tasks of the upcoming events the family is invited to that still need someone and that this person has not taken: shown on Home so they can be claimed."""
    events = {row.event_id: row.event for row in rows if row.event.start > now and not row.event.cancelled and row.event.start < now + timedelta(days=60)}
    if not events:
        return []
    tasks = EventTask.objects.filter(event_id__in=events).prefetch_related("claims").order_by("event__start", "created")
    out = []
    for task in tasks:
        claims = list(task.claims.all())
        if len(claims) >= task.needed_quantity or any(claim.member_id == viewer.pk for claim in claims):
            continue
        event = events[task.event_id]
        out.append({"id": task.pk, "event_id": event.pk, "event_title": event.title, "start": event.start, "title": task.title, "open_slots": task.needed_quantity - len(claims)})
    return out


def _visible_event(request, event_id):
    """The event if this person or one of their children has an answer or an officiating invitation on it, plus whether they are in its audience."""
    viewer = _viewer(request)
    event = Event.objects.select_related("location", "opponent").filter(pk=event_id).first()
    if event is None:
        raise HttpError(404, "Not found.")
    people = _people(viewer)
    in_audience = Attendance.objects.filter(event=event, member_id__in=people).exists()
    if not in_audience and not any(offer.event_id == event.pk for offer in officiating_invitations.opportunities(people, now=timezone.now())):
        raise HttpError(404, "Not found.")
    return viewer, people, event, in_audience


@router.get("/events/{event_id}", summary="What the family can see of one event: the published line-up, the tasks and the attachments")
def event_extras(request, event_id: UUID):
    viewer, people, event, in_audience = _visible_event(request, event_id)
    if not in_audience:
        return {"lineup": None, "tasks": [], "attachments": []}
    lineup = Lineup.objects.filter(event=event, published_at__isnull=False).select_related("team").first()
    lineup_out = None
    if lineup is not None:
        selected = list(LineupSelection.objects.filter(lineup=lineup).select_related("member"))
        numbers = {row.member_id: row.jersey_number for row in TeamMembership.objects.filter(team=lineup.team, season=attendance.resolve_season(event), member_id__in=[sel.member_id for sel in selected])}
        counts = {"going": 0, "out": 0, "waiting": 0}
        for status in Attendance.objects.filter(event=event).values_list("status", flat=True):
            key = "going" if status in ("present", "selected") else "out" if status in ("absent", "excused", "not_selected") else "waiting"
            counts[key] += 1
        lineup_out = {
            "players": sorted(
                ({"id": sel.member_id, "name": sel.member.last_name or sel.member.first_name, "number": numbers.get(sel.member_id), "mine": sel.member_id in people} for sel in selected),
                key=lambda player: (player["number"] is None, player["number"] or 0, player["name"]),
            ),
            **counts,
        }
    tasks = [_task_out(task, viewer.pk) for task in task_svc.list_tasks(request.user, event)]
    files = [{"id": a.pk, "name": a.name, "size": a.size, "download_url": f"/api/v1/portal/events/{event.pk}/attachments/{a.pk}/download"} for a in attachment_svc.list_attachments(request.user, event)]
    return {"lineup": lineup_out, "tasks": tasks, "attachments": files}


def _task_of(request, event_id, task_id):
    viewer, people, event, in_audience = _visible_event(request, event_id)
    task = EventTask.objects.filter(pk=task_id, event=event).first()
    if task is None or not in_audience:
        raise HttpError(404, "Not found.")
    return viewer, task


@router.post("/events/{event_id}/tasks/{task_id}/claim", summary="Take a slot of a task")
def claim_task(request, event_id: UUID, task_id: UUID):
    viewer, task = _task_of(request, event_id, task_id)
    try:
        task_svc.claim_task(request.user, task, viewer)
    except Invalid as error:
        raise HttpError(422, str(error)) from error
    task = EventTask.objects.prefetch_related("claims__member").get(pk=task.pk)
    return _task_out(task, viewer.pk)


@router.delete("/events/{event_id}/tasks/{task_id}/claim", summary="Give a slot back")
def unclaim_task(request, event_id: UUID, task_id: UUID):
    viewer, task = _task_of(request, event_id, task_id)
    task_svc.unclaim_member(request.user, task, viewer)
    task = EventTask.objects.prefetch_related("claims__member").get(pk=task.pk)
    return _task_out(task, viewer.pk)


@router.get("/events/{event_id}/attachments/{attachment_id}/download", summary="Download a file of an event")
def download_attachment(request, event_id: UUID, attachment_id: UUID):
    _viewer_, _people_, event, in_audience = _visible_event(request, event_id)
    attachment = EventAttachment.objects.filter(pk=attachment_id, event=event).select_related("event").first()
    if attachment is None or not in_audience:
        raise HttpError(404, "Not found.")
    try:
        handle = attachment_svc.open_attachment(attachment, actor=request.user)
    except FileNotFoundError:
        raise HttpError(404, "The file is gone.") from None
    response = FileResponse(handle, as_attachment=True, filename=attachment.name, content_type=attachment_svc.DOWNLOAD_CONTENT_TYPE)
    for header, value in attachment_svc.DOWNLOAD_HEADERS.items():
        response[header] = value
    return response


@router.get("/coach")
def coach_home(request):
    _viewer(request)
    if not access.can_access_management(request.user):
        raise HttpError(403, "Management access is required.")
    team_ids = access.staffed_team_ids(request.user)
    teams = Team.objects.filter(pk__in=team_ids)
    if access.can_manage_settings(request.user):
        teams = Team.objects.all()
    now = timezone.localtime()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = (
        Event.objects.filter(teams__in=teams, start__range=(start, start + timedelta(days=30)))
        .select_related("location", "opponent", "lineup")
        .prefetch_related("teams")
        .annotate(
            going=Count("attendances", filter=Q(attendances__status__in=["present", "selected"]), distinct=True),
            out=Count("attendances", filter=Q(attendances__status__in=["absent", "excused", "dropout"]), distinct=True),
            unanswered=Count("attendances", filter=Q(attendances__status="no_response"), distinct=True),
        )
        .order_by("start")
        .distinct()
    )
    news = member_news().filter(Q(teams__isnull=True) | Q(teams__in=teams)).distinct().prefetch_related("teams", "photos")[:12]
    return {
        **_home_branding(request, now.date()),
        "teams": [{"id": team.pk, "name": team.name} for team in teams],
        "news": [public_news_item(request, item) for item in news],
        "events": [
            {
                "id": event.pk, "title": event.title, "start": event.start, "location": event.location.name if event.location else "",
                "kind": event.kind, "deadline": event.deadline, "team_ids": [team.pk for team in event.teams.all()],
                "opponent_logo": event.opponent.logo.url if event.opponent and event.opponent.logo else None, "is_home": event.is_home_game,
                "going": event.going, "out": event.out, "unanswered": event.unanswered,
                "cancelled": event.cancelled, "lineup": event.kind in (Event.EventKind.GAME, Event.EventKind.TOURNAMENT),
                "lineup_published": getattr(getattr(event, "lineup", None), "published_at", None) is not None,
                "can_manage_attendance": access.can_manage_attendance(request.user, event),
            }
            for event in events
        ],
    }
