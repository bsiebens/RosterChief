"""The member's own profile and notification settings: the signed-in person and the children they manage."""

from datetime import date, time
from uuid import UUID

from django.utils import timezone
from ninja import File, Router, Schema, UploadedFile
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from api.routers.portal import _people, _viewer
from events.models import Event
from members.models import Member
from members.services import members as member_services
from members.services.errors import Invalid
from events.models import EventFollow
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
