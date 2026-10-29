import datetime
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from waffle import get_waffle_flag_model

from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import Club, ClubRole, EvaluationManager, OnboardingRequirement, RefereeManager, ShopManager, Sponsor
from management.tests import ONE_PIXEL_PNG
from members.models import Member
from registration.models import RegistrationBatch, RegistrationDetails
from teams.models import OfficialLevel, OfficialProfile, Position, RefereeLevel, RefereeProfile, StaffAssignment, Team

MEDIA = tempfile.mkdtemp(prefix="rc-test-media-")


def png(name="logo.png", content_type="image/png"):
    return SimpleUploadedFile(name, ONE_PIXEL_PNG, content_type=content_type)


class _Base(AppApiTestCase):
    def setUp(self):
        super().setUp()
        #: A signed-in plain member; valid until the caller is given a privileged role (then MFA is required).
        self.plain = self.login()
        self._totp_token = None

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._totp_token is None:
            self._totp_token = self.login_with_totp()
        return self._totp_token

    def admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def call(self, method, path, token=None, data=None, host=CLUB_HOST, **headers):
        return self.api(method, path, token=token, host=host, data=data, **headers)

    def multipart(self, path, token, data, host=CLUB_HOST, **headers):
        return self.client.post(f"/api/v1{path}", data, HTTP_HOST=host, HTTP_AUTHORIZATION=f"Bearer {token}", **headers)

    def coach(self):
        team, _created = Team.objects.get_or_create(club=self.club, name="Coached", defaults={"short_name": "C"})
        position, _created = Position.objects.get_or_create(club=self.club, name="Assistant", defaults={"short_name": "AC", "staff_position": True})
        StaffAssignment.objects.get_or_create(team=team, member=self.member, season=self.season, position=position)
        return self.plain

    def other_club(self):
        return Club.objects.create(name="Rival FC", slug="rival-fc")

    def stranger(self):
        return Member.objects.create(first_name="Sam", last_name="Stranger")


@override_settings(MEDIA_ROOT=MEDIA)
class ClubSettingsTests(_Base):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_gates(self):
        self.assertError(self.call("get", "/manage/settings"), 401, "unauthenticated")
        self.assertError(self.call("get", "/manage/settings", self.plain), 403, "forbidden")
        self.assertError(self.call("get", "/manage/settings", self.as_role(ClubRole.Roles.MEMBER_ADMIN)), 403, "forbidden")
        self.assertError(self.call("patch", "/manage/settings", self.as_role(ClubRole.Roles.MEMBER_ADMIN), {"name": "X"}), 403, "forbidden")

    def test_coach_is_403(self):
        self.assertError(self.call("get", "/manage/settings", self.coach()), 403, "forbidden")

    def test_get_and_patch(self):
        token = self.admin()
        body = self.call("get", "/manage/settings", token).json()
        self.assertEqual((body["slug"], body["name"], body["logo_url"]), ("ajax-united", "Ajax United", None))
        response = self.call("patch", "/manage/settings", token, {"name": "Ajax FC", "primary_color": "#112233", "contact_email": "hi@example.com"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["name"], "Ajax FC")
        self.assertEqual(response.json()["primary_color"], "#112233")
        self.club.refresh_from_db()
        self.assertEqual(self.club.contact_email, "hi@example.com")
        self.assertEqual(self.club.slug, "ajax-united")

    def test_patch_validation_is_422(self):
        token = self.admin()
        response = self.call("patch", "/manage/settings", token, {"primary_color": "blue"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("primary_color", response.json()["error"]["fields"])
        self.assertError(self.call("patch", "/manage/settings", token, {"name": ""}), 422, "validation_error")

    def test_logo_upload_and_remove(self):
        token = self.admin()
        response = self.multipart("/manage/settings/logo", token, {"logo": png()})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["logo_url"])
        self.club.refresh_from_db()
        self.assertTrue(self.club.logo)
        removed = self.call("delete", "/manage/settings/logo", token)
        self.assertEqual(removed.status_code, 200, removed.content)
        self.assertIsNone(removed.json()["logo_url"])

    def test_logo_upload_errors(self):
        self.assertError(self.multipart("/manage/settings/logo", self.plain, {"logo": png()}), 403, "forbidden")
        token = self.admin()
        self.assertError(self.multipart("/manage/settings/logo", token, {}), 422, "validation_error")
        self.assertError(self.multipart("/manage/settings/logo", token, {"logo": png("a.pdf", "application/pdf")}), 415, "unsupported_media_type")
        with override_settings(API_UPLOAD_MAX_BYTES=5):
            self.assertError(self.multipart("/manage/settings/logo", token, {"logo": png()}), 413, "file_too_large")

    def test_event_background_upload(self):
        token = self.admin()
        response = self.multipart("/manage/settings/event-background", token, {"event_background": png("bg.png")})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["event_background_url"])

    def test_previews(self):
        self.assertError(self.call("get", "/manage/settings/email-previews", self.plain), 403, "forbidden")
        token = self.admin()
        listing = self.call("get", "/manage/settings/email-previews", token).json()["items"]
        self.assertTrue(listing)
        self.assertTrue(listing[0]["subject"])
        key = listing[0]["key"]
        html = self.call("get", f"/manage/settings/email-previews/{key}/render", token)
        self.assertEqual(html.status_code, 200)
        self.assertIn("text/html", html["Content-Type"])
        self.assertError(self.call("get", "/manage/settings/email-previews/nope/render", token), 404, "not_found")
        pdfs = self.call("get", "/manage/settings/pdf-previews", token).json()["items"]
        self.assertTrue(pdfs)
        self.assertEqual(self.call("get", f"/manage/settings/pdf-previews/{pdfs[0]['key']}/render", token).status_code, 200)
        self.assertError(self.call("get", "/manage/settings/pdf-previews/nope/render", token), 404, "not_found")

    def test_wrong_host_sees_nothing(self):
        token = self.admin()
        self.assertIn(self.call("get", "/manage/settings", token, host=OTHER_HOST).status_code, (403, 404))


class OnboardingRequirementTests(_Base):
    BASE = "/manage/settings/onboarding-requirements"

    def test_gates(self):
        self.assertError(self.call("get", self.BASE), 401, "unauthenticated")
        self.assertError(self.call("get", self.BASE, self.plain), 403, "forbidden")
        self.assertError(self.call("get", self.BASE, self.coach()), 403, "forbidden")
        self.assertError(self.call("post", self.BASE, self.coach(), {"name": "X"}), 403, "forbidden")

    def test_member_admin_crud(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        created = self.call("post", self.BASE, token, {"name": "Medical certificate", "requires_document": True, "blocked_event_kinds": ["game"]})
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual((body["name"], body["requires_document"], body["blocked_event_kinds"], body["is_active"]), ("Medical certificate", True, ["game"], True))
        listing = self.call("get", self.BASE, token).json()["items"]
        self.assertEqual([r["name"] for r in listing], ["Medical certificate"])
        patched = self.call("patch", f"{self.BASE}/{body['id']}", token, {"is_active": False, "description": "Bring it"})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()["is_active"], patched.json()["description"], patched.json()["requires_document"]), (False, "Bring it", True))
        self.assertEqual(self.call("delete", f"{self.BASE}/{body['id']}", token).status_code, 204)
        self.assertFalse(OnboardingRequirement.objects.exists())

    def test_validation(self):
        token = self.admin()
        self.assertError(self.call("post", self.BASE, token, {"name": "A", "blocked_event_kinds": ["bogus"]}), 422, "validation_error")
        self.call("post", self.BASE, token, {"name": "Dup"})
        response = self.call("post", self.BASE, token, {"name": "Dup"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("name", response.json()["error"]["fields"])

    def test_other_club_is_404(self):
        other = OnboardingRequirement.objects.create(club=self.other_club(), name="Theirs")
        token = self.admin()
        self.assertError(self.call("patch", f"{self.BASE}/{other.pk}", token, {"name": "Mine"}), 404, "not_found")
        self.assertError(self.call("delete", f"{self.BASE}/{other.pk}", token), 404, "not_found")
        self.assertTrue(OnboardingRequirement.objects.filter(pk=other.pk).exists())


class RoleTests(_Base):
    BASE = "/manage/roles"

    def test_gates(self):
        self.assertError(self.call("get", self.BASE), 401, "unauthenticated")
        self.assertError(self.call("get", self.BASE, self.plain), 403, "forbidden")
        self.assertError(self.call("get", self.BASE, self.coach()), 403, "forbidden")
        # A member admin must not be able to grant themselves admin.
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        self.assertError(self.call("get", self.BASE, token), 403, "forbidden")
        self.assertError(self.call("post", self.BASE, token, {"member_id": str(self.member.pk), "role": "admin"}), 403, "forbidden")
        self.assertEqual(ClubRole.objects.get(club=self.club, member=self.member).role, ClubRole.Roles.MEMBER_ADMIN)

    def test_grant_promotes_the_one_role_and_lists(self):
        token = self.admin()
        response = self.call("post", self.BASE, token, {"member_id": str(self.child.pk), "role": "editor"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["role"], response.json()["role_label"], response.json()["created"]), ("editor", "editor", True))
        same = self.call("post", self.BASE, token, {"member_id": str(self.child.pk), "role": "editor"})
        self.assertEqual((same.status_code, same.json()["created"]), (200, False))
        again = self.call("post", self.BASE, token, {"member_id": str(self.child.pk), "role": "member_admin"})
        self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(ClubRole.objects.filter(club=self.club, member=self.child).count(), 1)
        self.assertEqual(ClubRole.objects.get(club=self.club, member=self.child).role, ClubRole.Roles.MEMBER_ADMIN)
        listing = self.call("get", self.BASE, token).json()
        roles = {row["member"]["name"]: row["role"] for row in listing["roles"]}
        self.assertEqual(roles["Kim Parent"], "member_admin")
        self.assertEqual(roles["Pat Parent"], "admin")

    def test_additive_grants_and_revoke(self):
        token = self.admin()
        for kind, model, path in (("shop_admin", ShopManager, "shop-admins"), ("referee_admin", RefereeManager, "referee-admins"), ("evaluation_manager", EvaluationManager, "evaluation-managers")):
            response = self.call("post", self.BASE, token, {"member_id": str(self.child.pk), "role": kind})
            self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(self.call("post", self.BASE, token, {"member_id": str(self.child.pk), "role": kind}).status_code, 200)
            self.assertEqual(model.objects.filter(club=self.club, member=self.child).count(), 1)
            key = {"shop_admin": "shop_admins", "referee_admin": "referee_admins", "evaluation_manager": "evaluation_managers"}[kind]
            holders = self.call("get", self.BASE, token).json()[key]
            self.assertEqual([h["member"]["name"] for h in holders], ["Kim Parent"])
            self.assertEqual(holders[0]["id"], response.json()["id"])
            self.assertEqual(self.call("delete", f"{self.BASE}/{path}/{holders[0]['id']}", token).status_code, 204)
            self.assertFalse(model.objects.exists())
        self.assertFalse(ClubRole.objects.filter(club=self.club, member=self.child).exclude(role="member").exists())

    def test_revoke_role(self):
        token = self.admin()
        role, _created = ClubRole.objects.update_or_create(club=self.club, member=self.child, defaults={"role": ClubRole.Roles.EDITOR})
        self.assertEqual(self.call("delete", f"{self.BASE}/{role.pk}", token).status_code, 204)
        self.assertFalse(ClubRole.objects.filter(pk=role.pk).exists())

    def test_validation(self):
        token = self.admin()
        self.assertError(self.call("post", self.BASE, token, {"member_id": str(self.child.pk), "role": "bogus"}), 422, "validation_error")
        response = self.call("post", self.BASE, token, {"member_id": str(self.stranger().pk), "role": "admin"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("member_id", response.json()["error"]["fields"])

    def test_other_club_rows_are_404(self):
        other = self.other_club()
        stranger = self.stranger()
        role = ClubRole.objects.create(club=other, member=stranger, role=ClubRole.Roles.ADMIN)
        shop = ShopManager.objects.create(club=other, member=stranger)
        token = self.admin()
        self.assertError(self.call("delete", f"{self.BASE}/{role.pk}", token), 404, "not_found")
        self.assertError(self.call("delete", f"{self.BASE}/shop-admins/{shop.pk}", token), 404, "not_found")
        self.assertError(self.call("delete", f"{self.BASE}/referee-admins/{shop.pk}", token), 404, "not_found")
        self.assertTrue(ClubRole.objects.filter(pk=role.pk).exists() and ShopManager.objects.filter(pk=shop.pk).exists())

    def test_grant_is_idempotent_with_a_key(self):
        token = self.admin()
        key = {"HTTP_IDEMPOTENCY_KEY": "grant-1"}
        first = self.call("post", self.BASE, token, {"member_id": str(self.child.pk), "role": "shop_admin"}, **key)
        second = self.call("post", self.BASE, token, {"member_id": str(self.child.pk), "role": "shop_admin"}, **key)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json(), second.json())


@override_settings(MEDIA_ROOT=MEDIA)
class SponsorTests(_Base):
    BASE = "/manage/sponsors"

    def form(self, **extra):
        return {"name": "Acme", "start_date": "2026-01-01", **extra}

    def test_gates(self):
        self.assertError(self.call("get", self.BASE), 401, "unauthenticated")
        self.assertError(self.call("get", self.BASE, self.plain), 403, "forbidden")
        self.assertError(self.call("get", self.BASE, self.coach()), 403, "forbidden")
        self.assertError(self.multipart(self.BASE, self.coach(), self.form()), 403, "forbidden")
        self.assertError(self.call("get", self.BASE, self.as_role(ClubRole.Roles.MEMBER_ADMIN)), 403, "forbidden")

    def test_create_with_logo_list_get_update_delete(self):
        token = self.admin()
        response = self.multipart(self.BASE, token, self.form(url="https://acme.example.com", logo=png()))
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["name"], body["is_active"]), ("Acme", True))
        self.assertTrue(body["logo_url"])
        self.assertEqual((body["logo_width"], body["logo_height"]), (1, 1))
        sponsor = Sponsor.objects.get(pk=body["id"])
        self.assertEqual(sponsor.club, self.club)
        self.assertEqual([s["name"] for s in self.call("get", self.BASE, token).json()["items"]], ["Acme"])
        self.assertEqual(self.call("get", f"{self.BASE}/{body['id']}", token).json()["url"], "https://acme.example.com")

        kept = self.multipart(f"{self.BASE}/{body['id']}", token, self.form(name="Acme 2", end_date="2026-12-31"))
        self.assertEqual(kept.status_code, 200, kept.content)
        self.assertEqual((kept.json()["name"], kept.json()["end_date"]), ("Acme 2", "2026-12-31"))
        self.assertTrue(kept.json()["logo_url"], "no logo part keeps the logo")
        cleared = self.multipart(f"{self.BASE}/{body['id']}", token, self.form(clear_logo="true"))
        self.assertIsNone(cleared.json()["logo_url"])
        self.assertEqual(self.call("delete", f"{self.BASE}/{body['id']}", token).status_code, 204)
        self.assertFalse(Sponsor.objects.exists())

    def test_ended_sponsor_is_inactive(self):
        token = self.admin()
        response = self.multipart(self.BASE, token, self.form(start_date="2020-01-01", end_date="2020-02-01"))
        self.assertFalse(response.json()["is_active"])

    def test_validation_and_upload_errors(self):
        token = self.admin()
        response = self.multipart(self.BASE, token, self.form(start_date="2026-05-01", end_date="2026-04-01"))
        self.assertError(response, 422, "validation_error")
        self.assertIn("end_date", response.json()["error"]["fields"])
        self.assertError(self.multipart(self.BASE, token, {"start_date": "2026-01-01"}), 422, "validation_error")
        self.assertError(self.multipart(self.BASE, token, self.form(logo=png("a.pdf", "application/pdf"))), 415, "unsupported_media_type")
        self.assertError(self.multipart(self.BASE, token, self.form(logo=SimpleUploadedFile("a.png", b"", content_type="image/png"))), 422, "validation_error")
        with override_settings(API_UPLOAD_MAX_BYTES=5):
            self.assertError(self.multipart(self.BASE, token, self.form(logo=png())), 413, "file_too_large")
        self.assertFalse(Sponsor.objects.exists())

    def test_create_is_idempotent(self):
        token = self.admin()
        key = {"HTTP_IDEMPOTENCY_KEY": "sponsor-1"}
        first = self.multipart(self.BASE, token, self.form(), **key)
        second = self.multipart(self.BASE, token, self.form(), **key)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(Sponsor.objects.count(), 1)

    def test_paginates(self):
        token = self.admin()
        for name in ("A", "B", "C"):
            Sponsor.objects.create(club=self.club, name=name, start_date=datetime.date(2026, 1, 1))
        first = self.call("get", f"{self.BASE}?limit=2", token).json()
        self.assertEqual([s["name"] for s in first["items"]], ["A", "B"])
        second = self.call("get", f"{self.BASE}?limit=2&cursor={first['next_cursor']}", token).json()
        self.assertEqual([s["name"] for s in second["items"]], ["C"])

    def test_other_club_is_404_and_hidden(self):
        theirs = Sponsor.objects.create(club=self.other_club(), name="Theirs", start_date=datetime.date(2026, 1, 1))
        token = self.admin()
        self.assertEqual(self.call("get", self.BASE, token).json()["items"], [])
        self.assertError(self.call("get", f"{self.BASE}/{theirs.pk}", token), 404, "not_found")
        self.assertError(self.multipart(f"{self.BASE}/{theirs.pk}", token, self.form()), 404, "not_found")
        self.assertError(self.call("delete", f"{self.BASE}/{theirs.pk}", token), 404, "not_found")
        self.assertTrue(Sponsor.objects.filter(pk=theirs.pk).exists())


class LevelTests(_Base):
    def officials_on(self):
        get_waffle_flag_model().objects.get_or_create(name="officials")[0].clubs.add(self.club)

    def setUp(self):
        super().setUp()
        self.u10 = Team.objects.create(club=self.club, name="U10", short_name="U10")
        self.u12 = Team.objects.create(club=self.club, name="U12", short_name="U12")

    def test_gates(self):
        self.officials_on()
        for path in ("/manage/referee-levels", "/manage/referees", "/manage/official-levels", "/manage/officials"):
            self.assertError(self.call("get", path), 401, "unauthenticated")
            self.assertError(self.call("get", path, self.plain), 403, "forbidden")
        coach = self.coach()
        for path in ("/manage/referee-levels", "/manage/referees", "/manage/official-levels", "/manage/officials"):
            self.assertEqual(self.call("get", path, coach).status_code, 200, path)
        for path in ("/manage/referee-levels", "/manage/official-levels"):
            self.assertError(self.call("post", path, coach, {"name": "X"}), 403, "forbidden")

    def test_referee_level_crud(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        base = "/manage/referee-levels"
        regional = self.call("post", base, token, {"name": "Regional", "team_ids": [str(self.u10.pk)]})
        self.assertEqual(regional.status_code, 201, regional.content)
        national = self.call("post", base, token, {"name": "National", "team_ids": [str(self.u12.pk)], "inherits_from_id": regional.json()["id"]})
        self.assertEqual(national.status_code, 201, national.content)
        self.assertEqual(national.json()["inherits_from"]["name"], "Regional")
        self.assertEqual([lv["name"] for lv in self.call("get", base, token).json()["items"]], ["National", "Regional"])
        patched = self.call("patch", f"{base}/{regional.json()['id']}", token, {"name": "Local"})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()["name"], [t["name"] for t in patched.json()["teams"]]), ("Local", ["U10"]))
        cleared = self.call("patch", f"{base}/{national.json()['id']}", token, {"inherits_from_id": None})
        self.assertIsNone(cleared.json()["inherits_from"])
        self.assertEqual(self.call("get", f"{base}/{national.json()['id']}", token).json()["name"], "National")
        self.assertEqual(self.call("delete", f"{base}/{national.json()['id']}", token).status_code, 204)
        self.assertEqual(RefereeLevel.objects.count(), 1)

    def test_level_validation(self):
        token = self.admin()
        base = "/manage/referee-levels"
        level = self.call("post", base, token, {"name": "Regional"}).json()
        dup = self.call("post", base, token, {"name": "Regional"})
        self.assertError(dup, 422, "validation_error")
        self.assertIn("name", dup.json()["error"]["fields"])
        other_team = Team.objects.create(club=self.other_club(), name="Theirs", short_name="T")
        response = self.call("post", base, token, {"name": "Bad", "team_ids": [str(other_team.pk)]})
        self.assertError(response, 422, "validation_error")
        self.assertIn("team_ids", response.json()["error"]["fields"])
        loop = self.call("patch", f"{base}/{level['id']}", token, {"inherits_from_id": level["id"]})
        self.assertError(loop, 422, "validation_error")
        self.assertIn("inherits_from_id", loop.json()["error"]["fields"])

    def test_delete_in_use_is_409(self):
        token = self.admin()
        base = "/manage/referee-levels"
        level = self.call("post", base, token, {"name": "Regional"}).json()
        child = self.call("post", base, token, {"name": "National", "inherits_from_id": level["id"]}).json()
        self.assertError(self.call("delete", f"{base}/{level['id']}", token), 409, "level_in_use")
        held = RefereeProfile.objects.create(club=self.club, member=self.child, level=RefereeLevel.objects.get(pk=child["id"]))
        self.assertError(self.call("delete", f"{base}/{child['id']}", token), 409, "level_in_use")
        held.delete()
        self.assertEqual(self.call("delete", f"{base}/{child['id']}", token).status_code, 204)
        self.assertEqual(self.call("delete", f"{base}/{level['id']}", token).status_code, 204)

    def test_other_club_level_is_404(self):
        theirs = RefereeLevel.objects.create(club=self.other_club(), name="Theirs")
        token = self.admin()
        for method, data in (("get", None), ("patch", {"name": "x"}), ("delete", None)):
            self.assertError(self.call(method, f"/manage/referee-levels/{theirs.pk}", token, data), 404, "not_found")
        self.assertEqual(self.call("get", "/manage/referee-levels", token).json()["items"], [])
        mine = self.call("post", "/manage/referee-levels", token, {"name": "Mine", "inherits_from_id": str(theirs.pk)})
        self.assertError(mine, 422, "validation_error")

    def test_create_is_idempotent(self):
        token = self.admin()
        key = {"HTTP_IDEMPOTENCY_KEY": "level-1"}
        first = self.call("post", "/manage/referee-levels", token, {"name": "Regional"}, **key)
        second = self.call("post", "/manage/referee-levels", token, {"name": "Regional"}, **key)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(RefereeLevel.objects.count(), 1)

    def test_referee_list(self):
        token = self.admin()
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        level.teams.add(self.u10, self.u12)
        profile = RefereeProfile.objects.create(club=self.club, member=self.child, level=level, valid_until=datetime.date.today() + datetime.timedelta(days=30))
        profile.excluded_teams.add(self.u12)
        RefereeProfile.objects.create(club=self.club, member=self.member)
        theirs_club = self.other_club()
        RefereeProfile.objects.create(club=theirs_club, member=self.stranger(), level=RefereeLevel.objects.create(club=theirs_club, name="Theirs"))
        body = self.call("get", "/manage/referees", token).json()
        rows = {r["first_name"]: r for r in body["items"]}
        self.assertEqual(set(rows), {"Kim", "Pat"})
        self.assertTrue(rows["Kim"]["is_eligible"])
        self.assertEqual([t["name"] for t in rows["Kim"]["eligible_teams"]], ["U10"])
        self.assertEqual(rows["Kim"]["level"]["name"], "Regional")
        self.assertFalse(rows["Pat"]["is_eligible"])
        self.assertEqual(rows["Pat"]["eligible_teams"], [])
        first = self.call("get", "/manage/referees?limit=1", token).json()
        self.assertEqual(len(first["items"]), 1)
        self.assertTrue(first["next_cursor"])

    def test_officials_need_the_feature(self):
        token = self.admin()
        for method, path, data in (("get", "/manage/official-levels", None), ("post", "/manage/official-levels", {"name": "X"}), ("get", "/manage/officials", None)):
            self.assertError(self.call(method, path, token, data), 404, "not_found")
        self.officials_on()
        self.assertEqual(self.call("get", "/manage/official-levels", token).status_code, 200)

    def test_official_levels_and_list(self):
        self.officials_on()
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        created = self.call("post", "/manage/official-levels", token, {"name": "Table", "team_ids": [str(self.u10.pk)]})
        self.assertEqual(created.status_code, 201, created.content)
        level = OfficialLevel.objects.get(pk=created.json()["id"])
        OfficialProfile.objects.create(club=self.club, member=self.child, level=level, valid_until=datetime.date.today() + datetime.timedelta(days=3))
        rows = self.call("get", "/manage/officials", token).json()["items"]
        self.assertEqual([(r["first_name"], r["is_eligible"], [t["name"] for t in r["eligible_teams"]]) for r in rows], [("Kim", True, ["U10"])])
        self.assertError(self.call("delete", f"/manage/official-levels/{level.pk}", token), 409, "level_in_use")
        theirs = OfficialLevel.objects.create(club=self.other_club(), name="Theirs")
        self.assertError(self.call("get", f"/manage/official-levels/{theirs.pk}", token), 404, "not_found")
        patched = self.call("patch", f"/manage/official-levels/{level.pk}", token, {"name": "Scorer"})
        self.assertEqual(patched.json()["name"], "Scorer")


class VolunteerTests(_Base):
    BASE = "/manage/volunteers"

    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        self.position = Position.objects.create(club=self.club, name="Manager", short_name="TM", staff_position=True)
        self.player_position = Position.objects.create(club=self.club, name="Forward", short_name="F")

    def pending(self, member=None, club=None, season=None):
        from club.models import ClubMembership

        club, season = club or self.club, season or self.season
        member = member or self.child
        membership, _created = ClubMembership.objects.get_or_create(club=club, member=member, season=season, defaults={"status": "active"})
        batch = RegistrationBatch.objects.create(club=club, season=season, contact_first_name="A", contact_last_name="B", contact_email="a@example.com")
        return RegistrationDetails.objects.create(membership=membership, batch=batch, entry_kind=RegistrationDetails.EntryKind.VOLUNTEER, requested_team=self.team if club == self.club else None)

    def test_gates(self):
        self.assertError(self.call("get", self.BASE), 401, "unauthenticated")
        self.assertError(self.call("get", self.BASE, self.plain), 403, "forbidden")
        details = self.pending()
        coach = self.coach()
        self.assertError(self.call("get", self.BASE, coach), 403, "forbidden")
        self.assertError(self.call("post", f"{self.BASE}/{details.pk}/place", coach, {"team_id": str(self.team.pk), "position_id": str(self.position.pk)}), 403, "forbidden")

    def test_list_and_place(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        details = self.pending()
        body = self.call("get", self.BASE, token).json()
        self.assertEqual(body["season"]["id"], str(self.season.pk))
        self.assertEqual([(p["id"], p["first_name"], p["requested_team"]["name"]) for p in body["pending"]], [(str(details.pk), "Kim", "U12")])
        self.assertEqual(body["volunteers"], [])

        placed = self.call("post", f"{self.BASE}/{details.pk}/place", token, {"team_id": str(self.team.pk), "position_id": str(self.position.pk)})
        self.assertEqual(placed.status_code, 201, placed.content)
        self.assertEqual((placed.json()["team"]["name"], placed.json()["position"]["name"]), ("U12", "Manager"))
        details.refresh_from_db()
        self.assertIsNotNone(details.resulting_staff_assignment_id)
        after = self.call("get", self.BASE, token).json()
        self.assertEqual(after["pending"], [])
        self.assertEqual([(v["first_name"], v["assignments"][0]["team"]["name"]) for v in after["volunteers"]], [("Kim", "U12")])
        self.assertError(self.call("post", f"{self.BASE}/{details.pk}/place", token, {"team_id": str(self.team.pk), "position_id": str(self.position.pk)}), 404, "not_found")

    def test_place_validation_and_conflict(self):
        token = self.admin()
        details = self.pending()
        response = self.call("post", f"{self.BASE}/{details.pk}/place", token, {"team_id": str(self.team.pk), "position_id": str(self.player_position.pk)})
        self.assertError(response, 422, "validation_error")
        self.assertIn("position_id", response.json()["error"]["fields"])
        StaffAssignment.objects.create(team=self.team, member=self.child, season=self.season, position=self.position)
        self.assertError(self.call("post", f"{self.BASE}/{details.pk}/place", token, {"team_id": str(self.team.pk), "position_id": str(self.position.pk)}), 409, "already_placed")
        details.refresh_from_db()
        self.assertIsNone(details.resulting_staff_assignment_id)

    def test_other_club_request_is_404(self):
        other = self.other_club()
        from club.models import Season

        season = Season.objects.create(club=other, start_date=self.season.start_date, end_date=self.season.end_date)
        theirs = self.pending(member=self.stranger(), club=other, season=season)
        token = self.admin()
        self.assertError(self.call("post", f"{self.BASE}/{theirs.pk}/place", token, {"team_id": str(self.team.pk), "position_id": str(self.position.pk)}), 404, "not_found")
        self.assertEqual(self.call("get", self.BASE, token).json()["pending"], [])
