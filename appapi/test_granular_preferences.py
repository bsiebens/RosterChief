"""Per-person notification switches, category metadata and quiet hours on the account plane."""

import datetime
from unittest import mock

from django.conf import settings
from django.utils import timezone

from members.models import Member
from notifications import preferences, quiet_hours
from notifications.models import QuietHours
from notifications.policy import android_channels

from .tests import AppApiTestCase

PATH = "/me/notification-preferences"
QUIET = "/me/quiet-hours"


class PersonPreferencesTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.login()

    def call(self, method, path=PATH, **kwargs):
        return self.api(method, path, token=self.token, **kwargs)

    def club_entry(self, body):
        return next(entry for entry in body["clubs"] if entry["club_slug"] == self.club.slug)

    def test_people_lists_self_and_child(self):
        entry = self.club_entry(self.call("get").json())

        self.assertEqual([(p["name"], p["is_self"]) for p in entry["people"]], [("Pat Parent", True), ("Kim Parent", False)])
        self.assertEqual(entry["people"][1]["effective"], entry["effective"])
        self.assertEqual(entry["people"][1]["overrides"], {})

    def test_categories_carry_description_and_android_fields(self):
        categories = self.call("get").json()["categories"]
        channels = {c["category"]: c for c in android_channels()}

        self.assertTrue(all(c["description"] for c in categories))
        for category in categories:
            self.assertEqual(category["android_channel_id"], channels[category["key"]]["id"])
            self.assertEqual(category["android_importance"], channels[category["key"]]["importance"])

    def test_person_patch_beats_club_row_and_only_touches_that_person(self):
        self.call("patch", data={"club_slug": self.club.slug, "changes": [{"category": "event", "push": True}]})
        response = self.call("patch", data={"club_slug": self.club.slug, "member_id": str(self.child.pk), "changes": [{"category": "event", "push": False}]})

        self.assertEqual(response.status_code, 200, response.content)
        entry = self.club_entry(response.json())
        me, kid = entry["people"]
        self.assertEqual(kid["overrides"], {"event": {"push": False}})
        self.assertFalse(kid["effective"]["event"]["push"])
        self.assertTrue(me["effective"]["event"]["push"])
        self.assertTrue(entry["effective"]["event"]["push"])
        self.assertFalse(preferences.matrix(self.user, self.club, self.child)["event"]["push"])

    def test_person_delete_only_resets_that_person(self):
        preferences.set_preference(self.user, self.club, "news", "push", False)
        preferences.set_preference(self.user, self.club, "news", "push", False, member=self.child)

        body = self.call("delete", f"{PATH}?club_slug={self.club.slug}&member_id={self.child.pk}").json()

        kid = self.club_entry(body)["people"][1]
        self.assertEqual(kid["overrides"], {})
        self.assertFalse(kid["effective"]["news"]["push"])  # the club row still applies
        self.assertEqual(self.club_entry(body)["overrides"], {"news": {"push": False}})

    def test_foreign_member_is_rejected_and_nothing_saved(self):
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger")

        response = self.call("patch", data={"club_slug": self.club.slug, "member_id": str(stranger.pk), "changes": [{"category": "news", "push": False}]})

        self.assertError(response, 422, "validation_error")
        self.assertIn("member_id", response.json()["error"]["fields"])
        self.assertFalse(preferences.overrides(self.user, self.club, stranger))
        self.assertError(self.call("delete", f"{PATH}?club_slug={self.club.slug}&member_id={stranger.pk}"), 422, "validation_error")

    def test_member_without_club_slug_is_rejected(self):
        response = self.call("patch", data={"club_slug": None, "member_id": str(self.child.pk), "changes": [{"category": "news", "push": False}]})
        self.assertError(response, 422, "validation_error")


class QuietHoursApiTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.login()

    def call(self, method, **kwargs):
        return self.api(method, QUIET, token=self.token, **kwargs)

    def put(self, **overrides):
        data = {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "Europe/Brussels", **overrides}
        return self.call("put", data=data)

    def test_requires_authentication(self):
        for method in ("get", "put", "delete"):
            self.assertError(self.api(method, QUIET, data={} if method == "put" else None), 401, "unauthenticated")

    def test_defaults(self):
        body = self.call("get").json()
        self.assertEqual(body, {"enabled": False, "start": "22:00", "end": "07:00", "timezone": settings.TIME_ZONE, "active_now": False, "until": None})

    def test_put_get_delete(self):
        response = self.put(start="23:30", end="06:15", timezone="America/New_York")
        self.assertEqual(response.status_code, 200, response.content)
        stored = QuietHours.objects.get(user=self.user)
        self.assertEqual((stored.start, stored.end, stored.timezone, stored.enabled), (datetime.time(23, 30), datetime.time(6, 15), "America/New_York", True))
        self.assertEqual(self.call("get").json()["start"], "23:30")

        self.assertEqual(self.call("delete").json()["enabled"], False)
        self.assertFalse(QuietHours.objects.filter(user=self.user).exists())
        self.assertEqual(self.call("delete").status_code, 200)

    def test_validation(self):
        for override, field in ({"start": "25:00"}, "start"), ({"end": "7am"}, "end"), ({"timezone": "Mars/Base"}, "timezone"), ({"timezone": ""}, "timezone"):
            response = self.put(**override)
            self.assertError(response, 422, "validation_error")
            self.assertIn(field, response.json()["error"]["fields"])
        self.assertFalse(QuietHours.objects.exists())

    def test_active_now_and_until(self):
        self.put()
        at_night = datetime.datetime(2026, 1, 15, 23, 0, tzinfo=datetime.UTC)  # 00:00 in Brussels
        with mock.patch.object(timezone, "now", return_value=at_night):
            body = self.call("get").json()
        self.assertTrue(body["active_now"])
        self.assertEqual(datetime.datetime.fromisoformat(body["until"]), datetime.datetime(2026, 1, 16, 6, 0, tzinfo=datetime.UTC))

        at_noon = datetime.datetime(2026, 1, 15, 11, 0, tzinfo=datetime.UTC)
        with mock.patch.object(timezone, "now", return_value=at_noon):
            body = self.call("get").json()
        self.assertFalse(body["active_now"])
        self.assertIsNone(body["until"])

    def test_disabled_is_never_active(self):
        self.put(enabled=False)
        at_night = datetime.datetime(2026, 1, 15, 23, 0, tzinfo=datetime.UTC)
        with mock.patch.object(timezone, "now", return_value=at_night):
            self.assertFalse(self.call("get").json()["active_now"])
        self.assertIsNone(quiet_hours.window_end(QuietHours.objects.get(user=self.user), at_night))
