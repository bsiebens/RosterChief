from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from api.models import PushDevice
from club.models import Club
from features.models import JobRun, JobToggle
from notifications import monitoring
from notifications.models import NotificationDelivery
from rosterchief.test_support import enrol_mfa

from .tests import ControlPanelTestBase, User

S = NotificationDelivery.Status


class PushMonitoringViewTests(ControlPanelTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.root = User.objects.create_superuser(email="super@example.com", password="pw-secret-123")
        enrol_mfa(cls.root)
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.root)
        self.url = reverse("controlpanel:push_monitoring")

    def make(self, *, provider="apns", status=S.SENT, age=timedelta(minutes=5), club=None, target="t1", channel="native", duration=100, error="", next_attempt_at=None):
        row = NotificationDelivery.objects.create(club=club or self.club, channel=channel, target=target, status=status, provider=provider, duration_ms=duration, last_error=error, next_attempt_at=next_attempt_at)
        NotificationDelivery.objects.filter(pk=row.pk).update(created=timezone.now() - age)
        return row

    def card(self, response, provider):
        return next(card for card in response.context["health"] if card["provider"] == provider)

    def test_access(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(User.objects.create_user(email="member@example.com", password="pw-secret-123"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        # staff who are not superusers are refused too
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_renders_empty(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Push monitoring")
        self.assertEqual(response.context["window"], "24h")
        self.assertEqual(self.card(response, "fcm")["state"], monitoring.QUIET)
        self.assertEqual(len(response.context["series"]), 24)

    def test_chart_has_axes_and_hover_amounts(self):
        for _i in range(3):
            self.make()
        self.make(status=S.FAILED)
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="y-axis"')
        self.assertContains(response, 'data-testid="x-axis"')
        self.assertContains(response, 'role="tooltip"', count=24)
        self.assertEqual(response.context["series"][0]["peak"], 4)
        self.assertEqual(len(response.context["x_ticks"]), 3)

    def test_numbers_per_provider(self):
        for _i in range(8):
            self.make(provider="apns", duration=100)
        self.make(provider="apns", status=S.FAILED, error="boom")
        self.make(provider="apns", status=S.DEAD, error="BadDeviceToken")
        self.make(provider="fcm", status=S.RETRYING, error="503", next_attempt_at=timezone.now() + timedelta(minutes=1))
        card = self.card(self.client.get(self.url), "apns")
        self.assertEqual((card["requests"], card["sent"], card["failed"], card["dead"]), (10, 8, 1, 1))
        self.assertEqual(card["success_rate"], 88.9)
        self.assertEqual(card["state"], monitoring.DEGRADED)
        self.assertEqual(card["p50"], 100)
        self.assertIsNotNone(card["last_failure"])
        self.assertEqual(self.card(self.client.get(self.url), "fcm")["retrying"], 1)

    def test_window_filter(self):
        self.make(age=timedelta(minutes=30))
        self.make(age=timedelta(hours=3))
        self.assertEqual(self.card(self.client.get(self.url + "?window=1h"), "apns")["requests"], 1)
        self.assertEqual(self.card(self.client.get(self.url + "?window=24h"), "apns")["requests"], 2)
        self.assertEqual(len(self.client.get(self.url + "?window=1h").context["series"]), 12)
        self.assertEqual(len(self.client.get(self.url + "?window=7d").context["series"]), 7)
        self.assertEqual(self.client.get(self.url + "?window=bogus").context["window"], "24h")

    def test_old_rows_excluded(self):
        self.make(age=timedelta(days=3))
        self.assertEqual(self.card(self.client.get(self.url), "apns")["requests"], 0)
        self.assertEqual(self.card(self.client.get(self.url + "?window=7d"), "apns")["requests"], 1)

    def test_club_filter(self):
        self.make()
        self.make(club=self.other)
        self.make(club=self.other, status=S.FAILED, error="x")
        response = self.client.get(self.url + f"?club={self.other.pk}")
        self.assertEqual(self.card(response, "apns")["requests"], 2)
        self.assertEqual(len(response.context["problems"]), 1)
        self.assertEqual(self.card(self.client.get(self.url + "?club=not-a-uuid"), "apns")["requests"], 3)

    def test_emails_are_masked_and_admin_link_present(self):
        row = self.make(provider="email", channel="email", status=S.FAILED, target="alice@example.com", error="550 rejected")
        response = self.client.get(self.url)
        self.assertContains(response, "a***@example.com")
        self.assertNotContains(response, "alice@example.com")
        self.assertContains(response, reverse("admin:notifications_notificationdelivery_change", args=[row.pk]))

    def test_top_reasons_grouped(self):
        for _i in range(3):
            self.make(status=S.FAILED, error="Unregistered")
        self.make(status=S.FAILED, error="Other")
        self.make()
        reasons = self.client.get(self.url).context["reasons"]
        self.assertEqual((reasons[0]["last_error"], reasons[0]["count"]), ("Unregistered", 3))
        self.assertEqual(len(reasons), 2)

    def test_stuck_queue_flags(self):
        JobRun.objects.create(task_id="a", name=monitoring.RETRY_JOB, status=JobRun.Status.SUCCESS, started_at=timezone.now())
        queue = self.client.get(self.url).context["queue"]
        self.assertFalse(queue["stuck"])
        self.make(status=S.RETRYING, next_attempt_at=timezone.now() - timedelta(minutes=30))
        queue = self.client.get(self.url).context["queue"]
        self.assertTrue(queue["stuck"])
        self.assertEqual((queue["waiting"], queue["overdue"]), (1, 1))
        self.assertContains(self.client.get(self.url), "Stuck")

    def test_paused_or_silent_job_is_flagged(self):
        self.assertTrue(self.client.get(self.url).context["queue"]["stuck"])  # never run
        JobRun.objects.create(task_id="a", name=monitoring.RETRY_JOB, status=JobRun.Status.SUCCESS, started_at=timezone.now())
        JobToggle.set_enabled(monitoring.RETRY_JOB, False)
        self.assertTrue(self.client.get(self.url).context["queue"]["stuck"])

    def test_configuration_mismatch(self):
        user = User.objects.create_user(email="p@example.com", password="pw-secret-123")
        PushDevice.objects.create(user=user, installation_id="i1", platform="ios", push_token="tok")
        with mock.patch("api.app.push.apns_configured", return_value=False), mock.patch("api.app.push.fcm_configured", return_value=True):
            config = self.client.get(self.url).context["config"]
        ios = next(d for d in config["devices"] if d["platform"] == "ios")
        self.assertTrue(ios["mismatch"])
        self.assertEqual(ios["enabled"], 1)
        self.assertTrue(config["any_mismatch"])
        with mock.patch("api.app.push.apns_configured", return_value=True), mock.patch("api.app.push.fcm_configured", return_value=True), override_settings(VAPID_PRIVATE_KEY="k"):
            self.assertFalse(self.client.get(self.url).context["config"]["any_mismatch"])

    def test_query_count_is_bounded(self):
        for provider in monitoring.PROVIDERS:
            for _i in range(20):
                self.make(provider=provider, status=S.FAILED if _i % 3 == 0 else S.SENT, error="e")
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertLess(len(queries), 40)


class PushAlertBannerTests(ControlPanelTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.root = User.objects.create_superuser(email="super@example.com", password="pw-secret-123")
        enrol_mfa(cls.root)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.root)
        self.url = reverse("controlpanel:push_monitoring")

    def test_banner_and_badge_hidden_without_open_alerts(self):
        response = self.client.get(self.url)
        self.assertNotContains(response, 'data-testid="open-alerts"')
        self.assertNotContains(response, 'data-testid="push-alert-badge"')

    def test_banner_and_badge_shown_with_an_open_alert(self):
        from notifications.models import PushAlert

        PushAlert.objects.create(key="provider:apns", severity="critical", message="APNs (iOS) is down", notifications_sent=2)
        PushAlert.objects.create(key="provider:fcm", severity="critical", message="old one", resolved_at=timezone.now())
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="open-alerts"')
        self.assertContains(response, "APNs (iOS) is down")
        self.assertContains(response, "2 emails sent")
        self.assertNotContains(response, "old one")
        self.assertContains(response, 'data-testid="push-alert-badge"')
