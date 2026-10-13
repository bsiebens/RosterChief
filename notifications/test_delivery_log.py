from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from club.models import Club
from notifications import delivery_log, dispatch
from notifications.dispatch import Channel
from notifications.models import NotificationDelivery
from rosterchief.test_support import enrol_mfa

Status = NotificationDelivery.Status


class FakeChannel(Channel):
    name = "native"

    def __init__(self, action="sent"):
        self.action = action
        self.retried = []

    def retry(self, delivery):
        self.retried.append(delivery.pk)
        if self.action == "raise":
            raise RuntimeError("boom")
        if self.action == "sent":
            delivery_log.mark_sent(delivery)


class DeliveryLogTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def make(self, status=Status.RETRYING, *, next_attempt_at=None, created_ago=None, channel="native", attempts=1):
        delivery = NotificationDelivery.objects.create(club=self.club, channel=channel, target="t", status=status, attempts=attempts, next_attempt_at=next_attempt_at, payload={"a": 1})
        if created_ago is not None:
            NotificationDelivery.objects.filter(pk=delivery.pk).update(created=timezone.now() - created_ago)
        return delivery


class RecordTests(DeliveryLogTestCase):
    def test_retry_outcome_keeps_payload_and_schedules(self):
        before = timezone.now()
        delivery = delivery_log.record(club=self.club, channel="native", target="abc", outcome="retry", error="503", payload={"x": 1})
        self.assertEqual(delivery.status, Status.RETRYING)
        self.assertEqual(delivery.payload, {"x": 1})
        self.assertEqual(delivery.attempts, 1)
        self.assertEqual(delivery.last_error, "503")
        self.assertAlmostEqual(delivery.next_attempt_at, before + timedelta(minutes=1), delta=timedelta(seconds=10))

    def test_other_outcomes_clear_payload_and_schedule(self):
        for outcome, status in [("sent", Status.SENT), ("failed", Status.FAILED), ("dead", Status.DEAD)]:
            delivery = delivery_log.record(club=self.club, channel="web", target="abc", outcome=outcome, payload={"x": 1})
            self.assertEqual(delivery.status, status)
            self.assertEqual(delivery.payload, {})
            self.assertIsNone(delivery.next_attempt_at)

    def test_never_raises_on_bad_input(self):
        self.assertIsNone(delivery_log.record(club=self.club, channel="native", target="abc", outcome="nonsense"))
        self.assertIsNone(delivery_log.record(club=self.club, channel="native", target="abc", outcome="retry", payload=object()))
        self.assertIsNone(delivery_log.record(club="not a club", channel="native", target="abc", outcome="sent"))

    def test_long_values_truncated(self):
        delivery = delivery_log.record(club=self.club, channel="email", target="a" * 400, outcome="failed", error="e" * 900)
        self.assertEqual(len(delivery.target), 255)
        self.assertEqual(len(delivery.last_error), 500)


class MarkTests(DeliveryLogTestCase):
    def test_backoff_schedule_then_failed(self):
        delivery = delivery_log.record(club=self.club, channel="native", target="t", outcome="retry", payload={"x": 1})
        expected = [timedelta(minutes=5), timedelta(minutes=30), timedelta(minutes=30)]
        for attempts, delay in zip([2, 3, 4], expected, strict=True):
            now = timezone.now()
            delivery_log.mark_failed(delivery, "again")
            delivery.refresh_from_db()
            self.assertEqual(delivery.status, Status.RETRYING)
            self.assertEqual(delivery.attempts, attempts)
            self.assertAlmostEqual(delivery.next_attempt_at, now + delay, delta=timedelta(seconds=10))
            self.assertEqual(delivery.payload, {"x": 1})
        self.assertEqual(delivery_log.MAX_ATTEMPTS, 4)
        delivery_log.mark_failed(delivery, "last")
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.FAILED)
        self.assertEqual(delivery.payload, {})
        self.assertIsNone(delivery.next_attempt_at)

    def test_non_retryable_fails_at_once(self):
        delivery = self.make(next_attempt_at=timezone.now())
        delivery_log.mark_failed(delivery, "refused", retryable=False)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.FAILED)
        self.assertEqual(delivery.last_error, "refused")
        self.assertEqual(delivery.payload, {})

    def test_mark_sent_and_dead_clear_payload(self):
        sent = self.make(next_attempt_at=timezone.now())
        delivery_log.mark_sent(sent)
        sent.refresh_from_db()
        self.assertEqual((sent.status, sent.payload, sent.next_attempt_at), (Status.SENT, {}, None))
        dead = self.make(next_attempt_at=timezone.now())
        delivery_log.mark_dead(dead, "unregistered")
        dead.refresh_from_db()
        self.assertEqual((dead.status, dead.payload, dead.last_error), (Status.DEAD, {}, "unregistered"))


class RetryDueTests(DeliveryLogTestCase):
    def setUp(self):
        self._saved = dispatch.channels()

    def tearDown(self):
        dispatch._channels[:] = self._saved

    def test_only_due_retrying_rows_go_to_matching_channel(self):
        fake = FakeChannel()
        dispatch._channels[:] = [fake]
        past = timezone.now() - timedelta(minutes=1)
        due = self.make(next_attempt_at=past)
        self.make(next_attempt_at=timezone.now() + timedelta(hours=1))
        self.make(Status.SENT)
        self.make(Status.FAILED)
        self.make(next_attempt_at=past, channel="web")
        # the web row has no registered channel: it fails, it is not retried
        self.assertEqual(delivery_log.retry_due(), 1)
        self.assertEqual(fake.retried, [due.pk])
        due.refresh_from_db()
        self.assertEqual(due.status, Status.SENT)

    def test_unknown_channel_fails(self):
        dispatch._channels[:] = []
        delivery = self.make(next_attempt_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(delivery_log.retry_due(), 0)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.FAILED)
        self.assertEqual(delivery.payload, {})

    def test_raising_retry_is_backed_off(self):
        dispatch.register_channel(FakeChannel("raise"))
        delivery = self.make(next_attempt_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(delivery_log.retry_due(), 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Status.RETRYING)
        self.assertEqual(delivery.attempts, 2)
        self.assertEqual(delivery.last_error, "boom")
        self.assertGreater(delivery.next_attempt_at, timezone.now() + timedelta(minutes=4))


class PurgeTests(DeliveryLogTestCase):
    def test_retention(self):
        old_sent = self.make(Status.SENT, created_ago=timedelta(days=15))
        new_sent = self.make(Status.SENT, created_ago=timedelta(days=13))
        old_failed = self.make(Status.FAILED, created_ago=timedelta(days=61))
        new_failed = self.make(Status.FAILED, created_ago=timedelta(days=59))
        old_dead = self.make(Status.DEAD, created_ago=timedelta(days=61))
        retrying = self.make(Status.RETRYING, created_ago=timedelta(days=400), next_attempt_at=timezone.now() + timedelta(days=1))
        self.assertEqual(delivery_log.purge(), 3)
        remaining = set(NotificationDelivery.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {new_sent.pk, new_failed.pk, retrying.pk})
        self.assertFalse({old_sent.pk, old_failed.pk, old_dead.pk} & remaining)


class CommandAndAdminTests(DeliveryLogTestCase):
    def test_command_end_to_end(self):
        saved = dispatch.channels()
        self.addCleanup(lambda: dispatch._channels.__setitem__(slice(None), saved))
        dispatch.register_channel(FakeChannel())
        due = self.make(next_attempt_at=timezone.now() - timedelta(minutes=1))
        old = self.make(Status.SENT, created_ago=timedelta(days=20))
        out = StringIO()
        call_command("retry_failed_deliveries", stdout=out)
        due.refresh_from_db()
        self.assertEqual(due.status, Status.SENT)
        self.assertFalse(NotificationDelivery.objects.filter(pk=old.pk).exists())

    def test_job_registered(self):
        from features.jobs import JOB_REGISTRY

        self.assertEqual(JOB_REGISTRY["notifications.tasks.retry_failed_deliveries"]["command"], "retry_failed_deliveries")

    def test_admin_changelist_loads_read_only(self):
        admin_user = get_user_model().objects.create_superuser(email="root@example.com", password="pw")
        enrol_mfa(admin_user)
        self.client.force_login(admin_user)
        delivery = self.make(next_attempt_at=timezone.now())
        response = self.client.get(reverse("admin:notifications_notificationdelivery_changelist"), HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:notifications_notificationdelivery_add"), HTTP_HOST="localhost").status_code, 403)
        self.assertContains(self.client.get(reverse("admin:notifications_notificationdelivery_change", args=[delivery.pk]), HTTP_HOST="localhost"), "t")
