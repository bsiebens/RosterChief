"""``/devices``: the phone apps register their push token here (APNs for iOS, FCM for Android). One row per app installation; registering again
refreshes the token and keeps the device with the signed-in account, signing out removes it. Open to every member (no management access needed)."""

from ninja import Field, Router, Schema
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from notifications import services
from notifications.models import PushDevice

router = Router(auth=[TokenAuth(door=False), CookieAuth(door=False)], tags=["notifications"])


class DeviceIn(Schema):
    installation_id: str = Field(min_length=1, max_length=64)
    platform: str = Field(description="ios or android")
    environment: str = Field("production", description="sandbox (debug builds) or production")
    push_token: str = Field(min_length=1, max_length=4096)
    push_to_start_token: str = Field("", max_length=4096, description="iOS Live Activity push-to-start token")
    app_version: str = Field("", max_length=32)
    locale: str = Field("", max_length=16)


class DeviceOut(Schema):
    installation_id: str
    platform: str
    environment: str
    enabled: bool


@router.put("", response=DeviceOut, summary="Register or refresh this installation's push token", operation_id="devices_register")
def register(request, payload: DeviceIn):
    try:
        device = services.register_device(
            request.user, installation_id=payload.installation_id, platform=payload.platform, environment=payload.environment, push_token=payload.push_token,
            app_version=payload.app_version, locale=payload.locale, push_to_start_token=payload.push_to_start_token,
        )  # fmt: skip
    except ValueError as error:
        raise HttpError(400, str(error)) from error
    return device


@router.delete("/{installation_id}", response={204: None}, summary="Remove this installation (on sign out)", operation_id="devices_remove")
def remove(request, installation_id: str):
    services.remove_device(request.user, installation_id)
    return 204, None


@router.get("/{installation_id}", response=DeviceOut, summary="Whether this installation is registered", operation_id="devices_status")
def status(request, installation_id: str):
    device = PushDevice.objects.filter(user=request.user, installation_id=installation_id, enabled=True).first()
    if device is None:
        raise HttpError(404, "Not found.")
    return device
