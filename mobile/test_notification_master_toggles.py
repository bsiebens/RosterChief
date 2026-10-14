"""PWA notification settings: "All push" / "All emails" master toggles, Customised marker, Reset all."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from club.models import ClubRole
from members.models import Member
from notifications import preferences
from notifications.models import NotificationCategory, NotificationMasterSwitch, NotificationPreference
from rosterchief.test_support import make_club

User = get_user_model()
HOST = "ajax-united.rosterchief.app"


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class MasterToggleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.user = User.objects.create_user(email="master-fan@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Mia", last_name="Master", user=cls.user)
        ClubRole.objects.create(club=cls.club, member=cls.member)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(self.user)
        self.url = reverse("mobile:notification_settings")

    def post(self, data):
        return self.client.post(self.url, data, HTTP_HOST=HOST)

    def form(self, *, push=True, email=True):
        data = {"category_switches": "1", "master_switches": "1"}
        for category in NotificationCategory.values:
            data[f"push__{category}"] = "on"
            data[f"email__{category}"] = "on"
        if push:
            data["master__push"] = "on"
        if email:
            data["master__email"] = "on"
        return data

    def test_renders_masters_on_and_not_customised(self):
        response = self.client.get(self.url, HTTP_HOST=HOST)
        self.assertContains(response, 'name="master__push"')
        self.assertContains(response, 'name="master__email"')
        self.assertNotContains(response, "Customised")
        self.assertNotContains(response, "Reset all")

    def test_master_off_saved_and_silences_enabled_categories(self):
        self.post(self.form(push=False))
        self.assertFalse(preferences.master_matrix(self.user, self.club)["push"])
        self.assertFalse(preferences.is_enabled(self.user, self.club, "news", "push"))
        self.assertTrue(preferences.is_enabled(self.user, self.club, "news", "email"))
        self.assertEqual(NotificationPreference.objects.count(), 0)  # category rows untouched: they were all on
        response = self.client.get(self.url, HTTP_HOST=HOST)
        self.assertContains(response, "Customised")
        self.assertContains(response, "Reset all")

    def test_disabled_category_boxes_do_not_overwrite_stored_choices(self):
        preferences.set_preference(self.user, self.club, "news", "push", False)
        data = self.form(push=False)
        for category in NotificationCategory.values:
            del data[f"push__{category}"]  # a browser does not submit disabled boxes
        self.post(data)
        self.assertEqual(NotificationPreference.objects.filter(channel="push", enabled=True).count(), 0)
        self.post(self.form(push=True) | {"push__news": "on"})
        self.assertTrue(preferences.master_matrix(self.user, self.club)["push"])
        self.assertTrue(preferences.matrix(self.user, self.club)["news"]["push"])

    def test_form_without_master_marker_leaves_masters_alone(self):
        preferences.set_master(self.user, self.club, "email", False)
        data = self.form()
        del data["master_switches"], data["master__email"]
        self.post(data)
        self.assertFalse(preferences.master_matrix(self.user, self.club)["email"])

    def test_reset_all_drops_masters_and_categories(self):
        preferences.set_master(self.user, self.club, "push", False)
        preferences.set_preference(self.user, self.club, "news", "email", False)
        response = self.post({"reset_all": "1"})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(NotificationMasterSwitch.objects.count(), 0)
        self.assertEqual(NotificationPreference.objects.count(), 0)
