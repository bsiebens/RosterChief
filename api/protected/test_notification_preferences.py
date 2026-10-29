"""The account plane's /me/notification-preferences: per-category push/email switches, all-clubs and per club."""

from authentication.models import User
from club.models import Club
from notifications import preferences
from notifications.models import NotificationCategory, NotificationPreference
from rosterchief.test_support import make_club

from .tests import AppApiTestCase

PATH = "/me/notification-preferences"


class NotificationPreferencesApiTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.login()

    def call(self, method, path=PATH, **kwargs):
        return self.api(method, path, token=self.token, **kwargs)

    def club_entry(self, body):
        return next(entry for entry in body["clubs"] if entry["club_slug"] == self.club.slug)

    def test_requires_authentication(self):
        for method in ("get", "patch", "delete"):
            self.assertError(self.api(method, PATH, data={"club_slug": None, "changes": []} if method == "patch" else None), 401, "unauthenticated")

    def test_shape_lists_every_category_and_everything_on(self):
        response = self.call("get")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual([c["key"] for c in body["categories"]], list(NotificationCategory.values))
        self.assertTrue(all(c["label"] for c in body["categories"]))
        self.assertEqual(set(body["defaults"]), set(NotificationCategory.values))
        self.assertTrue(all(channels == {"push": True, "email": True} for channels in body["defaults"].values()))
        entry = self.club_entry(body)
        self.assertEqual((entry["club_name"], entry["overrides"]), (self.club.name, {}))
        self.assertEqual(entry["effective"], body["defaults"])

    def test_global_change_then_club_override_and_effective(self):
        response = self.call("patch", data={"club_slug": None, "changes": [{"category": "news", "push": False}]})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["defaults"]["news"], {"push": False, "email": True})
        self.assertEqual(self.club_entry(body)["effective"]["news"], {"push": False, "email": True})

        body = self.call("patch", data={"club_slug": self.club.slug, "changes": [{"category": "news", "push": True}, {"category": "shop", "email": False}]}).json()

        self.assertEqual(body["defaults"]["news"], {"push": False, "email": True})
        entry = self.club_entry(body)
        self.assertEqual(entry["overrides"], {"news": {"push": True}, "shop": {"email": False}})
        self.assertEqual(entry["effective"]["news"], {"push": True, "email": True})
        self.assertEqual(entry["effective"]["shop"], {"push": True, "email": False})

    def test_patch_is_idempotent(self):
        data = {"club_slug": self.club.slug, "changes": [{"category": "news", "email": False}]}
        first = self.call("patch", data=data).json()
        second = self.call("patch", data=data).json()

        self.assertEqual(first, second)
        self.assertEqual(NotificationPreference.objects.count(), 1)

    def test_unknown_category_is_rejected_and_nothing_saved(self):
        response = self.call("patch", data={"club_slug": None, "changes": [{"category": "news", "push": False}, {"category": "nope", "push": False}]})

        self.assertError(response, 422, "validation_error")
        self.assertIn("changes", response.json()["error"]["fields"])
        self.assertFalse(NotificationPreference.objects.exists())

    def test_unknown_or_foreign_club_is_rejected(self):
        other = make_club(name="Other FC", slug="other-fc") if "name" in make_club.__code__.co_varnames else Club.objects.create(name="Other FC", slug="other-fc")
        for slug in ("nope", other.slug):
            response = self.call("patch", data={"club_slug": slug, "changes": [{"category": "news", "push": False}]})
            self.assertError(response, 422, "validation_error")
            self.assertIn("club_slug", response.json()["error"]["fields"])
        self.assertFalse(NotificationPreference.objects.exists())

    def test_another_accounts_switches_are_untouched_and_invisible(self):
        stranger = User.objects.create_user(email="other@example.com", password="pw-secret-123")
        preferences.set_preference(stranger, None, "news", "push", False)

        body = self.call("patch", data={"club_slug": None, "changes": [{"category": "news", "push": True}]}).json()

        self.assertTrue(body["defaults"]["news"]["push"])
        self.assertFalse(preferences.matrix(stranger)["news"]["push"])
        self.call("delete")
        self.assertFalse(preferences.matrix(stranger)["news"]["push"])

    def test_delete_drops_one_override_or_a_whole_scope(self):
        preferences.set_preference(self.user, None, "news", "push", False)
        preferences.set_preference(self.user, self.club, "news", "push", True)
        preferences.set_preference(self.user, self.club, "shop", "email", False)

        body = self.call("delete", path=f"{PATH}?club_slug={self.club.slug}&category=news").json()
        self.assertEqual(self.club_entry(body)["overrides"], {"shop": {"email": False}})
        self.assertFalse(self.club_entry(body)["effective"]["news"]["push"])

        body = self.call("delete", path=f"{PATH}?club_slug={self.club.slug}").json()
        self.assertEqual(self.club_entry(body)["overrides"], {})
        self.assertFalse(body["defaults"]["news"]["push"])  # the all-clubs row stays

        body = self.call("delete", path=f"{PATH}?category=news").json()
        self.assertTrue(body["defaults"]["news"]["push"])
        self.assertEqual(self.call("delete").status_code, 200)  # nothing stored is fine

    def test_delete_validates_category_and_club(self):
        self.assertError(self.call("delete", path=f"{PATH}?category=nope"), 422, "validation_error")
        self.assertError(self.call("delete", path=f"{PATH}?club_slug=nope"), 422, "validation_error")
