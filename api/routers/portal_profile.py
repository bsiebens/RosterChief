"""The member's own profile and notification settings: the signed-in person and the children they manage."""

import json
from datetime import date, time
from uuid import UUID

from django.db.models import Q
from django.utils import timezone
from ninja import File, Router, Schema, UploadedFile
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from api.routers.portal import _people, _viewer
from events.models import Event
from formbuilder.models import FormSend
from formbuilder.services import audience
from members.models import ClubMembership, Member
from members.services import memberships as membership_services, onboarding
from shop.models import Voucher
from members.services import members as member_services
from members.services import stats_sharing
from members.services.errors import Invalid
from api.routers.analytics import member_document
from api.schemas_member_stats import PublicPlayerStatsOut
from events.models import EventFollow, Season
from events.services import game_stats, member_stats
from events.services import follows
from notifications import preferences
from notifications.models import Category, Channel
from teams.models import Team, TeamMembership

router = Router(auth=[TokenAuth(door=False), CookieAuth(door=False)])

# Categories a member can switch (the rest -- coach and admin -- belong to staff and are not shown here).
MEMBER_CATEGORIES = [Category.NEWS, Category.EVENT, Category.REMINDER, Category.LINEUP, Category.CREW, Category.FORM, Category.SHOP, Category.ANNOUNCEMENT, Category.GAME, Category.GENERAL]


class ProfileIn(Schema):
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    phone: str | None = None
    emergency_phone: str | None = None
    photo_public_consent: bool | None = None


def _person(viewer, member):
    teams = TeamMembership.objects.filter(member=member, season__end_date__gte=timezone.localdate()).select_related("team", "season").order_by("team__name")
    return {
        "id": member.pk, "is_self": member.pk == viewer.pk, "name": str(member), "first_name": member.first_name, "last_name": member.last_name,
        "email": member.email or "", "date_of_birth": member.date_of_birth, "phone": str(member.phone) if member.phone else "",
        "emergency_phone": str(member.emergency_phone) if member.emergency_phone else "", "photo_consent": member.photo_public_consent, "photo_url": member.photo_url,
        "teams": [{"id": row.team_id, "name": row.team.name, "season": row.season.name, "jersey_number": row.jersey_number, "captain": row.is_captain or row.is_alternate_captain} for row in teams],
    }


def _own(request, member_id: UUID):
    viewer = _viewer(request)
    member = _people(viewer).get(member_id)
    if member is None:
        raise HttpError(404, "Not found.")
    return viewer, member


def _invalid(error: Invalid):
    raise HttpError(422, str(error))


@router.get("/profile", summary="Me and the people I manage, with the details I may edit")
def profile(request):
    viewer = _viewer(request)
    return {"people": [_person(viewer, member) for member in _people(viewer).values()]}


@router.patch("/profile/{member_id}", summary="Correct my own (or my child's) contact details")
def update_profile(request, member_id: UUID, payload: ProfileIn):
    viewer, member = _own(request, member_id)
    values = {key: value for key, value in payload.dict(exclude_unset=True).items()}
    for key in ("phone", "emergency_phone"):
        if key in values:
            values[key] = values[key].strip() or None
    try:
        member_services.update_own_profile(request.user, member, **values)
    except Invalid as error:
        raise HttpError(422, str(error)) from error
    return _person(viewer, member)


@router.post("/profile/{member_id}/photo", summary="Set the profile picture")
def set_photo(request, member_id: UUID, photo: File[UploadedFile]):
    viewer, member = _own(request, member_id)
    try:
        member_services.set_photo(request.user, member, photo, authorized=True)
    except Invalid as error:
        raise HttpError(422, str(error)) from error
    return _person(viewer, member)


@router.delete("/profile/{member_id}/photo", summary="Remove the profile picture")
def remove_photo(request, member_id: UUID):
    viewer, member = _own(request, member_id)
    member_services.remove_photo(request.user, member, authorized=True)
    return _person(viewer, member)


class SettingsIn(Schema):
    master: dict[str, bool] = {}
    categories: dict[str, dict[str, bool]] = {}
    email_mode: str | None = None
    quiet_hours: dict | None = None  # {"enabled", "start": "22:00", "end": "07:00", "timezone"}; null leaves it, {"enabled": false} clears it


def _settings(request):
    user = request.user
    club = request.tenant
    quiet = getattr(user, "quiet_hours", None) if hasattr(user, "quiet_hours") else None
    return {
        "master": {channel: preferences.master_enabled(user, channel, club) for channel in Channel.values},
        "categories": [{"key": category.value, "label": str(category.label), **{channel: preferences.is_enabled(user, category.value, channel, club) for channel in Channel.values}} for category in MEMBER_CATEGORIES],
        "email_mode": preferences.get_email_mode(user),
        "quiet_hours": {"enabled": quiet.enabled, "start": quiet.start.strftime("%H:%M"), "end": quiet.end.strftime("%H:%M"), "timezone": quiet.timezone} if quiet else None,
    }


@router.get("/notifications", summary="My notification switches in this club")
def notification_settings(request):
    _viewer(request)
    return _settings(request)


@router.put("/notifications", summary="Change my notification switches in this club")
def update_notification_settings(request, payload: SettingsIn):
    _viewer(request)
    user, club = request.user, request.tenant
    try:
        for channel, enabled in payload.master.items():
            preferences.set_master_switch(user, channel, enabled, club)
        for category, channels in payload.categories.items():
            if category not in {c.value for c in MEMBER_CATEGORIES}:
                raise ValueError(f"unknown category: {category}")
            for channel, enabled in channels.items():
                preferences.set_preference(user, category, channel, enabled, club)
        if payload.email_mode is not None:
            preferences.set_email_mode(user, payload.email_mode)
        if payload.quiet_hours is not None:
            quiet = payload.quiet_hours
            if quiet.get("enabled", True) is False and not quiet.get("start"):
                preferences.clear_quiet_hours(user)
            else:
                preferences.set_quiet_hours(user, time.fromisoformat(quiet["start"]), time.fromisoformat(quiet["end"]), quiet.get("timezone") or "Europe/Brussels", bool(quiet.get("enabled", True)))
    except (ValueError, KeyError) as error:
        raise HttpError(422, str(error)) from error
    user.refresh_from_db()
    return _settings(request)


def _follows(request, people):
    """The club's teams with whether this account follows them (live game pushes and the cards on Home), the family's own teams first,
    plus the single games followed directly that are still to come."""
    mine = set(TeamMembership.objects.filter(member_id__in=people, season__end_date__gte=timezone.localdate()).values_list("team_id", flat=True))
    followed = follows.followed_team_ids(request.user)
    teams = Team.objects.filter(tournament_only=False).order_by("ordering", "name")
    games = EventFollow.objects.filter(user=request.user, event__start__gte=timezone.now()).select_related("event").order_by("event__start")
    return {
        "teams": sorted(({"id": team.pk, "name": team.name, "mine": team.pk in mine, "followed": team.pk in followed} for team in teams), key=lambda team: not team["mine"]),
        "games": [{"event_id": row.event_id, "title": row.event.title, "start": row.event.start} for row in games],
    }


@router.get("/follows", summary="Which teams and games I follow for live updates")
def follows_state(request):
    viewer = _viewer(request)
    return _follows(request, _people(viewer))


@router.put("/follows/teams/{team_id}", summary="Follow a team's games")
def follow_team(request, team_id: UUID):
    viewer = _viewer(request)
    team = Team.objects.filter(pk=team_id).first()
    if team is None:
        raise HttpError(404, "Not found.")
    follows.follow_team(request.user, team)
    return _follows(request, _people(viewer))


@router.delete("/follows/teams/{team_id}", summary="Stop following a team's games")
def unfollow_team(request, team_id: UUID):
    viewer = _viewer(request)
    team = Team.objects.filter(pk=team_id).first()
    if team is not None:
        follows.unfollow_team(request.user, team)
    return _follows(request, _people(viewer))


@router.delete("/follows/games/{event_id}", summary="Stop following one game")
def unfollow_game(request, event_id: UUID):
    viewer = _viewer(request)
    event = Event.objects.filter(pk=event_id).first()
    if event is not None:
        follows.unfollow_event(request.user, event)
    return _follows(request, _people(viewer))


def _requirement_item(item):
    requirement, status = item.requirement, item.status
    return {
        "id": requirement.pk, "name": requirement.name, "description": requirement.description, "kind": requirement.kind,
        "question_type": requirement.question_type, "options": requirement.options, "requires_document": requirement.requires_document,
        "valid_for_months": requirement.valid_for_months,
        "state": item.display, "valid_until": item.valid_until, "answer": item.answer,
        "has_document": bool(status and status.document), "completed_at": status.completed_at if status else None,
        "submitted_at": status.submitted_at if status else None, "locked": item.display in ("complete", "expiring_soon", "bypassed"),
    }


def _membership_rows(people):
    rows = ClubMembership.objects.filter(member_id__in=people, season__end_date__gte=timezone.localdate()).select_related("season", "registration_details__batch").prefetch_related("requirement_statuses", "payments").order_by("season__start_date")
    out = []
    for row in rows:
        batch = getattr(getattr(row, "registration_details", None), "batch", None)
        invoice = batch if batch is not None and batch.invoice_number and batch.confirmed_at and not batch.cancelled_at else None
        out.append({
            "member_id": row.member_id, "season": row.season.name, "status": row.status, "fee_status": row.fee_status,
            "fee": str(row.fee_amount), "paid": str(row.amount_paid), "balance": str(membership_services.remaining_balance(row)),
            "invoice_id": invoice.pk if invoice and invoice.contact_id and invoice.contact_id in people else None, "invoice_number": invoice.invoice_number if invoice else None,
            "requirements": [_requirement_item(item) for item in onboarding.checklist_for(row)],
        })
    return out


@router.get("/overview", summary="Membership, requirements and vouchers of me and the people I manage")
def overview(request):
    viewer = _viewer(request)
    people = _people(viewer)
    vouchers = Voucher.objects.filter(issued_to_id__in=people, is_active=True).order_by("expiry_date")
    now = timezone.now()
    sends = list(FormSend.objects.filter(is_active=True).filter(Q(closes_at__isnull=True) | Q(closes_at__gte=now)).select_related("form"))
    recipients = audience.audience_ids_by_send(sends)
    return {
        "memberships": _membership_rows(people),
        "forms": [
            {"id": send.pk, "title": send.form.title, "member_id": member_id, "closes_at": send.closes_at}
            for send in sends
            for member_id in people.keys() & recipients[send.pk]
        ],
        "vouchers": [
            {"id": v.pk, "number": v.number, "member_id": v.issued_to_id, "amount": str(v.amount), "available": str(v.available_amount), "expiry_date": v.expiry_date, "expired": v.is_expired}
            for v in vouchers
        ],
    }


def _current_membership(member):
    return ClubMembership.objects.filter(member=member, season__end_date__gte=timezone.localdate()).order_by("season__start_date").first()


@router.post("/requirements/{member_id}/{requirement_id}", summary="Hand in a document or answer for an onboarding requirement; it waits for the club to approve it")
def hand_in(request, member_id: UUID, requirement_id: UUID, answer: str | None = None, valid_until: date | None = None, document: UploadedFile = File(None)):
    viewer = _viewer(request)
    member = _people(viewer).get(member_id)
    requirement = onboarding.open_requirements().filter(pk=requirement_id).first()
    membership = _current_membership(member) if member else None
    if member is None or requirement is None or membership is None:
        raise HttpError(404, "Not found.")
    try:
        if requirement.kind == onboarding.Kind.QUESTION:
            raw = answer
            if requirement.question_type == onboarding.QuestionType.MULTICHOICE:
                try:
                    raw = json.loads(answer or "[]")
                except ValueError:
                    raw = [part for part in (answer or "").split(",") if part]
            onboarding.submit_as(request.user, membership, requirement, raw=raw)
        else:
            onboarding.submit_as(request.user, membership, requirement, document=document, valid_until=valid_until)
    except Invalid as error:
        raise HttpError(409 if getattr(error, "code", "") == "locked" else 422, str(error)) from None
    return {"memberships": _membership_rows(_people(viewer))}


# --- personal statistics and their public link -----------------------------------------------------------------------------


class MyStatsOut(PublicPlayerStatsOut):
    #: The secret in the personal public link (``/stats/<token>``); null while the link is off.
    share_token: str | None = None


@router.get("/stats", summary="Who has personal statistics here, and whether their public link is on")
def stats_people(request):
    viewer = _viewer(request)
    return {
        "available": game_stats.analytics_supported(request.tenant, request.features),
        "people": [
            {"id": m.pk, "name": str(m), "first_name": m.first_name, "photo_url": m.photo_url, "is_self": m.pk == viewer.pk, "share_token": m.stats_share_token}
            for m in _people(viewer).values()
        ],
    }


@router.get("/stats/{member_id}", response=MyStatsOut, summary="My (or my child's) game statistics, the same numbers as the public link")
def my_stats(request, member_id: UUID, season: UUID | None = None):
    _viewer_, member = _own(request, member_id)
    if not game_stats.analytics_supported(request.tenant, request.features):
        raise HttpError(404, "Not found.")
    chosen = None
    if season is not None:
        chosen = Season.objects.filter(pk=season).first()
        if chosen is None:
            raise HttpError(404, "Not found.")
    player, goalie = member_stats.game_sections(member)
    return {"first_name": member.first_name, "last_name": member.last_name, "game_stats": member_document(member, chosen), "player": player, "goalie": goalie, "share_token": member.stats_share_token}


@router.put("/stats/{member_id}/share", summary="Switch the personal public link on (rotate=true replaces it, the old link stops working)")
def stats_share_on(request, member_id: UUID, rotate: bool = False):
    _viewer_, member = _own(request, member_id)
    if not game_stats.analytics_supported(request.tenant, request.features):
        raise HttpError(404, "Not found.")
    return {"share_token": stats_sharing.enable_for_owner(request.user, member, rotate=rotate)}


@router.delete("/stats/{member_id}/share", response={204: None}, summary="Switch the personal public link off")
def stats_share_off(request, member_id: UUID):
    _viewer_, member = _own(request, member_id)
    stats_sharing.disable_for_owner(request.user, member)
    return 204, None
