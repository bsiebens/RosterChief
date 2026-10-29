"""A duplicate name in a per-club form is a form error, not an IntegrityError 500 (the forms leave ``club`` out)."""

from django.urls import reverse

from club.models import OnboardingRequirement
from management.forms import OfficialLevelForm
from management.tests import ManagementTestBase
from members.models import Group
from rosterchief.test_support import make_club
from teams.models import NumberPool, OfficialLevel, Position, RefereeLevel


class DuplicateNameWebTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def test_position(self):
        Position.objects.create(club=self.club, name="Forward", short_name="F")
        response = self.club_post("position_create", {"name": "Forward", "short_name": "FW", "ordering": 0})
        self.assertContains(response, "A position with this name already exists.")
        self.assertEqual(Position.objects.filter(club=self.club).count(), 1)

    def test_position_rename_to_a_taken_name_but_not_to_its_own(self):
        Position.objects.create(club=self.club, name="Forward", short_name="F")
        winger = Position.objects.create(club=self.club, name="Winger", short_name="W")
        self.assertContains(self.club_post("position_update", {"name": "Forward", "short_name": "W", "ordering": 0}, winger.pk), "A position with this name already exists.")
        self.assertEqual(self.club_post("position_update", {"name": "Winger", "short_name": "WW", "ordering": 0}, winger.pk).status_code, 302)

    def test_number_pool(self):
        NumberPool.objects.create(club=self.club, name="Juniors", min_number=1, max_number=20)
        response = self.club_post("number_pool_create", {"name": "Juniors", "min_number": 1, "max_number": 9, "min_age_gap_years": 5})
        self.assertContains(response, "A number pool with this name already exists.")

    def test_group(self):
        Group.objects.create(club=self.club, name="Coaches")
        self.assertContains(self.club_post("group_create", {"name": "Coaches"}), "A group with this name already exists.")
        self.assertEqual(Group.objects.filter(club=self.club).count(), 1)

    def test_referee_level(self):
        RefereeLevel.objects.create(club=self.club, name="L1")
        self.assertContains(self.club_post("referee_level_create", {"name": "L1"}), "A level with this name already exists.")
        self.assertEqual(RefereeLevel.objects.filter(club=self.club).count(), 1)

    def test_official_level_form(self):
        OfficialLevel.objects.create(club=self.club, name="O1")
        form = OfficialLevelForm({"name": "O1"}, club=self.club)
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
        self.assertTrue(OfficialLevelForm({"name": "O2"}, club=self.club).is_valid())

    def test_onboarding_requirement(self):
        OnboardingRequirement.objects.create(club=self.club, name="Photo")
        response = self.client.post(reverse("management:onboarding_requirement_create"), {"name": "Photo", "description": ""}, HTTP_HOST="ajax-united.rosterchief.app")
        self.assertContains(response, "An onboarding requirement with this name already exists.")
        self.assertEqual(OnboardingRequirement.objects.filter(club=self.club).count(), 1)

    def test_another_clubs_name_is_not_a_duplicate(self):
        Group.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Coaches")
        self.assertEqual(self.club_post("group_create", {"name": "Coaches"}).status_code, 302)
