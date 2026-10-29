from waffle import get_waffle_flag_model

from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import ClubRole, ShopManager
from teams.models import Position, StaffAssignment, Team

URL = "/manage/me"


class MeTests(AppApiTestCase):
    def get(self, token, host=CLUB_HOST):
        return self.api("get", URL, token=token, host=host)

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def areas(self, token):
        response = self.get(token)
        self.assertEqual(response.status_code, 200, response.content)
        return {area["key"]: area for area in response.json()["areas"]}

    def flag_on(self, name):
        get_waffle_flag_model().objects.create(name=name, everyone=True)

    def test_no_token_is_401(self):
        self.assertError(self.api("get", URL, host=CLUB_HOST), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        self.assertError(self.get(self.login()), 403, "forbidden")

    def test_other_club_and_base_domain(self):
        token = self.as_role(ClubRole.Roles.ADMIN)
        from rosterchief.test_support import make_club

        make_club(name="Rival FC", slug="rival-fc")
        self.assertError(self.get(token, OTHER_HOST), 403, "not_a_member")
        self.assertEqual(self.api("get", URL, token=token).status_code, 404)

    def test_admin_document(self):
        token = self.as_role(ClubRole.Roles.ADMIN)
        body = self.get(token).json()
        self.assertEqual(body["member"], {"id": str(self.member.pk), "name": str(self.member)})
        self.assertEqual(body["club"], {"id": str(self.club.pk), "name": self.club.name, "slug": self.club.slug})
        self.assertEqual(body["season"]["id"], str(self.season.pk))
        self.assertIn("admin", body["roles"])
        self.assertIn("shop", body["club_features"])
        keys = [area["key"] for area in body["areas"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertNotIn("controlpanel", keys)
        areas = self.areas(token)
        for key, area in areas.items():
            if area["available"]:
                self.assertTrue(area["can_view"] and area["can_manage"], key)

    def test_feature_off_makes_area_unavailable_even_for_admin(self):
        token = self.as_role(ClubRole.Roles.ADMIN)
        areas = self.areas(token)
        for key in ("shop", "forms", "evaluations", "officials", "official_levels"):
            self.assertFalse(areas[key]["available"], key)
            self.assertFalse(areas[key]["can_view"], key)
            self.assertFalse(areas[key]["can_manage"], key)
        self.flag_on("shop")
        areas = self.areas(token)
        self.assertTrue(areas["shop"]["available"])
        self.assertTrue(areas["shop"]["can_manage"])
        self.assertFalse(areas["forms"]["available"])

    def test_member_admin_manages_people_but_not_admin_ground(self):
        areas = self.areas(self.as_role(ClubRole.Roles.MEMBER_ADMIN))
        for key in ("members", "families", "groups", "teams", "signup", "volunteers", "referees", "referee_levels", "onboarding_requirements"):
            self.assertTrue(areas[key]["can_manage"], key)
        for key in ("memberships", "settings", "roles", "sponsors", "registrations", "news", "locations"):
            self.assertFalse(areas[key]["can_manage"], key)
        self.assertTrue(areas["news"]["can_view"])
        self.assertFalse(areas["settings"]["can_view"])

    def test_editor_manages_news_only(self):
        areas = self.areas(self.as_role(ClubRole.Roles.EDITOR))
        self.assertTrue(areas["news"]["can_manage"])
        self.assertTrue(areas["members"]["can_view"])
        for key in ("members", "teams", "settings", "memberships", "roles"):
            self.assertFalse(areas[key]["can_manage"], key)

    def test_coach_manager_reads_and_manages_what_its_position_allows(self):
        team = Team.objects.create(club=self.club, name="U12")
        position = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)
        token = self.login_with_totp()
        body = self.get(token).json()
        self.assertIn("coach_manager", body["roles"])
        areas = {area["key"]: area for area in body["areas"]}
        self.assertTrue(areas["members"]["can_view"] and not areas["members"]["can_manage"])
        self.assertTrue(areas["events"]["can_manage"])
        self.assertTrue(areas["locations"]["can_manage"])
        self.assertTrue(areas["news"]["can_manage"])
        self.assertFalse(areas["settings"]["can_view"])
        self.assertFalse(areas["teams"]["can_manage"])

    def test_non_management_coach_cannot_manage_events_or_news(self):
        team = Team.objects.create(club=self.club, name="U12")
        position = Position.objects.create(club=self.club, name="Physio", short_name="PH", staff_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)
        areas = self.areas(self.login_with_totp())
        self.assertTrue(areas["events"]["can_view"])
        self.assertFalse(areas["events"]["can_manage"])
        self.assertFalse(areas["news"]["can_manage"])

    def test_shop_manager_grant_is_additive(self):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": ClubRole.Roles.EDITOR})
        ShopManager.objects.create(club=self.club, member=self.member)
        self.flag_on("shop")
        areas = self.areas(self.login_with_totp())
        self.assertTrue(areas["shop"]["can_manage"])
        self.assertFalse(areas["settings"]["can_view"])
