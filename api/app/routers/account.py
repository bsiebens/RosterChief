"""Account plane (API.md §4, §7.1): base domain only. Who am I, which clubs, and this
install's push registration."""

import datetime
import uuid

from allauth.mfa.models import Authenticator
from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from ninja import Router

from api.models import PushDevice
from members.models import Member
from members.services.family import managed_people
from notifications import preferences, quiet_hours
from notifications.models import NotificationCategory, NotificationPreference, QuietHours
from notifications.policy import android_channels

from .. import services
from ..auth import require_base_domain
from ..cleanup import drop_superseded
from ..errors import ApiError
from ..push import get_gateway
from ..schemas import AccountSettingsIn, AccountSettingsOut, ClubListOut, DeviceIn, DeviceOut, ErrorOut, MeOut, MetaOut, NotificationPreferencesIn, NotificationPreferencesOut, QuietHoursIn, QuietHoursOut

router = Router(tags=["account"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


@router.get("/meta", auth=None, response={200: MetaOut, 404: ErrorOut}, summary="Server and client-version info")
def meta(request):
    """No authentication. Call on launch: if the app's version is below
    `min_supported_app_version`, show an 'update required' screen."""
    require_base_domain(request)
    return {
        "api_version": "1",
        "server_time": timezone.now(),
        "min_supported_app_version": {"ios": settings.APP_MIN_VERSION_IOS, "android": settings.APP_MIN_VERSION_ANDROID},
        "docs_url": request.build_absolute_uri("/api/docs/"),
    }


@router.get("/me", response={200: MeOut, **ERRORS}, summary="The signed-in account")
def me(request):
    require_base_domain(request)
    user = request.user
    member = Member.objects.filter(user=user).first()
    return {
        "user": {"id": user.pk, "email": user.email},
        "member": member,
        "mfa": {"enabled": is_mfa_enabled(user), "types": sorted(Authenticator.objects.filter(user=user).values_list("type", flat=True))},
        "settings": {"email_notifications": user.email_notifications},
    }


@router.patch("/me/settings", response={200: AccountSettingsOut, **ERRORS}, summary="Change the account's settings")
def update_settings(request, payload: AccountSettingsIn):
    """Account-wide, for every club. `email_notifications`: `always` (default) or `unless_app`
    -- skip the notification email while this account has the app installed."""
    require_base_domain(request)
    request.user.email_notifications = payload.email_notifications
    request.user.save(update_fields=["email_notifications"])
    return {"email_notifications": request.user.email_notifications}


def _category_descriptions() -> dict:
    return {
        NotificationCategory.NEWS: _("News posts from your clubs."),
        NotificationCategory.EVENT: _("A new event, or a change to or cancellation of an existing one."),
        NotificationCategory.REMINDER: _("A nudge when an event is coming up and nobody has answered yet."),
        NotificationCategory.LINEUP: _("The line-up for a game has been published or changed."),
        NotificationCategory.CREW: _("Invitations and changes for referee and official duties."),
        NotificationCategory.FORM: _("A form that is waiting to be filled in."),
        NotificationCategory.SHOP: _("Updates about your shop orders."),
        NotificationCategory.COACH: _("Messages that a coach sends to the team."),
        NotificationCategory.ADMIN: _("Things the club administration needs from you."),
        NotificationCategory.ANNOUNCEMENT: _("Announcements from the RosterChief platform."),
        NotificationCategory.GAME: _("Live updates of the games you follow, such as goals and the final score."),
        NotificationCategory.GENERAL: _("Everything else."),
    }


def _preferences_out(user) -> dict:
    descriptions = _category_descriptions()
    channels = {entry["category"]: entry for entry in android_channels()}
    me = Member.objects.filter(user=user).first()
    clubs = []
    for club in services.clubs_for(user):
        effective = preferences.matrix(user, club)
        people = [
            {
                "member_id": person.pk,
                "name": str(person),
                "is_self": person.pk == getattr(me, "pk", None),
                "overrides": preferences.overrides(user, club, person),
                "effective": preferences.matrix(user, club, person),
                "master": preferences.master_matrix(user, club, person),
                "master_overrides": preferences.master_overrides(user, club, person),
            }
            for person in managed_people(me, club)
        ]
        clubs.append(
            {
                "club_slug": club.slug,
                "club_name": club.name,
                "overrides": preferences.overrides(user, club),
                "effective": effective,
                "master": preferences.master_matrix(user, club),
                "master_overrides": preferences.master_overrides(user, club),
                "people": people,
            }
        )
    return {
        "categories": [{"key": value, "label": str(label), "description": str(descriptions[value]), "android_channel_id": channels[value]["id"], "android_importance": channels[value]["importance"]} for value, label in NotificationCategory.choices],
        "defaults": preferences.matrix(user),
        "master_defaults": preferences.master_matrix(user),
        "clubs": clubs,
    }


def _scope_person(user, club, member_id):
    """The person a per-person call is about: one of the people the account manages in ``club``."""
    if member_id is None:
        return None
    if club is None:
        raise ApiError(422, "validation_error", fields={"member_id": [_("A person needs a club.")]})
    me = Member.objects.filter(user=user).first()
    person = next((person for person in managed_people(me, club) if person.pk == member_id), None)
    if person is None:
        raise ApiError(422, "validation_error", fields={"member_id": [_("Unknown person.")]})
    return person


def _scope_club(user, club_slug: str | None):
    """The club a preference call is about (``None`` = all clubs); only clubs the account belongs to."""
    if club_slug is None:
        return None
    club = next((club for club in services.clubs_for(user) if club.slug == club_slug), None)
    if club is None:
        raise ApiError(422, "validation_error", fields={"club_slug": [_("Unknown club.")]})
    return club


@router.get("/me/notification-preferences", response={200: NotificationPreferencesOut, **ERRORS}, exclude_none=True, summary="Which notifications reach this account")
def get_notification_preferences(request):
    """Per category two switches: `push` (this account's phones and browsers) and `email`. The in-app
    inbox is never switchable. `defaults` are the all-clubs switches; each club in `clubs` can override
    them (`overrides`), and `effective` is what applies there. No stored switch means on."""
    require_base_domain(request)
    return _preferences_out(request.user)


@router.patch("/me/notification-preferences", response={200: NotificationPreferencesOut, **ERRORS, 422: ErrorOut}, exclude_none=True, summary="Switch notifications on or off")
def update_notification_preferences(request, payload: NotificationPreferencesIn):
    """`club_slug: null` changes the all-clubs defaults, a slug writes overrides for that club only.
    Each change names a `category` and the `push` and/or `email` switch to set (an omitted one is left
    alone). Repeating a change is harmless. Nothing is saved when any change is invalid. Answers like GET."""
    require_base_domain(request)
    club = _scope_club(request.user, payload.club_slug)
    person = _scope_person(request.user, club, payload.member_id)
    valid = set(NotificationCategory.values)
    unknown = sorted({change.category for change in payload.changes if change.category not in valid})
    if unknown:
        raise ApiError(422, "validation_error", fields={"changes": [_("Unknown category: %(category)s.") % {"category": ", ".join(unknown)}]})
    with transaction.atomic():
        if payload.master_push is not None:
            preferences.set_master(request.user, club, NotificationPreference.Channel.PUSH, payload.master_push, member=person)
        if payload.master_email is not None:
            preferences.set_master(request.user, club, NotificationPreference.Channel.EMAIL, payload.master_email, member=person)
        for change in payload.changes:
            if change.push is not None:
                preferences.set_preference(request.user, club, change.category, NotificationPreference.Channel.PUSH, change.push, member=person)
            if change.email is not None:
                preferences.set_preference(request.user, club, change.category, NotificationPreference.Channel.EMAIL, change.email, member=person)
    return _preferences_out(request.user)


@router.delete("/me/notification-preferences", response={200: NotificationPreferencesOut, **ERRORS, 422: ErrorOut}, exclude_none=True, summary="Back to the inherited notification switches")
def reset_notification_preferences(request, club_slug: str | None = None, category: str | None = None, member_id: uuid.UUID | None = None):
    """Drops the stored switches of one scope (`club_slug` empty: the all-clubs defaults, which go back
    to on; a slug: that club's overrides, which go back to the defaults). With `category` only that
    category's, otherwise all of them. Nothing stored is fine. Answers like GET."""
    require_base_domain(request)
    club = _scope_club(request.user, club_slug or None)
    person = _scope_person(request.user, club, member_id)
    if category and category not in NotificationCategory.values:
        raise ApiError(422, "validation_error", fields={"category": [_("Unknown category.")]})
    preferences.reset(request.user, club, category or None, member=person)
    return _preferences_out(request.user)


def _quiet_hours_out(user) -> dict:
    hours = quiet_hours.get(user)
    if hours is None:
        return {"enabled": False, "start": "22:00", "end": "07:00", "timezone": settings.TIME_ZONE, "active_now": False, "until": None}
    until = quiet_hours.window_end(hours) if hours.enabled else None
    return {"enabled": hours.enabled, "start": f"{hours.start:%H:%M}", "end": f"{hours.end:%H:%M}", "timezone": hours.timezone, "active_now": until is not None, "until": until}


def _parse_hhmm(value: str, field: str) -> datetime.time:
    try:
        return datetime.datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise ApiError(422, "validation_error", fields={field: [_("Use the format HH:MM.")]}) from None


@router.get("/me/quiet-hours", response={200: QuietHoursOut, **ERRORS}, summary="Quiet hours for pushes")
def get_quiet_hours(request):
    """The daily window in which pushes to this account are held. Defaults when nothing is stored:
    off, 22:00 to 07:00 in the server's zone. **Critical** notifications (a cancelled or same-day changed
    event) and **email** ignore quiet hours. Held pushes arrive when the window ends; live game updates
    are dropped instead (stale by then). `until` is when the running window ends."""
    require_base_domain(request)
    return _quiet_hours_out(request.user)


@router.put("/me/quiet-hours", response={200: QuietHoursOut, **ERRORS, 422: ErrorOut}, summary="Set quiet hours")
def put_quiet_hours(request, payload: QuietHoursIn):
    """Replaces the quiet hours. `start`/`end` are `HH:MM` (end before start crosses midnight; equal
    means no window), `timezone` an IANA zone name. Answers like GET."""
    require_base_domain(request)
    start = _parse_hhmm(payload.start, "start")
    end = _parse_hhmm(payload.end, "end")
    try:
        quiet_hours.set_quiet_hours(request.user, enabled=payload.enabled, start=start, end=end, tz=payload.timezone)
    except ValueError:
        raise ApiError(422, "validation_error", fields={"timezone": [_("Unknown time zone.")]}) from None
    return _quiet_hours_out(request.user)


@router.delete("/me/quiet-hours", response={200: QuietHoursOut, **ERRORS}, summary="Remove quiet hours")
def delete_quiet_hours(request):
    """Back to the defaults (off). Nothing stored is fine. Answers like GET."""
    require_base_domain(request)
    QuietHours.objects.filter(user=request.user).delete()
    return _quiet_hours_out(request.user)


@router.get("/clubs", response={200: ClubListOut, **ERRORS}, summary="Clubs the account can use")
def clubs(request):
    """Every active club the account has a real tie to, with the base URL to use for that
    club's endpoints. Cache it; offer a club picker when there is more than one."""
    require_base_domain(request)
    items = []
    member = Member.objects.filter(user=request.user).first()
    for club in services.clubs_for(request.user):
        items.append(
            {
                "id": club.pk,
                "slug": club.slug,
                "name": club.name,
                "sport_type": club.sport_type,
                "api_base_url": services.club_api_base_url(request, club),
                "public_api_base_url": services.club_public_api_base_url(request, club),
                "logo_url": services.logo_url(request, club),
                "event_background_url": services.event_background_url(request, club),
                **services.club_colors(club),
                "relationship": services.relationship(request.user, member, club),
                "features": services.club_features(club),
            }
        )
    return {"items": items}


def _device_out(device: PushDevice) -> dict:
    return {"installation_id": device.installation_id, "platform": device.platform, "environment": device.environment, "push_enabled": device.enabled and get_gateway().supports(device)}


@router.post("/devices", response={200: DeviceOut, **ERRORS, 422: ErrorOut}, summary="Register this install for push")
def register_device(request, payload: DeviceIn):
    """Call after sign-in and whenever the push token changes (iOS may rotate it). Keyed by
    `installation_id`: repeating the call updates the same registration. If another account
    registered this install before, it moves to the signed-in account."""
    require_base_domain(request)
    with transaction.atomic():
        device, _created = PushDevice.objects.select_for_update().get_or_create(
            installation_id=payload.installation_id,
            defaults={"user": request.user, "platform": payload.platform, "environment": payload.environment, "push_token": payload.push_token},
        )
        if device.user_id != request.user.pk:
            device.live_activities.all().delete()  # the previous account's activities are not this one's
        device.user = request.user
        device.platform = payload.platform
        device.environment = payload.environment
        device.push_token = payload.push_token
        device.app_version = payload.app_version
        device.locale = payload.locale
        if payload.push_to_start_token is not None:
            device.push_to_start_token = payload.push_to_start_token
        device.enabled = True  # a fresh registration revives a device disabled after a dead-token report
        device.save()
        drop_superseded(device)
    return _device_out(device)


@router.delete("/devices/{installation_id}", response={204: None, **ERRORS}, summary="Unregister this install")
def unregister_device(request, installation_id: str):
    """Call on sign-out, before discarding the tokens. Only the account's own installs."""
    require_base_domain(request)
    device = PushDevice.objects.filter(installation_id=installation_id, user=request.user).first()
    if device is None:
        raise ApiError(404, "not_found")
    device.delete()
    return 204, None
