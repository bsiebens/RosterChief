"""notify_user, the outbox and the Celery tasks."""

from datetime import timedelta
from unittest import mock

from django.utils import timezone

from club.models import Club

from . import outbox, tasks
from .models import NotificationDelivery, OutboxEntry
from .services import notify_user
from .tests_support import PushTestCase, make_device, make_user

Status = OutboxEntry.Status


class NotifyUserTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()

    def test_enqueues_in_the_callers_transaction_and_kicks_after_commit(self):
        with mock.patch("notifications.tasks.process_outbox.delay") as delay, self.captureOnCommitCallbacks(execute=True) as callbacks:
            entry = notify_user(self.user, title="Hi", body="There", category="news", priority="critical", data={"a": 1}, notification_id="6e2ee25e-5cd0-4c9b-aaf0-4a3cd2e66a9c", send_email=True)
            delay.assert_not_called()  # not before commit
        self.assertEqual(len(callbacks), 1)
        delay.assert_called_once_with()
        entry.refresh_from_db()
        self.assertEqual((entry.kind, entry.status, entry.attempts), ("notification", "pending", 0))
        self.assertLessEqual(entry.available_at, timezone.now())
        self.assertEqual(entry.payload["user_id"], str(self.user.pk))
        self.assertEqual((entry.payload["category"], entry.payload["priority"], entry.payload["send_email"]), ("news", "critical", True))

    def test_a_broker_problem_does_not_break_the_caller(self):
        with mock.patch("notifications.tasks.process_outbox.delay", side_effect=OSError("redis down")), self.captureOnCommitCallbacks(execute=True):
            notify_user(self.user, title="Hi", body="There")
        self.assertEqual(OutboxEntry.objects.count(), 1)

    def test_end_to_end_through_the_task(self):
        make_device(self.user, "ios")
        fake = self.use_gateway()
        club = Club(name="Sharks", schema_name="outbox_sharks", owner=self.user)
        club.auto_create_schema = False
        club.save()
        notify_user(self.user, title="Hi", body="There", club=club)
        tasks.process_outbox()
        entry = OutboxEntry.objects.get()
        self.assertEqual(entry.status, "done")
        (_target, message) = fake.calls[0]
        self.assertEqual((message.title, message.club_slug), ("Hi", club.slug))
        self.assertEqual(NotificationDelivery.objects.get().club, club)


class OutboxTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()

    def entry(self, **extra):
        return OutboxEntry.objects.create(kind="notification", payload={"user_id": str(self.user.pk), "title": "t", "body": "b"}, available_at=timezone.now() - timedelta(seconds=1), **extra)

    def test_claim_takes_only_due_pending_entries_once(self):
        due = self.entry()
        future = OutboxEntry.objects.create(payload={}, available_at=timezone.now() + timedelta(minutes=5))
        self.entry(status=Status.DONE)
        claimed = outbox.claim()
        self.assertEqual([e.pk for e in claimed], [due.pk])
        due.refresh_from_db()
        self.assertEqual((due.status, due.locked_at is not None), ("processing", True))
        self.assertEqual(outbox.claim(), [])
        future.refresh_from_db()
        self.assertEqual(future.status, "pending")

    def test_claim_respects_the_limit_and_order(self):
        first = OutboxEntry.objects.create(payload={}, available_at=timezone.now() - timedelta(minutes=2))
        self.entry()
        self.assertEqual([e.pk for e in outbox.claim(limit=1)], [first.pk])

    def test_success_marks_done(self):
        make_device(self.user)
        self.use_gateway()
        entry = self.entry()
        counts = outbox.process_outbox()
        entry.refresh_from_db()
        self.assertEqual((entry.status, counts["done"]), ("done", 1))

    def test_failure_is_retried_with_a_backoff_then_fails_after_the_max(self):
        entry = self.entry()
        self.settings.outbox_max_attempts = 3
        self.settings.save()
        with mock.patch.dict(outbox.HANDLERS, {"notification": mock.Mock(side_effect=RuntimeError("boom"))}):
            now = timezone.now()
            outbox.process_outbox(now=now)
            entry.refresh_from_db()
            self.assertEqual((entry.status, entry.attempts), ("pending", 1))
            self.assertIn("boom", entry.last_error)
            self.assertAlmostEqual(entry.available_at.timestamp(), (now + timedelta(minutes=1)).timestamp(), delta=5)
            self.assertEqual(outbox.process_outbox(now=now)["failed"], 0)  # not due yet: backoff holds
            outbox.process_outbox(now=now + timedelta(minutes=2))
            entry.refresh_from_db()
            self.assertEqual((entry.status, entry.attempts), ("pending", 2))
            self.assertAlmostEqual(entry.available_at.timestamp(), (now + timedelta(minutes=2, seconds=0) + timedelta(minutes=5)).timestamp(), delta=5)
            outbox.process_outbox(now=now + timedelta(minutes=10))
            entry.refresh_from_db()
            self.assertEqual((entry.status, entry.attempts), ("failed", 3))

    def test_entries_stuck_in_processing_are_recovered_and_count_as_an_attempt(self):
        entry = self.entry(status=Status.PROCESSING)
        OutboxEntry.objects.filter(pk=entry.pk).update(locked_at=timezone.now() - timedelta(minutes=6))
        fresh = self.entry(status=Status.PROCESSING)
        OutboxEntry.objects.filter(pk=fresh.pk).update(locked_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(outbox.recover_stuck(), 1)
        entry.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual((entry.status, entry.attempts, fresh.status), ("pending", 1, "processing"))
        self.assertIn("worker died", entry.last_error)

    def test_a_poison_entry_eventually_fails_through_recovery(self):
        entry = self.entry(status=Status.PROCESSING, attempts=self.settings.outbox_max_attempts - 1)
        OutboxEntry.objects.filter(pk=entry.pk).update(locked_at=timezone.now() - timedelta(minutes=6))
        outbox.recover_stuck()
        entry.refresh_from_db()
        self.assertEqual(entry.status, "failed")

    def test_unknown_account_is_skipped_quietly(self):
        entry = OutboxEntry.objects.create(payload={"user_id": "00000000-0000-0000-0000-000000000000", "title": "t", "body": "b"}, available_at=timezone.now())
        outbox.process_outbox()
        entry.refresh_from_db()
        self.assertEqual(entry.status, "done")

    def test_purge(self):
        old_done = self.entry(status=Status.DONE)
        old_failed = self.entry(status=Status.FAILED)
        keep = self.entry(status=Status.DONE)
        OutboxEntry.objects.filter(pk=old_done.pk).update(modified=timezone.now() - timedelta(days=4))
        OutboxEntry.objects.filter(pk=old_failed.pk).update(modified=timezone.now() - timedelta(days=31))
        self.assertEqual(outbox.purge(), 2)
        self.assertEqual(list(OutboxEntry.objects.values_list("pk", flat=True)), [keep.pk])


class TaskTests(PushTestCase):
    def test_task_names_and_beat_entries(self):
        from rosterchief.celery import app

        names = {"notifications.process_outbox", "notifications.retry_failed_deliveries", "notifications.check_push_health", "notifications.cleanup"}
        self.assertEqual({tasks.process_outbox.name, tasks.retry_failed_deliveries.name, tasks.check_push_health.name, tasks.cleanup.name}, names)
        scheduled = {entry["task"] for entry in app.conf.beat_schedule.values()}
        self.assertTrue(names <= scheduled)

    def test_tasks_run(self):
        self.assertIn("outbox", tasks.process_outbox())
        self.assertIn("retry", tasks.retry_failed_deliveries())
        self.assertIn("alerts", tasks.check_push_health())
        self.assertIn("cleanup", tasks.cleanup())
