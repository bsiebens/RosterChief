"""Offline-tolerant coach entry: Idempotency-Key replays on the attendance / line-up saves, the
service worker's coach page cache opt-in, and the queue script wiring."""

from unittest import mock

from django.test import override_settings
from django.urls import reverse

from api.models import IdempotencyRecord
from events.models import Attendance, LineupSelection
from notifications.models import Notification

from .test_coach_ops import HOST, HTMX, CoachOpsBase, Event, setUpModule, tearDownModule  # noqa: F401


def key_headers(key):
    return {"HTTP_IDEMPOTENCY_KEY": key, **HTMX}


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class AttendanceIdempotencyTests(CoachOpsBase):
    def check_in_url(self):
        return reverse("mobile:coach_attendance_check_in", kwargs={"event_id": self.event.pk, "attendance_id": self.attendance.pk})

    def edit_url(self):
        return reverse("mobile:coach_attendance_edit", kwargs={"event_id": self.event.pk, "attendance_id": self.attendance.pk})

    def test_a_replayed_check_in_does_not_toggle_back(self):
        self.client.force_login(self.user)

        first = self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST, **key_headers("k-check-1"))
        replay = self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST, **key_headers("k-check-1"))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.attendance.refresh_from_db()
        self.assertTrue(self.attendance.showed_up)
        self.assertContains(replay, 'aria-pressed="true"')

    def test_a_new_key_is_a_new_action(self):
        self.client.force_login(self.user)
        self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST, **key_headers("k-a"))

        self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST, **key_headers("k-b"))

        self.attendance.refresh_from_db()
        self.assertIsNone(self.attendance.showed_up)

    def test_without_a_key_nothing_is_stored(self):
        self.client.force_login(self.user)

        self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST, **HTMX)

        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_the_edit_replay_keeps_the_toast_and_never_stores_the_private_note(self):
        self.client.force_login(self.user)

        self.client.post(self.edit_url(), {"status": "absent", "coach_note": "Sensitive knee note"}, HTTP_HOST=HOST, **key_headers("k-edit"))
        sent = Notification.objects.count()
        replay = self.client.post(self.edit_url(), {"status": "absent", "coach_note": "Sensitive knee note", "notify": "on"}, HTTP_HOST=HOST, **key_headers("k-edit"))
        self.assertEqual(Notification.objects.count(), sent)

        self.assertContains(replay, "Answer saved")
        record = IdempotencyRecord.objects.get(user=self.user, key="k-edit")
        self.assertNotIn("Sensitive knee note", str(record.body))

    def test_a_refused_save_releases_the_key_and_is_tagged(self):
        self.client.force_login(self.user)

        response = self.client.post(self.edit_url(), {"status": "absent", "coach_note": "x" * 600}, HTTP_HOST=HOST, **key_headers("k-bad"))

        self.assertEqual(response["X-Offline-Result"], "error")
        self.assertFalse(IdempotencyRecord.objects.filter(key="k-bad").exists())
        fixed = self.client.post(self.edit_url(), {"status": "absent", "coach_note": "short"}, HTTP_HOST=HOST, **key_headers("k-bad"))
        self.assertNotIn("X-Offline-Result", fixed)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.ABSENT)

    def test_a_key_cannot_be_reused_for_another_url(self):
        self.client.force_login(self.user)
        self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST, **key_headers("k-same"))

        response = self.client.post(self.edit_url(), {"status": "absent"}, HTTP_HOST=HOST, **key_headers("k-same"))

        self.assertEqual(response.status_code, 422)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.PRESENT)

    def test_a_non_manager_is_still_forbidden_with_a_key(self):
        self.client.force_login(self.physio())

        response = self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST, **key_headers("k-physio"))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(IdempotencyRecord.objects.exists())


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class LineupIdempotencyTests(CoachOpsBase):
    event_kind = Event.EventKind.GAME

    def toggle_url(self):
        return reverse("mobile:coach_lineup_toggle", kwargs={"event_id": self.event.pk, "member_id": self.player.pk})

    def publish_url(self):
        return reverse("mobile:coach_lineup_publish", kwargs={"event_id": self.event.pk})

    def test_a_replayed_toggle_keeps_the_pick(self):
        self.client.force_login(self.user)

        self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **key_headers("k-t"))
        replay = self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **key_headers("k-t"))

        self.assertEqual(replay.status_code, 200)
        self.assertTrue(LineupSelection.objects.filter(lineup__event=self.event, member=self.player).exists())

    def test_a_replayed_publish_runs_and_notifies_once(self):
        self.client.force_login(self.user)
        self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)

        with mock.patch("mobile.coach_views.publish_lineup") as publish:
            first = self.client.post(self.publish_url(), HTTP_HOST=HOST, **key_headers("k-pub"))
            replay = self.client.post(self.publish_url(), HTTP_HOST=HOST, **key_headers("k-pub"))

        self.assertEqual(publish.call_count, 1)
        self.assertContains(first, "1 player was told.")
        self.assertContains(replay, "1 player was told.")

    def test_a_refused_publish_does_not_consume_the_key(self):
        self.client.force_login(self.user)
        self.client.post(self.toggle_url(), {"selected": "false"}, HTTP_HOST=HOST, **HTMX)

        refused = self.client.post(self.publish_url(), HTTP_HOST=HOST, **key_headers("k-pub2"))
        self.assertContains(refused, "Pick at least one player first.")
        self.assertEqual(refused["X-Offline-Result"], "error")

        self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)
        ok = self.client.post(self.publish_url(), HTTP_HOST=HOST, **key_headers("k-pub2"))
        self.assertContains(ok, "Published")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class OfflineCacheOptInTests(CoachOpsBase):
    def attendance_url(self):
        return reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk})

    def test_attendance_without_private_notes_may_be_saved_offline(self):
        self.client.force_login(self.user)

        response = self.client.get(self.attendance_url(), HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Offline-Cache"], "1")
        self.assertIn("no-store", response["Cache-Control"])

    def test_attendance_with_a_private_note_is_never_offered_for_saving(self):
        Attendance.objects.filter(pk=self.attendance.pk).update(coach_note="Private")
        self.client.force_login(self.user)

        response = self.client.get(self.attendance_url(), HTTP_HOST=HOST)

        self.assertNotIn("X-Offline-Cache", response)

    def test_inline_and_write_responses_are_never_offered(self):
        self.client.force_login(self.user)

        response = self.client.get(self.attendance_url(), HTTP_HOST=HOST, **HTMX)

        self.assertNotIn("X-Offline-Cache", response)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class ServiceWorkerAndShellTests(CoachOpsBase):
    def test_service_worker_has_the_coach_cache_and_the_new_version(self):
        body = self.client.get("/app/sw.js", HTTP_HOST=HOST).content.decode()

        self.assertIn("rosterchief-shell-v5", body)
        self.assertIn("rosterchief-coach-pages-v1", body)
        self.assertIn("Offline: showing saved version", body)
        self.assertIn("X-Offline-Cache", body)
        self.assertIn("deleteDatabase", body)
        self.assertNotIn("{%", body)
        self.assertNotIn("{{", body)

    def test_the_coach_shell_loads_the_queue_script_tagged_with_the_user(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_today"), HTTP_HOST=HOST)

        self.assertContains(response, "js/mobile-offline-queue.js")
        self.assertContains(response, f'data-user="{self.user.pk}"')
