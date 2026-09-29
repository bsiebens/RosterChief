"""Native push through Amazon SNS Mobile Push (API.md §9).

SNS holds the APNs (and later FCM) credentials as *platform applications* and gives each
device token an *endpoint*; we store that endpoint's ARN on the PushDevice and publish to
it. Everything talks to a ``PushGateway`` so tests (and a server without SNS) never touch
the network:

* ``SnsGateway`` -- the real one, used when a platform application ARN is configured;
* ``NullGateway`` -- no SNS configured: devices still register, nothing is sent;
* ``appapi.tests`` swaps in a fake via ``APP_PUSH_GATEWAY``.

Settings (all optional; empty = that platform can't receive pushes yet)::

    APP_PUSH_SNS_REGION
    APP_PUSH_SNS_APNS_ARN           # App Store / TestFlight builds
    APP_PUSH_SNS_APNS_SANDBOX_ARN   # debug builds from Xcode
    APP_PUSH_SNS_FCM_ARN            # Android, later
    APP_PUSH_GATEWAY                # dotted path to override the gateway class

Payloads are pointers, never content (API.md §9): a localised title/body plus
``{notification_id, club_slug, type, target}``; the app fetches the rest over the API.
"""

import json
import logging
import re

from django.conf import settings
from django.utils.module_loading import import_string

from .models import PushDevice

logger = logging.getLogger(__name__)

#: SNS reports "already exists with the same Token, but different attributes" and names the
#: existing endpoint in the message; we reuse that endpoint instead of failing.
_EXISTING_ENDPOINT = re.compile(r"Endpoint (arn:aws:sns:[^\s]+) already exists")


class PushGateway:
    def register(self, device: PushDevice) -> str:
        """Create or refresh the device's endpoint; return its ARN ('' if none)."""
        raise NotImplementedError

    def unregister(self, device: PushDevice) -> None:
        raise NotImplementedError

    def publish(self, device: PushDevice, *, title: str, body: str, data: dict) -> bool:
        """Send one push. False means the token is dead: the caller disables the device."""
        raise NotImplementedError


class NullGateway(PushGateway):
    def register(self, device):
        return ""

    def unregister(self, device):
        return None

    def publish(self, device, *, title, body, data):
        return True


def _platform_application_arn(device: PushDevice) -> str:
    if device.platform == PushDevice.Platform.IOS:
        if device.environment == PushDevice.Environment.SANDBOX:
            return settings.APP_PUSH_SNS_APNS_SANDBOX_ARN
        return settings.APP_PUSH_SNS_APNS_ARN
    return settings.APP_PUSH_SNS_FCM_ARN


def build_message(device: PushDevice, *, title: str, body: str, data: dict) -> str:
    """The SNS ``MessageStructure=json`` message for ``device``'s platform."""
    if device.platform == PushDevice.Platform.IOS:
        apns = {"aps": {"alert": {"title": title, "body": body}, "sound": "default", "mutable-content": 1}, **data}
        key = "APNS_SANDBOX" if device.environment == PushDevice.Environment.SANDBOX else "APNS"
        return json.dumps({"default": body, key: json.dumps(apns)})
    fcm = {"notification": {"title": title, "body": body}, "data": {name: str(value) for name, value in data.items()}}
    return json.dumps({"default": body, "GCM": json.dumps(fcm)})


class SnsGateway(PushGateway):
    def __init__(self, client=None):
        if client is None:
            import boto3

            client = boto3.client("sns", region_name=settings.APP_PUSH_SNS_REGION or None)
        self.client = client

    def register(self, device):
        application_arn = _platform_application_arn(device)
        if not application_arn:
            return ""
        if device.endpoint_arn:
            try:
                self.client.set_endpoint_attributes(EndpointArn=device.endpoint_arn, Attributes={"Token": device.push_token, "Enabled": "true", "CustomUserData": str(device.user_id)})
                return device.endpoint_arn
            except self.client.exceptions.NotFoundException:
                pass  # deleted on the SNS side: create a new one below
        try:
            response = self.client.create_platform_endpoint(PlatformApplicationArn=application_arn, Token=device.push_token, CustomUserData=str(device.user_id))
            return response["EndpointArn"]
        except self.client.exceptions.InvalidParameterException as exc:
            match = _EXISTING_ENDPOINT.search(str(exc))
            if not match:
                raise
            endpoint_arn = match.group(1)
            self.client.set_endpoint_attributes(EndpointArn=endpoint_arn, Attributes={"Token": device.push_token, "Enabled": "true", "CustomUserData": str(device.user_id)})
            return endpoint_arn

    def unregister(self, device):
        if device.endpoint_arn:
            self.client.delete_endpoint(EndpointArn=device.endpoint_arn)

    def publish(self, device, *, title, body, data):
        if not device.endpoint_arn:
            return True
        try:
            self.client.publish(TargetArn=device.endpoint_arn, MessageStructure="json", Message=build_message(device, title=title, body=body, data=data))
        except self.client.exceptions.EndpointDisabledException, self.client.exceptions.NotFoundException:
            return False
        return True


def get_gateway() -> PushGateway:
    override = getattr(settings, "APP_PUSH_GATEWAY", "")
    if override:
        return import_string(override)()
    if settings.APP_PUSH_SNS_APNS_ARN or settings.APP_PUSH_SNS_APNS_SANDBOX_ARN or settings.APP_PUSH_SNS_FCM_ARN:
        return SnsGateway()
    return NullGateway()
