from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from club.models import ClubMembership
from members.models import Member
from rosterchief.test_support import make_club, make_season

User = get_user_model()
HOST = "ajax-united.rosterchief.app"
SETTINGS = {"ROSTERCHIEF_BASE_DOMAIN": "rosterchief.app", "ALLOWED_HOSTS": ["rosterchief.app", HOST, "testserver"]}


@override_settings(**SETTINGS)
class SwitchClubTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.other = make_club(name="Bravo FC", slug="bravo-fc")
        cls.stranger = make_club(name="Secret Club", slug="secret-club")
        cls.user = User.objects.create_user(email="a@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="A", last_name="B", email="a@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=make_season(cls.club))

    def setUp(self):
        self.client.force_login(self.user)

    def get(self, name):
        return self.client.get(reverse(name), headers={"host": HOST})

    def test_single_club_account_has_no_row(self):
        self.assertNotContains(self.get("mobile:me"), reverse("mobile:club_picker"))

    def test_multi_club_account_sees_row_and_picker(self):
        ClubMembership.objects.create(club=self.other, member=self.member, season=make_season(self.other))
        self.assertContains(self.get("mobile:me"), reverse("mobile:club_picker"))
        response = self.get("mobile:club_picker")
        self.assertContains(response, "Choose Your Club")
        self.assertContains(response, "Bravo FC")
        self.assertContains(response, "http://bravo-fc.rosterchief.app/app/")
        self.assertNotContains(response, "Secret Club")
        self.assertNotContains(response, "secret-club")

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        self.assertEqual(self.get("mobile:club_picker").status_code, 302)
