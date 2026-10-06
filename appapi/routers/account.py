"""Account plane (API.md §4, §7.1): base domain only. Who am I, which clubs, and this
install's push registration."""

from allauth.mfa.models import Authenticator
from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from ninja import Router

from members.models import Member

from .. import services
from ..auth import require_base_domain
from ..errors import ApiError
from ..models import PushDevice
from ..push import get_gateway
from ..schemas import AccountSettingsIn, AccountSettingsOut, ClubListOut, DeviceIn, DeviceOut, ErrorOut, MeOut, MetaOut

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
    return {"installation_id": device.installation_id, "platform": device.platform, "environment": device.environment, "push_enabled": device.enabled and bool(device.endpoint_arn)}


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
        device.user = request.user
        device.platform = payload.platform
        device.environment = payload.environment
        token_changed = device.push_token != payload.push_token
        device.push_token = payload.push_token
        device.app_version = payload.app_version
        device.locale = payload.locale
        if token_changed or not device.endpoint_arn or not device.enabled:
            device.endpoint_arn = get_gateway().register(device)
            device.enabled = True
        device.save()
    return _device_out(device)


@router.delete("/devices/{installation_id}", response={204: None, **ERRORS}, summary="Unregister this install")
def unregister_device(request, installation_id: str):
    """Call on sign-out, before discarding the tokens. Only the account's own installs."""
    require_base_domain(request)
    device = PushDevice.objects.filter(installation_id=installation_id, user=request.user).first()
    if device is None:
        raise ApiError(404, "not_found")
    get_gateway().unregister(device)
    device.delete()
    return 204, None
