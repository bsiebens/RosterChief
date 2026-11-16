"""Club app endpoints scoped to the signed-in member and their children."""

from datetime import timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django_tenants.utils import get_public_schema_name
from ninja import Field, Router, Schema
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from api.routers.public_forms import _field as form_field
from api.routers.public_news import public_news_item
from events.models import Attendance, Event, EventAttachment, EventTask, GameTimeline, Lineup, LineupSelection, Season
from events.services import attachments as attachment_svc
from events.services import attendance, follows, officiating_invitations, referees
from events.services import tasks as task_svc
from events.services.attachments import ALLOWED_EXTENSIONS, MAX_ATTACHMENT_BYTES
from formbuilder.models import FormSend, Submission
from formbuilder.services import audience
from formbuilder.services.submission import submit_form
from members.models import ClubMembership, Member
from members.services import access, invoice_access, memberships
from members.services.errors import Invalid
from news.services import member_news
from registration.models import RegistrationSettings
from registration.services import installments, notifications
from shop.models import Order, Payment
from shop.services import charges as charge_svc
from shop.services import invoices as invoice_svc
from shop.services.invoices import PDFUnavailable
from shop.services.orders import is_shop_open
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


def _charges(people):
    """Sent, not cancelled charge invoices (a fine, a late fee) written for the viewer or a child they manage, newest first, with what was paid."""
    paid = Coalesce(Sum("payments__amount", filter=Q(payments__status=Payment.PaymentStatus.CONFIRMED)), Value(Decimal("0")), output_field=DecimalField())
    return (
        Order.objects.filter(kind=Order.Kind.CHARGE, purchaser_id__in=people, invoice__isnull=False)
        .exclude(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
        .select_related("invoice").prefetch_related("lines").annotate(paid=paid).order_by("-invoice__issued_at")
    )  # fmt: skip


def _charge(order, today):
    lines = list(order.lines.all())
    summary = lines[0].description if lines else ""
    if len(lines) > 1:
        summary = f"{summary} +{len(lines) - 1}"
    invoice = order.invoice
    return {
        "id": order.pk, "number": invoice.number, "member_id": order.purchaser_id, "summary": summary, "total": str(order.total), "paid": str(order.paid),
        "due": str(max(Decimal("0"), order.total - order.paid)), "due_date": invoice.due_date, "status": charge_svc.status_for(order, invoice, today),
    }  # fmt: skip


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
        "notes": event.notes,
    }


def _attendance(row, blocked=None):
    code, detail = blocked if blocked else (None, None)
    return {**_event_display(row.event), "id": row.pk, "member_id": row.member_id, "status": row.status, "note": row.note, "blocked": code, "blocked_detail": detail}


def _requirement_rows(people, rows, now):
    """Answers that do not exist because an open requirement keeps someone out of an event they belong to.

    The invite sync leaves such people without an attendance row, so the app would never mention the event for them. Here they get a row
    anyway (``not_invited`` with the names of the requirements), for each coming event of the next 90 days whose audience they are in,
    so every card of an event lists the whole family and the person sees what to fix. A fixed number of queries."""
    have = {(row.event_id, row.member_id) for row in rows}
    events = list(Event.objects.filter(start__gt=now, start__lt=now + timedelta(days=90), cancelled=False).select_related("location", "opponent"))
    audience = attendance.audience_check(events, set(people))
    out = []
    for event in events:
        for member_id, names in audience.get(event.pk, {}).get("requirements", {}).items():
            if (event.pk, member_id) in have or not names:
                continue
            out.append({**_event_display(event), "id": f"requirement-{event.pk}-{member_id}", "member_id": member_id, "status": "no_response", "note": "",
                        "blocked": "not_invited", "blocked_detail": {"reason": "requirements", "requirements": [str(name) for name in names]}})
    return out


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


def _shop_visible(request) -> bool:
    """The member app shows the shop only while the club has it and has it open; a closed shop disappears from the navigation."""
    return request.features.has("shop") and is_shop_open()


@router.get("/access")
def portal_access(request):
    _viewer(request)
    return {"can_manage": access.can_access_management(request.user), "shop_enabled": _shop_visible(request)}


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
    invoices = invoice_access.downloadable_invoices(viewer)
    sends = list(
        FormSend.objects.filter(is_active=True)
        .filter(Q(closes_at__isnull=True) | Q(closes_at__gte=now))
        .select_related("form", "season")
    )
    recipients = audience.audience_ids_by_send(sends)
    return {
        "viewer": viewer.pk,
        **_home_branding(request, timezone.localdate(now)),
        "shop_enabled": _shop_visible(request),
        "people": [{"id": member.pk, "name": str(member), "first_name": member.first_name, "photo_url": member.photo_url} for member in people.values()],
        "events": sorted(
            [_attendance(row, blockers.get(row.pk)) for row in events] + _requirement_rows(people, events, now),
            key=lambda row: (row["start"], str(row["member_id"])),
        ),
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
        "charges": [_charge(order, timezone.localdate()) for order in _charges(people)[:20]],
        "payment_instructions": request.tenant.payment_instructions or "",
        "payments": sorted(
            ({"id": payment.pk, "member_id": fee.member_id, "season": fee.season.name, "amount": str(payment.amount), "method": payment.method, "paid_at": payment.paid_at} for fee in fees for payment in fee.payments.all()),
            key=lambda payment: payment["paid_at"], reverse=True,
        )[:20],
        "submitted_forms": [
            {"id": sub.pk, "title": sub.send.form.title, "member_id": sub.member_id, "submitted_at": sub.submitted_at}
            for sub in Submission.objects.filter(member_id__in=people).select_related("send__form").order_by("-submitted_at")[:20]
        ],
        "forms": [
            {"id": send.pk, "title": send.form.title, "member_id": member_id, "opens_at": send.opens_at, "closes_at": send.closes_at}
            for send in sends
            for member_id in people.keys() & recipients[send.pk]
        ],
    }


@router.get("/evaluations", summary="Evaluations the club has shared with this family (never anything that was not released)")
def shared_evaluations(request):
    """Only released evaluations. ``notes`` shows the public note alone; ``full`` adds every score and answer."""
    viewer = _viewer(request)
    if not request.features.has("evaluations"):
        return []
    from evaluations.models import PlayerEvaluation
    from evaluations.services.checklists import evaluations_queryset

    rows = evaluations_queryset().filter(player_id__in=_people(viewer)).exclude(release="").order_by("-released_at")
    return [
        {
            "id": e.pk, "member_id": e.player_id, "checklist": e.checklist.name, "season": e.season.name, "released_at": e.released_at,
            "release": e.release, "public_note": e.public_note,
            "answers": [
                {"label": a.field.label, "field_type": a.field.field_type, "section": a.field.section, "options": a.field.options, "value": a.value}
                for a in sorted(e.submission.answers.all(), key=lambda a: a.field.order) if a.field.visible_to_family
            ] if e.release == PlayerEvaluation.Release.FULL else [],
        }
        for e in rows
    ]


@router.get("/forms/submissions/{submission_id}", summary="The answers of a form this family filled in")
def submitted_form(request, submission_id: UUID):
    viewer = _viewer(request)
    submission = Submission.objects.filter(pk=submission_id, member_id__in=_people(viewer)).select_related("send__form").first()
    if submission is None:
        raise HttpError(404, "Not found.")
    answers = submission.answers.select_related("field").order_by("field__order", "created")
    return {
        "title": submission.send.form.title, "member_id": submission.member_id, "submitted_at": submission.submitted_at,
        "answers": [
            {"label": answer.field.label, "value": (answer.value or {}).get("name") if answer.field.field_type == "file" and isinstance(answer.value, dict) else answer.value}
            for answer in answers
        ],
    }


@router.get("/invoices/{invoice_id}.pdf")
def portal_invoice(request, invoice_id: UUID):
    viewer = _viewer(request)
    batch = invoice_access.downloadable_invoices(viewer).filter(pk=invoice_id).first()
    if batch is None:
        raise HttpError(404, "Not found.")
    try:
        pdf = notifications.render_invoice_pdf(batch)
    except PDFUnavailable:
        raise HttpError(503, "PDF rendering is not available on this server.") from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Cache-Control"] = "private, no-store"
    return response


@router.get("/charges/{order_id}.pdf", summary="The PDF of a charge invoice written for this person or a child they manage")
def portal_charge_invoice(request, order_id: UUID):
    viewer = _viewer(request)
    order = _charges(_people(viewer)).filter(pk=order_id).first()
    if order is None:
        raise HttpError(404, "Not found.")
    try:
        pdf = invoice_svc.render_pdf(order.invoice)
    except PDFUnavailable:
        raise HttpError(503, "PDF rendering is not available on this server.") from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{order.invoice.number}.pdf"'
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
        return {"lineup": None, "game": None, "roster": None, "totals": None, "tasks": [], "attachments": [], "follow": follows.follow_state(request.user, event) if event.kind == Event.EventKind.GAME else None}
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
    game = _game_sheet(event)
    roster = None if event.club_wide else _roster(event, people)
    totals = _attendance_totals(event) if event.club_wide else None  # a whole-club event has too many people to list: families see only the totals
    tasks = [_task_out(task, viewer.pk) for task in task_svc.list_tasks(request.user, event)]
    files = [{"id": a.pk, "name": a.name, "size": a.size, "download_url": f"/api/v1/portal/events/{event.pk}/attachments/{a.pk}/download"} for a in attachment_svc.list_attachments(request.user, event)]
    return {"lineup": lineup_out, "game": game, "roster": roster, "totals": totals, "tasks": tasks, "attachments": files, "follow": follows.follow_state(request.user, event) if event.kind == Event.EventKind.GAME else None}


_ROSTER_STATES = {"present": "going", "selected": "going", "absent": "out", "excused": "out", "not_selected": "out", "maybe": "maybe", "no_response": "waiting"}


def _attendance_totals(event) -> dict:
    """How many invited people are going / maybe / waiting / out, and no names: for events too big to list (the whole club)."""
    totals = {"going": 0, "maybe": 0, "waiting": 0, "out": 0}
    for status, before in Attendance.objects.filter(event=event).values_list("status", "status_before_lineup"):
        answer = before if status in ("selected", "not_selected") and before else status
        totals[_ROSTER_STATES.get(answer, "waiting")] += 1
    return {**totals, "total": sum(totals.values())}


def _roster(event, people):
    """Everyone invited to an event and whether they are coming, for the families: a name, a shirt number, a position and one of going / out /
    maybe / waiting. Never the reason or the coach's note: those are for the coaching app. A line-up that was published over an answer shows the
    answer it replaced, so this stays "who said they can come" rather than "who was picked".

    Players of the event's teams carry their number and position. An extra invitee (from another team, a group, or invited by name) gets the
    position they play on their own team that season, marked ``extra`` with that team's name, and no number (it is another team's). Ordered by the
    club's position order, then coming before not coming, then shirt number; people without any position come last, regulars before extras."""
    rows = list(Attendance.objects.filter(event=event).select_related("member"))
    own, other = {}, {}
    if rows:
        season = attendance.resolve_season(event)
        team_ids = set(event.teams.values_list("pk", flat=True))
        for membership in TeamMembership.objects.filter(season=season, member_id__in=[row.member_id for row in rows]).select_related("position", "team").order_by("team__name"):
            if membership.team_id in team_ids:
                own[membership.member_id] = membership
            elif membership.position_id and membership.position and not membership.position.staff_position:
                other.setdefault(membership.member_id, membership)
    states = _ROSTER_STATES
    out = []
    for row in rows:
        answer = row.status_before_lineup if row.status in ("selected", "not_selected") and row.status_before_lineup else row.status
        regular = own.get(row.member_id)
        guest = None if regular else other.get(row.member_id)
        place = regular or guest
        position = place.position if place else None
        out.append(
            {
                "id": row.member_id,
                "name": f"{row.member.first_name} {row.member.last_name}".strip(),
                "number": regular.jersey_number if regular else None,
                "position": position.name if position else None,
                "position_order": position.ordering if position else None,
                "extra": regular is None,
                "team": guest.team.name if guest else None,
                "state": states.get(answer, "waiting"),
                "mine": row.member_id in people,
            }
        )
    order = {"going": 0, "maybe": 1, "waiting": 2, "out": 3}
    return sorted(out, key=lambda p: (p["position_order"] is None, p["position_order"] or 0, p["position"] or "", p["extra"], order[p["state"]], p["number"] is None, p["number"] or 0, p["name"].lower()))


def _game_sheet(event):
    """The game as the competition's page captured it, for a game that has been played: the score, who dressed (ours first, with goals, assists
    and penalty minutes) and the timeline of goals, penalties and goalie changes by period. None when there is no game sheet."""
    import json

    from api.routers.events import _game_lineup
    from events.services import game_timeline

    timeline = GameTimeline.objects.filter(event=event).first()
    if timeline is None or not timeline.data:
        return None
    out = game_timeline.display_timeline(timeline, event)
    out["lineup"] = _game_lineup(event, timeline, out)
    out["final"] = timeline.finalized_at is not None
    out["updated_at"] = timeline.changed_at
    return json.loads(json.dumps(out, default=str))  # lazy translations -> plain strings


def _game_card(event, via, now):
    """A followed game for the small cards at the top of Home: who plays, where, the score so far and whether it is live."""
    scored = event.score_for is not None and event.score_against is not None
    return {
        **_event_display(event), "status": "live" if event.is_live else "final" if scored and event.start <= now else "scheduled",
        "teams": ", ".join(team.name for team in event.teams.all()), "via": via,
    }


@router.get("/followed", summary="The games this person follows that are live, coming up or just finished")
def followed_games(request):
    _viewer(request)
    now = timezone.now()
    return [_game_card(row["event"], row["via"], now) for row in follows.followed_games(request.user, now=now)]


def _game_visible(request, event):
    """Whoever follows the game (directly or through a team) or is invited to it may read its game sheet."""
    state = follows.follow_state(request.user, event)
    if state["directly"] or state["via_team"]:
        return
    _visible_event(request, event.pk)


@router.get("/events/{event_id}/game", summary="The captured game sheet of a game the person follows or plays")
def event_game(request, event_id: UUID):
    _viewer(request)
    event = Event.objects.select_related("location", "opponent").filter(pk=event_id, kind=Event.EventKind.GAME).first()
    if event is None:
        raise HttpError(404, "Not found.")
    _game_visible(request, event)
    return {"game": _game_sheet(event), "status": "live" if event.is_live else "scheduled"}


@router.put("/events/{event_id}/follow", summary="Follow this game: it appears on top of Home and sends live pushes")
def follow_event(request, event_id: UUID):
    _viewer, _people, event, _in_audience = _visible_event(request, event_id)
    try:
        follows.follow_event(request.user, event)
    except Invalid as error:
        raise HttpError(422, str(error)) from error
    return follows.follow_state(request.user, event)


@router.delete("/events/{event_id}/follow", summary="Stop following this game")
def unfollow_event(request, event_id: UUID):
    _viewer, _people, event, _in_audience = _visible_event(request, event_id)
    follows.unfollow_event(request.user, event)
    return follows.follow_state(request.user, event)


def _task_of(request, event_id, task_id):
    viewer, _people, event, in_audience = _visible_event(request, event_id)
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
    _viewer, _people, event, in_audience = _visible_event(request, event_id)
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
    teams = Team.objects.filter(pk__in=team_ids)  # the coach app is about the teams this person is staff of, administrators included
    now = timezone.localtime()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = (
        Event.objects.filter(teams__in=teams, start__range=(start - timedelta(days=14), start + timedelta(days=45)))
        .select_related("location", "opponent", "lineup", "timeline")
        .prefetch_related("teams")
        .annotate(
            going=Count("attendances", filter=Q(attendances__status__in=["present", "selected"]), distinct=True),
            out=Count("attendances", filter=Q(attendances__status__in=["absent", "excused", "dropout"]), distinct=True),
            unanswered=Count("attendances", filter=Q(attendances__status="no_response"), distinct=True),
            maybe=Count("attendances", filter=Q(attendances__status="maybe"), distinct=True),
            invited=Count("attendances", distinct=True),
        )
        .order_by("start")
        .distinct()
    )
    news = member_news().filter(Q(teams__isnull=True) | Q(teams__in=teams)).distinct().prefetch_related("teams", "photos")[:12]
    return {
        **_home_branding(request, now.date()),
        "teams": [{"id": team.pk, "name": team.name} for team in teams],
        "can_create_events": access.can_manage_any_team(request.user),
        "can_add_news": access.can_add_news(request.user),
        "news": [public_news_item(request, item) for item in news],
        "events": [
            {
                "id": event.pk, "title": event.title, "start": event.start, "location": event.location.name if event.location else "",
                "kind": event.kind, "deadline": event.deadline, "team_ids": [team.pk for team in event.teams.all()],
                "opponent_logo": event.opponent.logo.url if event.opponent and event.opponent.logo else None, "is_home": event.is_home_game,
                "end": event.end, "gathering": event.gathering, "opponent": event.opponent.name if event.opponent else "", "notes": event.notes,
                "going": event.going, "out": event.out, "unanswered": event.unanswered, "maybe": event.maybe, "invited": event.invited,
                "has_game_sheet": hasattr(event, "timeline"),
                "cancelled": event.cancelled, "lineup": event.kind in (Event.EventKind.GAME, Event.EventKind.TOURNAMENT),
                "lineup_published": getattr(getattr(event, "lineup", None), "published_at", None) is not None,
                "can_manage_attendance": access.can_manage_attendance(request.user, event),
            }
            for event in events
        ],
    }
