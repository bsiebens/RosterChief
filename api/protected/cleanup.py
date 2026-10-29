"""Keeping the native push device registry free of dead weight.

An APNs/FCM token that stops working is *disabled* when the provider says so (410 / UNREGISTERED), but the row
stays. A reinstall or a second sign-in also leaves rows behind: the app keeps one ``installation_id`` per install, a
new install is a new id. Without a sweep the registry only grows and every send pays one request per dead row.
"""

from datetime import timedelta

from django.utils import timezone

from api.models import PushDevice

#: A device the provider reported gone (disabled) is deleted after this long; a fresh registration revives it before that.
DISABLED_RETENTION = timedelta(days=30)
#: The app registers on every launch, so a device unseen this long is a phone that no longer has the app.
UNSEEN_RETENTION = timedelta(days=180)


def drop_superseded(device: PushDevice) -> int:
    """Delete the other rows holding the same push token as ``device`` (a token belongs to exactly one install, so
    those rows are an earlier registration of it, e.g. after the app lost its installation id)."""
    if not device.push_token:
        return 0
    deleted, _ = PushDevice.objects.filter(push_token=device.push_token, platform=device.platform).exclude(pk=device.pk).delete()
    return deleted


def purge(now=None) -> int:
    """Delete devices disabled for ``DISABLED_RETENTION`` or unseen for ``UNSEEN_RETENTION``. Returns how many."""
    now = now or timezone.now()
    gone, _ = PushDevice.objects.filter(enabled=False, last_seen__lt=now - DISABLED_RETENTION).delete()
    unseen, _ = PushDevice.objects.filter(last_seen__lt=now - UNSEEN_RETENTION).delete()
    return gone + unseen
