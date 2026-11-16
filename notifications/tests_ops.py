"""Health numbers, alerts, cleanup and the device registry."""

from datetime import timedelta

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from . import alerts, cleanup, health, sandbox
from .models import NotificationDelivery, OutboxEntry, PushAlert, PushDevice, SandboxEvent, SandboxMessage, WebPushSubscription
from .services import register_device, remove_device, subscribe_web, unsubscribe_web
from .tests_support import NO_CREDENTIALS, WEB_ENDPOINT, PushTestCase, make_device, make_subscription, make_user

Status = NotificationDelivery.Status


def log(provider="apns", status="sent", duration=None, age=timedelta(0), **extra):
    row = NotificationDelivery.objects.create(provider=provider, channel={"apns": "native", "fcm": "native", "webpush": "web", "email": "email"}[provider], target="t", status=status, duration_ms=duration, **extra)
    if age:
        NotificationDelivery.objects.filter(pk=row.pk).update(created=timezone.now() - age)
    return row


class HealthTests(PushTestCase):
    since = property(lambda self: timezone.now() - timedelta(hours=24))

    def test_empty(self):
        cards = health.provider_health(self.since)
        self.assertEqual(set(cards), {"apns", "fcm", "webpush", "email"})
        self.assertEqual(set(cards["apns"]), {"requests", "sent", "failed", "dead", "retrying", "success_rate", "p50_ms", "p95_ms", "last_success", "last_failure", "state"})
        self.assertEqual((cards["apns"]["requests"], cards["apns"]["success_rate"], cards["apns"]["p50_ms"], cards["apns"]["state"]), (0, None, None, "quiet"))

    def test_numbers(self):
        for ms in range(10, 101, 10):
            log(status="sent" if ms <= 80 else "failed", duration=ms)  # 8 sent, 2 failed
        log(status="dead", duration=5)
        log(status="held")  # not a send attempt
        log(provider="fcm", status="sent", duration=1)
        card = health.provider_health(self.since)["apns"]
        self.assertEqual((card["requests"], card["sent"], card["failed"], card["dead"]), (11, 8, 2, 1))
        self.assertEqual(card["success_rate"], 80.0)  # sent / (requests - dead)
        self.assertEqual((card["p50_ms"], card["p95_ms"]), (50, 100))  # nearest rank over 11 values: 5,10..100 -> rank 6 and 11
        self.assertEqual(card["state"], "degraded")
        self.assertIsNotNone(card["last_success"])
        self.assertIsNotNone(card["last_failure"])

    def test_states(self):
        for _ in range(4):
            log(status="sent")
        self.assertEqual(health.provider_health(self.since)["apns"]["state"], "quiet")  # 4 < health_min_requests
        log(status="sent")
        self.assertEqual(health.provider_health(self.since)["apns"]["state"], "ok")
        for _ in range(5):
            log(status="failed")
        self.assertEqual(health.provider_health(self.since)["apns"]["success_rate"], 50.0)
        self.assertEqual(health.provider_health(self.since)["apns"]["state"], "degraded")  # 50% is not below down_below_percent
        log(status="failed")
        self.assertEqual(health.provider_health(self.since)["apns"]["state"], "down")

    def test_thresholds_follow_the_settings(self):
        self.settings.health_min_requests = 1
        self.settings.degraded_below_percent = 99
        self.settings.down_below_percent = 90
        self.settings.save()
        for status in ["sent"] * 19 + ["failed"]:
            log(status=status)
        self.assertEqual(health.provider_health(self.since)["apns"]["success_rate"], 95.0)
        self.assertEqual(health.provider_health(self.since)["apns"]["state"], "degraded")

    def test_window_and_club_filter_and_retrying_ignores_the_window(self):
        log(status="sent", age=timedelta(hours=30))
        log(status="retrying", age=timedelta(hours=30), next_attempt_at=timezone.now())
        card = health.provider_health(self.since)["apns"]
        self.assertEqual((card["requests"], card["retrying"]), (0, 1))
        card = health.provider_health(self.since - timedelta(days=2))["apns"]
        self.assertEqual(card["requests"], 2)

    def test_retry_queue_and_outbox(self):
        now = timezone.now()
        self.assertEqual(health.retry_queue(), {"waiting": 0, "oldest_next_attempt": None, "overdue": 0, "stuck": False, "reasons": []})
        log(status="retrying", next_attempt_at=now - timedelta(minutes=30))
        log(status="retrying", next_attempt_at=now + timedelta(minutes=3))
        queue = health.retry_queue(now)
        self.assertEqual((queue["waiting"], queue["overdue"], queue["stuck"]), (2, 1, True))
        self.assertEqual(queue["oldest_next_attempt"], now - timedelta(minutes=30))
        self.assertTrue(queue["reasons"])
        OutboxEntry.objects.create(payload={}, available_at=now - timedelta(minutes=9))
        OutboxEntry.objects.create(payload={}, available_at=now, status="failed")
        stats = health.outbox_stats()
        self.assertEqual((stats["pending"], stats["failed"], stats["oldest_pending"]), (1, 1, now - timedelta(minutes=9)))


class AlertTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.settings.alert_emails = "ops@example.com, second@example.com"
        self.settings.save()

    def bad_apns(self, n=10):
        for _ in range(n):
            log(status="failed")

    def test_open_once_remind_after_six_hours_then_resolve(self):
        self.bad_apns()
        counts = alerts.evaluate()
        self.assertEqual(counts["opened"], 1)
        alert = PushAlert.objects.get()
        self.assertEqual((alert.key, alert.notifications_sent, alert.resolved_at), ("provider:apns", 1, None))
        self.assertIn("APNs (iOS) is down", alert.message)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(sorted(mail.outbox[0].to), ["ops@example.com", "second@example.com"])
        self.assertIn("APNs", mail.outbox[0].subject)

        self.assertEqual(alerts.evaluate()["opened"], 0)  # same outage: no new alert, no new mail
        self.assertEqual(len(mail.outbox), 1)

        PushAlert.objects.update(last_notified_at=timezone.now() - timedelta(hours=7))
        self.assertEqual(alerts.evaluate()["reminded"], 1)
        self.assertEqual((len(mail.outbox), PushAlert.objects.get().notifications_sent), (2, 2))
        self.assertIn("still open", mail.outbox[1].subject)

        NotificationDelivery.objects.all().delete()
        self.assertEqual(alerts.evaluate()["resolved"], 1)
        alert.refresh_from_db()
        self.assertIsNotNone(alert.resolved_at)
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("recovered", mail.outbox[2].subject)

    def test_a_new_outage_after_resolution_opens_a_new_alert(self):
        self.bad_apns()
        alerts.evaluate()
        NotificationDelivery.objects.all().delete()
        alerts.evaluate()
        self.bad_apns()
        alerts.evaluate()
        self.assertEqual(PushAlert.objects.filter(key="provider:apns").count(), 2)
        self.assertEqual(PushAlert.objects.filter(resolved_at__isnull=True).count(), 1)

    def test_degraded_becoming_down_is_mailed_at_once(self):
        for status in ["sent"] * 9 + ["failed"]:
            log(status=status)
        alerts.evaluate()
        self.assertIn("degraded", PushAlert.objects.get().message)
        self.bad_apns(30)
        self.assertEqual(alerts.evaluate()["escalated"], 1)
        self.assertIn("down", PushAlert.objects.get().message)
        self.assertEqual(len(mail.outbox), 2)

    def test_quiet_providers_raise_nothing(self):
        for _ in range(3):
            log(status="failed")
        self.assertEqual(alerts.evaluate(), {"opened": 0, "reminded": 0, "escalated": 0, "resolved": 0})

    def test_stuck_retry_queue_and_outbox_backlog(self):
        log(status="retrying", next_attempt_at=timezone.now() - timedelta(hours=1))
        OutboxEntry.objects.create(payload={}, available_at=timezone.now() - timedelta(minutes=10))
        alerts.evaluate()
        self.assertEqual(set(PushAlert.objects.values_list("key", flat=True)), {"retry_queue", "outbox_backlog"})

    @override_settings(**NO_CREDENTIALS)
    def test_credentials_alert_when_devices_exist_without_credentials(self):
        make_device(make_user(), "ios")
        alerts.evaluate()
        self.assertEqual(list(PushAlert.objects.values_list("key", flat=True)), ["credentials:apns"])
        PushDevice.objects.all().delete()
        alerts.evaluate()
        self.assertIsNotNone(PushAlert.objects.get().resolved_at)

    def test_sandbox_left_on(self):
        sandbox.set_mode(None, True)
        self.assertEqual(alerts.evaluate()["opened"], 0)  # just switched on on purpose
        SandboxEvent.objects.update(created=timezone.now() - timedelta(hours=2))
        alerts.evaluate()
        self.assertEqual(PushAlert.objects.get().key, "sandbox_left_on")
        sandbox.set_mode(None, False)
        alerts.evaluate()
        self.assertIsNotNone(PushAlert.objects.get().resolved_at)

    def test_recipients_fall_back_to_the_platform_superusers(self):
        from tenant_users.permissions.models import UserTenantPermissions

        self.settings.alert_emails = ""
        self.settings.save()
        admin = make_user("root@example.com")
        staff = make_user("staff@example.com")
        UserTenantPermissions.objects.create(profile=admin, is_staff=True, is_superuser=True)
        UserTenantPermissions.objects.create(profile=staff, is_staff=True, is_superuser=False)
        self.assertEqual(alerts.recipients(), ["root@example.com"])

    def test_no_recipients_means_no_mail_but_the_alert_is_kept(self):
        self.settings.alert_emails = ""
        self.settings.save()
        self.bad_apns()
        alerts.evaluate()
        self.assertEqual((len(mail.outbox), PushAlert.objects.get().notifications_sent), (0, 0))


class CleanupTests(PushTestCase):
    def test_deliveries_follow_the_retention_settings(self):
        keep_sent = log(status="sent", age=timedelta(days=13))
        log(status="sent", age=timedelta(days=15))
        keep_failed = log(status="failed", age=timedelta(days=59))
        log(status="dead", age=timedelta(days=61))
        retrying = log(status="retrying", age=timedelta(days=90), next_attempt_at=timezone.now())
        held = log(status="held", age=timedelta(days=90), next_attempt_at=timezone.now())
        self.assertEqual(cleanup.purge_deliveries(), 2)
        self.assertEqual({r.pk for r in NotificationDelivery.objects.all()}, {keep_sent.pk, keep_failed.pk, retrying.pk, held.pk})
        self.settings.sent_retention_days = 1
        self.settings.save()
        cleanup.purge_deliveries()
        self.assertNotIn(keep_sent.pk, {r.pk for r in NotificationDelivery.objects.all()})

    def test_devices(self):
        user = make_user()
        old = timezone.now() - timedelta(days=400)
        disabled_long = make_device(user, enabled=False, disabled_at=timezone.now() - timedelta(days=31))
        disabled_recent = make_device(user, enabled=False, disabled_at=timezone.now() - timedelta(days=5))
        unseen = make_device(user)
        PushDevice.objects.filter(pk=unseen.pk).update(last_seen=old)
        fresh = make_device(user)
        # a disabled row superseded by an enabled one with the same token
        twin_token = "same-token"
        superseded = make_device(user, token=twin_token, enabled=False, disabled_at=timezone.now())
        make_device(user, token=twin_token)
        self.assertEqual(cleanup.drop_superseded(), 1)
        self.assertEqual(cleanup.purge_devices(), 2)
        left = set(PushDevice.objects.values_list("pk", flat=True))
        self.assertIn(disabled_recent.pk, left)
        self.assertIn(fresh.pk, left)
        for gone in (disabled_long, unseen, superseded):
            self.assertNotIn(gone.pk, left)

    def test_sandbox_messages_older_than_seven_days(self):
        SandboxMessage.objects.create(channel="native", provider="apns", target="x", outcome="delivered")
        old = SandboxMessage.objects.create(channel="native", provider="apns", target="x", outcome="delivered")
        SandboxMessage.objects.filter(pk=old.pk).update(created=timezone.now() - timedelta(days=8))
        counts = cleanup.run()
        self.assertEqual((counts["sandbox_messages"], SandboxMessage.objects.count()), (1, 1))
        self.assertEqual(set(counts), {"deliveries", "superseded_devices", "devices", "sandbox_messages", "outbox"})


class RegistryTests(PushTestCase):
    def register(self, user, **extra):
        args = {"installation_id": "inst-1", "platform": "ios", "environment": "production", "push_token": "tok-1"}
        return register_device(user, **{**args, **extra})

    def test_upsert_by_installation_id(self):
        user = make_user()
        first = self.register(user, app_version="1.0", locale="nl")
        again = self.register(user, push_token="tok-2", app_version="1.1", environment="sandbox")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(PushDevice.objects.count(), 1)
        again.refresh_from_db()
        self.assertEqual((again.push_token, again.app_version, again.environment), ("tok-2", "1.1", "sandbox"))

    def test_reregistering_reenables_a_disabled_device(self):
        user = make_user()
        device = self.register(user)
        PushDevice.objects.filter(pk=device.pk).update(enabled=False, disabled_at=timezone.now())
        device = self.register(user)
        self.assertEqual((device.enabled, device.disabled_at), (True, None))

    def test_the_install_follows_the_account_that_signs_in(self):
        a, b = make_user(), make_user()
        self.register(a)
        device = self.register(b)
        self.assertEqual(device.user, b)
        self.assertEqual(PushDevice.objects.count(), 1)

    def test_other_rows_with_the_same_token_are_disabled(self):
        a, b = make_user(), make_user()
        old = self.register(a, installation_id="old", push_token="shared")
        other_platform = self.register(a, installation_id="and", platform="android", push_token="shared")
        new = self.register(b, installation_id="new", push_token="shared")
        old.refresh_from_db()
        other_platform.refresh_from_db()
        self.assertEqual((old.enabled, new.enabled, other_platform.enabled), (False, True, True))
        self.assertIsNotNone(old.disabled_at)

    def test_validation(self):
        user = make_user()
        for bad in ({"platform": "windows"}, {"environment": "staging"}, {"push_token": ""}):
            with self.assertRaises(ValueError):
                self.register(user, **bad)

    def test_remove_device_is_scoped_to_the_account(self):
        a, b = make_user(), make_user()
        self.register(a)
        self.assertFalse(remove_device(b, "inst-1"))
        self.assertTrue(remove_device(a, "inst-1"))
        self.assertFalse(remove_device(a, "inst-1"))

    def test_web_subscription_upsert_and_unsubscribe(self):
        a, b = make_user(), make_user()
        first = subscribe_web(a, club=None, endpoint=WEB_ENDPOINT, p256dh="p1", auth="a1", user_agent="UA")
        again = subscribe_web(b, club=None, endpoint=WEB_ENDPOINT, p256dh="p2", auth="a2")
        self.assertEqual((first.pk, again.user, again.p256dh, WebPushSubscription.objects.count()), (first.pk, b, "p2", 1))
        self.assertFalse(unsubscribe_web(a, WEB_ENDPOINT))
        self.assertTrue(unsubscribe_web(b, WEB_ENDPOINT))
        self.assertEqual(make_subscription(a).user, a)
