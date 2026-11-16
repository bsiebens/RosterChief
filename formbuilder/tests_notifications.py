"""Form notifications: announcing a send (audience, guardians, language, dedupe, scheduled opening), the reminder sweep."""

import datetime
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from formbuilder import tasks
from formbuilder.models import Field, Form, FormSend
from formbuilder.services import notifications
from formbuilder.services.submission import submit_form
from news.tests import NewsClubMixin
from notifications.models import InboxNotification

H = datetime.timedelta(hours=1)


class FormNotificationTests(NewsClubMixin, TestCase):
    def make_send(self, *, teams=(), club_wide=False, **kw):
        form = Form.objects.create(title="Allergies")
        Field.objects.create(form=form, label="Notes", required=False)
        send = FormSend.objects.create(form=form, club_wide=club_wide, **kw)
        send.teams.set(teams)
        return send

    def notes(self, user):
        return list(InboxNotification.objects.filter(user=user))

    def test_audience_guardians_language_and_link(self):
        send = self.make_send(teams=[self.team], closes_at=timezone.now() + 5 * 24 * H)
        self.assertEqual(notifications.notify_now(send.pk), 4)  # ann, cara, dan (rostered), parent (once for two kids)
        self.assertEqual(self.notes(self.bob_user), [])  # not on the team
        ann, cara, parent = (self.notes(u) for u in (self.ann_user, self.cara_user, self.parent_user))
        self.assertEqual([len(x) for x in (ann, cara, parent)], [1, 1, 1])
        self.assertEqual(ann[0].title, "Nieuw formulier: Allergies")  # club language by default
        self.assertEqual(cara[0].title, "New form: Allergies")  # her own language
        self.assertEqual(ann[0].href, f"/forms/{send.pk}/fill")
        self.assertTrue(parent[0].title.startswith("Kid"))  # sent on a child's behalf
        send.refresh_from_db()
        self.assertIsNotNone(send.notified_at)

    def test_club_wide_and_public_link_in_href(self):
        send = self.make_send(club_wide=True, is_public=True)
        notifications.notify_now(send.pk)
        self.assertEqual(self.notes(self.bob_user)[0].href, f"/f/{send.public_token}")
        self.assertEqual(self.notes(self.dan_user), [])  # lapsed

    def test_open_ended_body(self):
        send = self.make_send(club_wide=True)
        notifications.notify_now(send.pk)
        self.assertEqual(self.notes(self.cara_user)[0].body, "Please fill it in when you have a moment.")

    def test_public_only_without_audience_tells_nobody(self):
        send = self.make_send(is_public=True)
        self.assertEqual(notifications.notify_now(send.pk), 0)
        self.assertFalse(InboxNotification.objects.exists())
        send.refresh_from_db()
        self.assertIsNotNone(send.notified_at)

    def test_never_twice(self):
        send = self.make_send(club_wide=True)
        notifications.notify_now(send.pk)
        self.assertEqual(notifications.notify_now(send.pk), 0)
        self.assertEqual(len(self.notes(self.ann_user)), 1)

    def test_scheduled_waits_for_opens_at_and_the_sweep_delivers(self):
        send = self.make_send(club_wide=True, opens_at=timezone.now() + 2 * H)
        self.assertEqual(notifications.notify_now(send.pk), 0)
        self.assertEqual(notifications.notify_due()["told"], 0)
        send.refresh_from_db()
        self.assertIsNone(send.notified_at)
        FormSend.objects.filter(pk=send.pk).update(opens_at=timezone.now() - H)
        self.assertGreater(notifications.notify_due()["told"], 0)
        self.assertEqual(notifications.notify_due()["told"], 0)  # idempotent
        self.assertEqual(len(self.notes(self.ann_user)), 1)

    def test_inactive_or_closed_sends_are_not_announced(self):
        for kw in ({"is_active": False}, {"closes_at": timezone.now() - H}):
            send = self.make_send(club_wide=True, **kw)
            self.assertEqual(notifications.notify_now(send.pk), 0)
        self.assertFalse(InboxNotification.objects.exists())

    def test_failure_releases_the_claim(self):
        send = self.make_send(club_wide=True)
        with mock.patch.object(notifications, "_deliver", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                notifications.notify_now(send.pk)
        send.refresh_from_db()
        self.assertIsNone(send.notified_at)

    def test_dispatch_queues_a_task_and_swallows_broker_errors(self):
        with mock.patch.object(tasks.notify_send, "delay") as delay:
            notifications.dispatch_send_notification("abc")
        delay.assert_called_once_with("abc")
        with mock.patch.object(tasks.notify_send, "delay", side_effect=OSError("broker down")), self.assertLogs("formbuilder.services.notifications", "ERROR"):
            notifications.dispatch_send_notification("abc")  # logged, not raised

    def test_notify_false_at_creation_marks_the_send_handled(self):
        from formbuilder.services import management

        with mock.patch("members.services.access.require_manage_forms"):
            form = Form.objects.create(title="X")
            send = management.create_send(self.owner, form, club_wide=True, notify=False)
        self.assertIsNotNone(send.notified_at)
        self.assertEqual(notifications.notify_due()["sends"], 0)

    # --- reminders ----------------------------------------------------------------------------------------------

    def test_reminder_selection_and_one_time_marking(self):
        now = timezone.now()
        due = self.make_send(teams=[self.team], closes_at=now + 30 * H)
        too_soon = self.make_send(teams=[self.team], closes_at=now + 10 * H)
        too_far = self.make_send(teams=[self.team], closes_at=now + 60 * H)
        no_deadline = self.make_send(teams=[self.team])
        off = self.make_send(teams=[self.team], closes_at=now + 30 * H, is_active=False)
        done = self.make_send(teams=[self.team], closes_at=now + 30 * H, reminder_sent_at=now - H)
        with mock.patch("formbuilder.services.notifications._current_club", return_value=self.club):
            counts = notifications.remind_due(now)
        self.assertEqual(counts["sends"], 1)
        for send in (too_soon, too_far, no_deadline, off):
            send.refresh_from_db()
            self.assertIsNone(send.reminder_sent_at)
        due.refresh_from_db()
        self.assertIsNotNone(due.reminder_sent_at)
        done.refresh_from_db()
        self.assertEqual(done.reminder_sent_at, now - H)
        self.assertEqual(counts["told"], len(InboxNotification.objects.all()))
        self.assertEqual(notifications.remind_due(now)["sends"], 0)  # only once

    def test_reminder_only_reaches_those_who_have_not_responded(self):
        now = timezone.now()
        send = self.make_send(teams=[self.team], closes_at=now + 30 * H)
        submit_form(send, self.ann, {})
        with mock.patch("formbuilder.services.notifications._current_club", return_value=self.club):
            notifications.remind_due(now)
        self.assertEqual(self.notes(self.ann_user), [])
        self.assertEqual(self.notes(self.cara_user)[0].title, "Reminder: Allergies")
        self.assertEqual(len(self.notes(self.parent_user)), 1)  # kids have not responded

    def test_reminder_with_nobody_left_still_marks(self):
        now = timezone.now()
        send = self.make_send(is_public=True, closes_at=now + 30 * H)
        notifications.remind_due(now)
        send.refresh_from_db()
        self.assertIsNotNone(send.reminder_sent_at)

    def test_reminder_task_is_registered_hourly(self):
        from rosterchief.celery import app

        self.assertEqual(app.conf.beat_schedule["formbuilder-send-form-reminders"]["task"], "formbuilder.send_form_reminders")
        with mock.patch.object(notifications, "send_form_reminders", return_value={"sends": 1, "told": 2, "failed": []}):
            self.assertEqual(tasks.send_form_reminders(), "reminded 2 account(s) for 1 form send(s)")
