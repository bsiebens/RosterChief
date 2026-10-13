"""PWA notification settings: person selector for children, per-person rows, quiet hours."""

import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from club.models import ClubMembership, ClubRole, Season
from members.models import Family, FamilyMembership, Member
from notifications import preferences
from notifications.models import NotificationCategory, NotificationPreference, QuietHours
from rosterchief.test_support import make_club

User = get_user_model()
HOST = "ajax-united.rosterchief.app"


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class GranularNotificationSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Pat", last_name="Parent", user=cls.user)
        ClubRole.objects.create(club=cls.club, member=cls.member)
        cls.child = Member.objects.create(first_name="Kim", last_name="Parent", date_of_birth=datetime.date(2014, 5, 1))
        for person in (cls.member, cls.child):
            ClubMembership.objects.create(club=cls.club, member=person, season=season, status=ClubMembership.StatusChoices.ACTIVE)
        family = Family.objects.create(club=cls.club)
        FamilyMembership.objects.create(family=family, member=cls.member, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(self.user)
        self.url = reverse("mobile:notification_settings")

    def everything_on(self, **extra):
        data = {"category_switches": "1", **extra}
        for category in NotificationCategory.values:
            data[f"push__{category}"] = "on"
            data[f"email__{category}"] = "on"
        return data

    def test_selector_lists_children(self):
        page = self.client.get(self.url, HTTP_HOST=HOST)
        self.assertContains(page, f"?person={self.child.pk}")
        self.assertContains(page, "Kim")

    def test_page_for_a_child_shows_their_switches(self):
        preferences.set_preference(self.user, self.club, "news", "push", False, member=self.child)
        page = self.client.get(f"{self.url}?person={self.child.pk}", HTTP_HOST=HOST)
        self.assertContains(page, "Set for this person only")
        self.assertContains(page, f'name="person" value="{self.child.pk}"')

    def test_saving_for_a_child_writes_person_rows_only(self):
        data = self.everything_on(person=str(self.child.pk))
        del data["push__news"]

        response = self.client.post(self.url, data, HTTP_HOST=HOST)

        self.assertRedirects(response, f"{self.url}?person={self.child.pk}", fetch_redirect_response=False)
        row = NotificationPreference.objects.get()
        self.assertEqual((row.club, row.member, row.category, row.channel, row.enabled), (self.club, self.child, "news", "push", False))
        self.assertTrue(preferences.matrix(self.user, self.club)["news"]["push"])

    def test_reset_for_a_child_leaves_other_scopes(self):
        preferences.set_preference(self.user, self.club, "news", "push", False)
        preferences.set_preference(self.user, self.club, "news", "push", False, member=self.child)

        self.client.post(self.url, {"reset_category": "news", "person": str(self.child.pk)}, HTTP_HOST=HOST)

        self.assertFalse(NotificationPreference.objects.filter(member=self.child).exists())
        self.assertTrue(NotificationPreference.objects.filter(club=self.club, member__isnull=True).exists())

    def test_a_foreign_person_id_falls_back_to_the_club_scope(self):
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger")
        data = self.everything_on(person=str(stranger.pk))
        del data["push__news"]

        self.client.post(self.url, data, HTTP_HOST=HOST)

        self.assertEqual(NotificationPreference.objects.get().member, None)

    def test_quiet_hours_form_renders_defaults_and_saves(self):
        page = self.client.get(self.url, HTTP_HOST=HOST)
        self.assertContains(page, 'name="timezone"')
        self.assertContains(page, 'value="22:00"')

        response = self.client.post(self.url, {"quiet_hours": "1", "enabled": "on", "start": "21:30", "end": "06:45", "timezone": "Europe/London"}, HTTP_HOST=HOST)

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        hours = QuietHours.objects.get(user=self.user)
        self.assertEqual((hours.enabled, hours.start, hours.end, hours.timezone), (True, datetime.time(21, 30), datetime.time(6, 45), "Europe/London"))

    def test_quiet_hours_can_be_switched_off_and_rejects_garbage(self):
        self.client.post(self.url, {"quiet_hours": "1", "start": "22:00", "end": "07:00", "timezone": "UTC"}, HTTP_HOST=HOST)
        self.assertFalse(QuietHours.objects.get().enabled)
        bad = self.client.post(self.url, {"quiet_hours": "1", "start": "nope", "end": "07:00", "timezone": "UTC"}, HTTP_HOST=HOST)
        self.assertEqual(bad.status_code, 400)
        bad = self.client.post(self.url, {"quiet_hours": "1", "start": "22:00", "end": "07:00", "timezone": "Mars/Base"}, HTTP_HOST=HOST)
        self.assertEqual(bad.status_code, 400)
