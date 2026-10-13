"""Me -> notification settings: this club's push/email switches per category (mobile.views.NotificationSettingsView)."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from club.models import ClubRole
from members.models import Member
from notifications import preferences
from notifications.models import NotificationCategory, NotificationPreference
from rosterchief.test_support import make_club

User = get_user_model()
HOST = "ajax-united.rosterchief.app"


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class NotificationCategorySwitchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.user = User.objects.create_user(email="fan@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Fay", last_name="Fan", user=cls.user)
        ClubRole.objects.create(club=cls.club, member=cls.member)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(self.user)
        self.url = reverse("mobile:notification_settings")

    def post(self, data):
        return self.client.post(self.url, data, HTTP_HOST=HOST)

    def everything_on(self):
        data = {"category_switches": "1"}
        for category in NotificationCategory.values:
            data[f"push__{category}"] = "on"
            data[f"email__{category}"] = "on"
        return data

    def test_renders_every_category_with_both_switches_on(self):
        response = self.client.get(self.url, HTTP_HOST=HOST)

        for value, label in NotificationCategory.choices:
            self.assertContains(response, label)
            self.assertContains(response, f'name="push__{value}"')
            self.assertContains(response, f'name="email__{value}"')
        self.assertNotContains(response, "Reset to default")
        self.assertContains(response, 'name="email_notifications"')  # the existing email choice is still there

    def test_toggling_saves_a_club_row_and_no_global_one(self):
        data = self.everything_on()
        del data["push__news"]

        response = self.post(data)

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        row = NotificationPreference.objects.get()
        self.assertEqual((row.club, row.category, row.channel, row.enabled), (self.club, "news", "push", False))
        self.assertFalse(preferences.matrix(self.user, self.club)["news"]["push"])
        self.assertTrue(preferences.matrix(self.user)["news"]["push"])

    def test_unchanged_switches_write_nothing_and_inherit_the_default(self):
        preferences.set_preference(self.user, None, "shop", "email", False)
        data = self.everything_on()
        del data["email__shop"]  # what the form shows: the inherited "off"

        self.post(data)

        self.assertEqual(NotificationPreference.objects.count(), 1)

    def test_an_override_shows_its_state_and_can_be_reset(self):
        preferences.set_preference(self.user, None, "news", "push", False)
        preferences.set_preference(self.user, self.club, "news", "push", True)

        page = self.client.get(self.url, HTTP_HOST=HOST)
        self.assertContains(page, "Set for this club only")
        self.assertContains(page, 'value="news"')

        response = self.post({"reset_category": "news"})

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertFalse(NotificationPreference.objects.filter(club=self.club).exists())
        self.assertFalse(preferences.matrix(self.user, self.club)["news"]["push"])  # inherits the all-clubs row again

    def test_reset_of_an_unknown_category_is_rejected(self):
        self.assertEqual(self.post({"reset_category": "nope"}).status_code, 400)
