import datetime
from decimal import Decimal

from django.urls import reverse

from club.models import ClubMembership, ClubRole, Season
from members.models import Member
from notifications.models import NotificationCategory, NotificationPreference, QuietHours
from rosterchief.test_support import enrol_mfa

from .tests import ControlPanelTestBase, User


class UserDebugViewTests(ControlPanelTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.root = User.objects.create_superuser(email="super@example.com", password="pw-secret-123")
        enrol_mfa(cls.root)
        cls.person = User.objects.create_user(email="Person@Example.com", password="pw-secret-123")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.root)
        self.url = reverse("controlpanel:user_debug")

    def test_staff_who_is_not_superuser_is_refused(self):
        staff = User.objects.create_user(email="staff@example.com", password="pw-secret-123", is_staff=True)
        enrol_mfa(staff)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_unknown_email(self):
        response = self.client.get(self.url, {"email": "nobody@example.com"})
        self.assertContains(response, "No account with the email")

    def test_shows_saved_switches_and_quiet_hours(self):
        NotificationPreference.objects.create(user=self.person, club=None, category=NotificationCategory.NEWS, channel="push", enabled=False)
        NotificationPreference.objects.create(user=self.person, club=self.club, category=NotificationCategory.EVENT, channel="email", enabled=True)
        QuietHours.objects.create(user=self.person, enabled=True, start=datetime.time(22, 0), end=datetime.time(7, 30), timezone="Europe/Brussels")
        response = self.client.get(self.url, {"email": "person@example.com"})
        self.assertContains(response, "Person@example.com")
        self.assertContains(response, "22:00 \u2013 07:30")
        self.assertContains(response, "All clubs")
        self.assertContains(response, self.club.name)
        self.assertContains(response, "News")
        self.assertEqual(len(response.context["notifications"]["scopes"]), 2)

    def test_account_without_anything_saved(self):
        response = self.client.get(self.url, {"email": "person@example.com"})
        self.assertContains(response, "Nothing saved")
        self.assertContains(response, "None saved")

    def test_shows_clubs_roles_memberships_without_personal_data(self):
        member = Member.objects.create(user=self.person, first_name="Pat", last_name="Debug", date_of_birth=datetime.date(2011, 3, 4), phone="+32475123456", emergency_phone="+32475999888")
        season = Season.objects.create(club=self.club, start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2027, 7, 31))
        ClubRole.objects.create(club=self.club, member=member, role=ClubRole.Roles.EDITOR)
        ClubMembership.objects.create(club=self.club, member=member, season=season, status=ClubMembership.StatusChoices.LAPSED, fee_amount=Decimal("120.00"))
        response = self.client.get(self.url, {"email": "person@example.com"})
        self.assertContains(response, "Pat Debug")
        self.assertContains(response, self.club.name)
        self.assertContains(response, season.name)
        self.assertContains(response, "lapsed")
        self.assertContains(response, "editor")
        for private in ("475123456", "475999888", "2011", "03/04", "04/03"):
            self.assertNotContains(response, private)

    def test_account_without_member_profile(self):
        response = self.client.get(self.url, {"email": "person@example.com"})
        self.assertContains(response, "No member profile")
        self.assertContains(response, "Not attached to any club")
