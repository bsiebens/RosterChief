from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from club.models import Club
from features.jobs import JOB_REGISTRY
from members.models import Member
from notifications import outbox
from notifications.models import Notification, OutboxEntry

Kind = OutboxEntry.Kind
Status = OutboxEntry.Status


def age(entry, **fields):
    """Rewrites timestamps the model sets itself (created/modified are auto)."""
    OutboxEntry.objects.filter(pk=entry.pk).update(**fields)
    entry.refresh_from_db()
    return entry


class OutboxTestCase(TestCase):
    def setUp(self):
        self.calls = []

        def handler(payload):
            self.calls.append(payload)
            if self.fail:
                raise RuntimeError("boom")

        self.fail = False
        patcher = patch.dict(outbox.HANDLERS, {Kind.NOTIFICATIONS: f"{__name__}.recording", Kind.GAME_MOMENTS: f"{__name__}.recording"})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch(f"{__name__}.recording", handler, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def entry(self, **kwargs):
        return OutboxEntry.objects.create(kind=kwargs.pop("kind", Kind.NOTIFICATIONS), payload={"n": 1}, **kwargs)


class ClaimAndProcessTests(OutboxTestCase):
    def test_claim_is_exclusive(self):
        entry = self.entry()
        self.assertTrue(outbox.claim(entry))
        self.assertFalse(outbox.claim(entry))
        entry.refresh_from_db()
        self.assertEqual(entry.status, Status.PROCESSING)
        self.assertIsNotNone(entry.locked_at)

    def test_a_not_yet_due_entry_cannot_be_claimed(self):
        entry = self.entry(available_at=timezone.now() + timedelta(minutes=1))
        self.assertFalse(outbox.claim(entry))

    def test_process_runs_the_handler_and_marks_done(self):
        entry = self.entry()
        self.assertTrue(outbox.process(entry.pk))
        entry.refresh_from_db()
        self.assertEqual((entry.status, entry.locked_at), (Status.DONE, None))
        self.assertEqual(self.calls, [{"n": 1}])

    def test_no_double_send_when_already_done(self):
        entry = self.entry()
        outbox.process(entry.pk)
        self.assertFalse(outbox.process(entry.pk))
        self.assertEqual(len(self.calls), 1)

    def test_failure_backs_off_1_5_30_minutes_then_fails_after_five_attempts(self):
        self.fail = True
        entry = self.entry()
        expected = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(minutes=30)]
        for attempt, delay in enumerate(expected, start=1):
            before = timezone.now()
            self.assertFalse(outbox.process(entry.pk))
            entry.refresh_from_db()
            self.assertEqual((entry.status, entry.attempts), (Status.PENDING, attempt))
            self.assertIn("boom", entry.last_error)
            self.assertAlmostEqual((entry.available_at - before).total_seconds(), delay.total_seconds(), delta=5)
            # Not due yet, so a second try right away is refused ...
            self.assertFalse(outbox.process(entry.pk))
            self.assertEqual(len(self.calls), attempt)
            # ... until the backoff has passed.
            age(entry, available_at=timezone.now())
        self.assertFalse(outbox.process(entry.pk))
        entry.refresh_from_db()
        self.assertEqual((entry.status, entry.attempts), (Status.FAILED, 5))
        self.assertFalse(outbox.process(entry.pk))
        self.assertEqual(len(self.calls), 5)


class RecoverTests(OutboxTestCase):
    def test_an_entry_stuck_in_processing_is_retried(self):
        entry = self.entry()
        outbox.claim(entry)
        age(entry, locked_at=timezone.now() - timedelta(minutes=6))

        self.assertEqual(outbox.recover(), 0)  # back to pending with a backoff ...
        entry.refresh_from_db()
        self.assertEqual((entry.status, entry.attempts), (Status.PENDING, 1))
        age(entry, available_at=timezone.now(), created=timezone.now() - timedelta(minutes=10))
        self.assertEqual(outbox.recover(), 1)  # ... then processed
        entry.refresh_from_db()
        self.assertEqual(entry.status, Status.DONE)
        self.assertEqual(len(self.calls), 1)

    def test_a_recently_claimed_entry_is_left_alone(self):
        entry = self.entry()
        outbox.claim(entry)
        self.assertEqual(outbox.recover(), 0)
        entry.refresh_from_db()
        self.assertEqual((entry.status, entry.attempts), (Status.PROCESSING, 0))

    def test_the_sweeper_gives_a_fresh_entry_30_seconds_for_the_post_commit_thread(self):
        fresh = self.entry()
        old = age(self.entry(), created=timezone.now() - timedelta(seconds=45))

        self.assertEqual(outbox.recover(), 1)
        fresh.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual((fresh.status, old.status), (Status.PENDING, Status.DONE))

    def test_purge_keeps_recent_entries_and_drops_old_done_and_failed_ones(self):
        now = timezone.now()
        old_done = age(self.entry(status=Status.DONE), modified=now - timedelta(days=4))
        new_done = age(self.entry(status=Status.DONE), modified=now - timedelta(days=2))
        old_failed = age(self.entry(status=Status.FAILED), modified=now - timedelta(days=31))
        new_failed = age(self.entry(status=Status.FAILED), modified=now - timedelta(days=29))
        pending = age(self.entry(), created=now - timedelta(days=40), modified=now - timedelta(days=40))

        self.assertEqual(outbox.purge(), 2)
        self.assertEqual(set(OutboxEntry.objects.values_list("pk", flat=True)), {new_done.pk, new_failed.pk, pending.pk})
        self.assertNotIn(old_done.pk, OutboxEntry.objects.values_list("pk", flat=True))
        self.assertNotIn(old_failed.pk, OutboxEntry.objects.values_list("pk", flat=True))

    def test_the_job_command_recovers_and_purges(self):
        age(self.entry(), created=timezone.now() - timedelta(minutes=2))
        age(self.entry(status=Status.DONE), modified=timezone.now() - timedelta(days=5))
        out = StringIO()
        call_command("process_outbox", stdout=out)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(JOB_REGISTRY["notifications.tasks.process_outbox"]["command"], "process_outbox")
        self.assertFalse(OutboxEntry.objects.filter(status=Status.DONE, modified__lt=timezone.now() - timedelta(days=3)).exists())


class KickTests(OutboxTestCase):
    @override_settings(NOTIFICATIONS_DISPATCH_EAGER=True)
    def test_eager_kick_runs_inline(self):
        entry = outbox.enqueue(Kind.NOTIFICATIONS, {"n": 1})
        outbox.kick([entry.pk])
        self.assertEqual(len(self.calls), 1)
        entry.refresh_from_db()
        self.assertEqual(entry.status, Status.DONE)

    @override_settings(NOTIFICATIONS_DISPATCH_EAGER=False)
    def test_the_entry_is_written_at_once_and_processed_only_after_commit(self):
        with patch("notifications.outbox.run_in_background", side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)) as background:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                entry = outbox.enqueue(Kind.NOTIFICATIONS, {"n": 1})
                outbox.kick([entry.pk])
                self.assertEqual(OutboxEntry.objects.get().status, Status.PENDING)
            background.assert_not_called()
            self.assertEqual(self.calls, [])
            callbacks[0]()
        self.assertEqual(len(self.calls), 1)
        entry.refresh_from_db()
        self.assertEqual(entry.status, Status.DONE)


class ScheduleNotificationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Pat", last_name="Parent")

    def test_schedule_notifications_writes_an_entry_with_the_ids_and_email_flag(self):
        from notifications.dispatch import schedule_notifications

        notification = Notification.objects.create(club=self.club, member=self.member, title="Hi", body="…")
        with override_settings(NOTIFICATIONS_DISPATCH_EAGER=False), patch("notifications.outbox.run_in_background"):
            schedule_notifications([notification], email=False)
        entry = OutboxEntry.objects.get()
        self.assertEqual((entry.kind, entry.payload), (Kind.NOTIFICATIONS, {"ids": [str(notification.pk)], "email": False}))

    def test_the_default_handler_dispatches_the_stored_notifications(self):
        notification = Notification.objects.create(club=self.club, member=self.member, title="Hi", body="…")
        entry = outbox.enqueue(Kind.NOTIFICATIONS, {"ids": [str(notification.pk)], "email": False})
        with patch("notifications.dispatch.dispatch_notifications") as dispatch:
            self.assertTrue(outbox.process(entry.pk))
        dispatch.assert_called_once_with([str(notification.pk)], email=False)


class RollbackTests(TestCase):
    """The entry lives in the caller's transaction: rolled back with it, and only kicked off after commit
    (``captureOnCommitCallbacks`` stands in for the real commit; TransactionTestCase cannot flush on PostgreSQL here)."""

    @override_settings(NOTIFICATIONS_DISPATCH_EAGER=False)
    def test_the_entry_rolls_back_with_its_transaction(self):
        with patch("notifications.outbox.run_in_background") as background, self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    entry = outbox.enqueue(Kind.NOTIFICATIONS, {"n": 1})
                    outbox.kick([entry.pk])
                    raise RuntimeError("abort")
            except RuntimeError:
                pass
        self.assertFalse(OutboxEntry.objects.exists())
        background.assert_not_called()

    @override_settings(NOTIFICATIONS_DISPATCH_EAGER=False)
    def test_committing_runs_it_in_the_background(self):
        with patch("notifications.outbox.run_in_background") as background, self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                entry = outbox.enqueue(Kind.NOTIFICATIONS, {"n": 1})
                outbox.kick([entry.pk])
                background.assert_not_called()
        background.assert_called_once_with(outbox._process_all, [entry.pk])
        self.assertEqual(OutboxEntry.objects.count(), 1)
