from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from club.models import Club
from features.models import EmailSuppression
from notifications import alerts
from notifications.models import NotificationDelivery, PushAlert

S = NotificationDelivery.Status


class PushAlertTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        cls.club = Club.objects.create(name="Ajax United")
        cls.root = User.objects.create_superuser(email="root@example.com", password="pw-secret-123")
        cls.root2 = User.objects.create_superuser(email="root2@example.com", password="pw-secret-123")
        cls.staff = User.objects.create_user(email="staff@example.com", password="pw-secret-123", is_staff=True)
        cls.member = User.objects.create_user(email="member@example.com", password="pw-secret-123")

    def setUp(self):
        super().setUp()
        cache.clear()
        # The retry job has never run in a test database, which would read as a stuck queue.
        self.queue_patch = mock.patch("notifications.monitoring.retry_queue", return_value={"stuck": False})
        self.queue_patch.start()
        self.addCleanup(self.queue_patch.stop)

    def fail(self, n, *, provider="apns", status=S.FAILED, age=timedelta(minutes=5)):
        for i in range(n):
            row = NotificationDelivery.objects.create(club=self.club, channel="native", target=f"t{i}", status=status, provider=provider)
            NotificationDelivery.objects.filter(pk=row.pk).update(created=timezone.now() - age)

    def make_apns_down(self):
        self.fail(10, status=S.FAILED)

    def recipients(self):
        return {to for message in mail.outbox for to in message.to}

    def test_a_down_provider_opens_an_alert_and_mails_only_superusers(self):
        self.make_apns_down()
        counts = alerts.evaluate()
        self.assertEqual(counts["opened"], 1)
        alert = PushAlert.objects.get(key="provider:apns")
        self.assertEqual(alert.severity, alerts.CRITICAL)
        self.assertEqual(alert.notifications_sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.recipients(), {"root@example.com", "root2@example.com"})
        self.assertIn("[RosterChief] Push problem: APNs (iOS) is down", mail.outbox[0].subject)
        self.assertIn("/control/push/", mail.outbox[0].body)

    def test_the_sandbox_being_on_is_an_alert_until_it_is_switched_back(self):
        from notifications import sandbox

        sandbox.set_mode(self.root, on=True)
        alerts.evaluate()

        self.assertTrue(PushAlert.objects.get(key="sandbox").is_open)
        self.assertEqual(self.recipients(), {"root@example.com", "root2@example.com"})

        sandbox.set_mode(self.root, on=False)
        alerts.evaluate()

        self.assertFalse(PushAlert.objects.get(key="sandbox").is_open)

    def test_a_sandbox_period_that_ran_out_is_recorded_and_resolved(self):
        from notifications import sandbox
        from notifications.models import SandboxEvent

        sandbox.set_mode(self.root, on=True, expires_in=timedelta(minutes=30))
        alerts.evaluate(now=timezone.now() + timedelta(hours=1))

        self.assertFalse(sandbox.enabled())
        self.assertTrue(SandboxEvent.objects.filter(action="expired").exists())

    def test_quiet_provider_below_min_volume_is_not_an_alert(self):
        self.fail(3)
        alerts.evaluate()
        self.assertFalse(PushAlert.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_no_duplicate_mail_on_the_next_check(self):
        self.make_apns_down()
        now = timezone.now()
        alerts.evaluate(now)
        alerts.evaluate(now + timedelta(minutes=5))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(PushAlert.objects.count(), 1)

    def test_reminder_after_six_hours(self):
        self.make_apns_down()
        now = timezone.now()
        alerts.evaluate(now)
        # keep the failures inside the 1h window of the later checks
        NotificationDelivery.objects.update(created=now + timedelta(hours=6, minutes=-5))
        alerts.evaluate(now + timedelta(hours=5, minutes=59))
        self.assertEqual(len(mail.outbox), 1)
        counts = alerts.evaluate(now + timedelta(hours=6, minutes=1))
        self.assertEqual(counts["reminded"], 1)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("still open", mail.outbox[1].subject)
        self.assertEqual(PushAlert.objects.get().notifications_sent, 2)

    def test_escalation_from_degraded_to_down_mails_immediately(self):
        self.fail(18, status=S.SENT)
        self.fail(2, status=S.FAILED)  # 90% -> degraded
        alerts.evaluate()
        alert = PushAlert.objects.get()
        self.assertEqual(alert.severity, alerts.WARNING)
        self.assertEqual(len(mail.outbox), 1)
        NotificationDelivery.objects.filter(status=S.SENT).update(status=S.FAILED)  # 0% -> down
        counts = alerts.evaluate()
        self.assertEqual(counts["escalated"], 1)
        alert.refresh_from_db()
        self.assertEqual(alert.severity, alerts.CRITICAL)
        self.assertEqual(len(mail.outbox), 2)

    def test_recovery_resolves_and_mails(self):
        self.make_apns_down()
        alerts.evaluate()
        NotificationDelivery.objects.all().delete()
        counts = alerts.evaluate()
        self.assertEqual(counts["resolved"], 1)
        self.assertIsNotNone(PushAlert.objects.get().resolved_at)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Push recovered", mail.outbox[1].subject)

    def test_a_returning_problem_reopens_the_alert(self):
        self.make_apns_down()
        alerts.evaluate()
        NotificationDelivery.objects.all().delete()
        alerts.evaluate()
        self.make_apns_down()
        alerts.evaluate()
        alert = PushAlert.objects.get()
        self.assertIsNone(alert.resolved_at)
        self.assertEqual(alert.notifications_sent, 1)
        self.assertEqual(len(mail.outbox), 3)

    def test_paused_email_is_respected_and_sent_once_unpaused(self):
        EmailSuppression.start()
        self.make_apns_down()
        alerts.evaluate()
        self.assertEqual(mail.outbox, [])
        alert = PushAlert.objects.get()
        self.assertEqual(alert.notifications_sent, 0)
        EmailSuppression.objects.update(is_active=False)
        cache.clear()
        alerts.evaluate()
        self.assertEqual(len(mail.outbox), 1)

    def test_stuck_retry_queue(self):
        self.queue_patch.stop()
        NotificationDelivery.objects.create(club=self.club, channel="native", target="t", status=S.RETRYING, provider="apns", next_attempt_at=timezone.now() - timedelta(hours=1))
        alerts.evaluate()
        self.assertTrue(PushAlert.objects.filter(key="retry_queue").exists())

    def test_configuration_mismatch(self):
        from appapi.models import PushDevice

        PushDevice.objects.create(user=self.member, installation_id="inst-1", platform="ios", push_token="abc")
        with mock.patch("appapi.push.apns_configured", return_value=False):
            counts = alerts.evaluate()
        self.assertEqual(counts["opened"], 1)
        self.assertTrue(PushAlert.objects.filter(key="config:apns").exists())

    def test_stale_pending_outbox(self):
        from notifications.models import OutboxEntry

        entry = OutboxEntry.objects.create(kind=OutboxEntry.Kind.NOTIFICATIONS)
        OutboxEntry.objects.filter(pk=entry.pk).update(available_at=timezone.now() - timedelta(minutes=6))
        alerts.evaluate()
        self.assertTrue(PushAlert.objects.filter(key="outbox").exists())
