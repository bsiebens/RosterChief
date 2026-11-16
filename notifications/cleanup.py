"""Keeping the push tables free of dead weight, driven by ``PushSettings``."""

from datetime import timedelta

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from . import outbox, sandbox
from .models import NotificationDelivery, PushDevice, PushSettings

Status = NotificationDelivery.Status


def purge_deliveries(now=None) -> int:
    """Sent rows after ``sent_retention_days``; failed and dead ones after ``failed_retention_days``. RETRYING and HELD rows stay."""
    now = now or timezone.now()
    ps = PushSettings.get()
    sent, _ = NotificationDelivery.objects.filter(status=Status.SENT, created__lt=now - timedelta(days=ps.sent_retention_days)).delete()
    other, _ = NotificationDelivery.objects.filter(status__in=[Status.FAILED, Status.DEAD], created__lt=now - timedelta(days=ps.failed_retention_days)).delete()
    return sent + other


def drop_superseded() -> int:
    """Delete disabled devices whose token is now held by another enabled device (an earlier registration of the same install)."""
    newer = PushDevice.objects.filter(enabled=True, push_token=OuterRef("push_token"), platform=OuterRef("platform")).exclude(pk=OuterRef("pk"))
    count, _ = PushDevice.objects.filter(enabled=False).filter(Exists(newer)).delete()
    return count


def purge_devices(now=None) -> int:
    """Delete devices disabled for ``disabled_device_retention_days`` and devices unseen for ``unseen_device_retention_days``."""
    now = now or timezone.now()
    ps = PushSettings.get()
    disabled_before = now - timedelta(days=ps.disabled_device_retention_days)
    gone, _ = PushDevice.objects.filter(enabled=False).filter(Q(disabled_at__lt=disabled_before) | Q(disabled_at__isnull=True, last_seen__lt=disabled_before)).delete()
    unseen, _ = PushDevice.objects.filter(last_seen__lt=now - timedelta(days=ps.unseen_device_retention_days)).delete()
    return gone + unseen


def run(now=None) -> dict:
    now = now or timezone.now()
    return {
        "deliveries": purge_deliveries(now),
        "superseded_devices": drop_superseded(),
        "devices": purge_devices(now),
        "sandbox_messages": sandbox.purge(now),
        "outbox": outbox.purge(now),
    }
