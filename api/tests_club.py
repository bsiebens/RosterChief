"""Club branding, club picker, /me permissions and the team options endpoint."""

import io
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django_tenants.utils import schema_context
from PIL import Image

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from evaluations.testing import evaluations_feature
from members.models import Member
from teams.models import Team

BRANDING_KEYS = {"slug", "name", "is_platform", "logo_url", "icons", "primary_color", "secondary_color", "primary_content_color", "secondary_content_color"}
MY_CLUB_KEYS = {"slug", "name", "host", "logo_url", "primary_color", "primary_content_color"}


def tiny_png() -> SimpleUploadedFile:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, "PNG")
    return SimpleUploadedFile("logo.png", buf.getvalue(), content_type="image/png")


class ClubApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._mfa.stop()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@club.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        cls.public_host = "testserver"
        Domain.objects.create(domain=cls.public_host, tenant=public, is_primary=True)
        cls.club = create_club("Beta FC", cls.owner)
        cls.club.primary_color = "#1e40af"
        cls.club.secondary_color = "#fde047"
        cls.club.save()
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        with schema_context(cls.club.schema_name):
            Team.objects.create(name="U14", short_name="U14")
            Team.objects.create(name="U10", short_name="U10")
            Team.objects.create(name="U12", short_name="U12")
            cls.member = Member.objects.create(first_name="Plain", last_name="Member", email="plain@club.test")
        cls.plain_user, _ = grant_login(cls.club, cls.member)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def get(self, path, host=None, user=None):
        headers = {"HTTP_HOST": host or self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        return Client().get("/api/v1" + path, **headers)

    # -- /club --

    def test_club_branding_anonymous(self):
        r = self.get("/club")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(set(body), BRANDING_KEYS)
        self.assertEqual(body["slug"], self.club.slug)
        self.assertEqual(body["name"], "Beta FC")
        self.assertFalse(body["is_platform"])
        self.assertIsNone(body["logo_url"])
        self.assertEqual(body["primary_color"], "#1e40af")
        self.assertEqual(body["secondary_color"], "#fde047")
        self.assertEqual(body["primary_content_color"], self.club.primary_content_color)
        self.assertEqual(body["primary_content_color"], "#ffffff")
        self.assertEqual(body["secondary_content_color"], "#000000")

    def test_club_branding_with_logo(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            club = Club.objects.get(pk=self.club.pk)
            club.logo.save("logo.png", tiny_png())
            body = self.get("/club").json()
        self.assertTrue(body["logo_url"].startswith("/media/"), body["logo_url"])
        self.assertTrue(body["logo_url"].endswith(".png"))
        self.assertEqual(set(body), BRANDING_KEYS)

    def test_club_branding_on_platform_host(self):
        r = self.get("/club", host=self.public_host)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["is_platform"])
        self.assertIsNone(body["logo_url"])
        self.assertEqual(body["primary_color"], "")
        self.assertEqual(body["secondary_color"], "")
        self.assertNotEqual(body["slug"], self.club.slug)

    # -- /my-clubs --

    def test_my_clubs_anonymous_is_refused(self):
        self.assertEqual(self.get("/my-clubs", host=self.public_host).status_code, 401)

    def test_my_clubs_lists_user_clubs_ordered_by_name(self):
        alpha = create_club("Alpha FC", self.owner)
        alpha.primary_color = "#be185d"
        alpha.save()
        r = self.get("/my-clubs", host=self.public_host, user=self.owner)
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()
        self.assertEqual([c["name"] for c in rows], ["Alpha FC", "Beta FC"])
        for row, club in zip(rows, (alpha, self.club), strict=True):
            self.assertEqual(set(row), MY_CLUB_KEYS)
            self.assertEqual(row["slug"], club.slug)
            self.assertEqual(row["host"], Domain.objects.get(tenant=club, is_primary=True).domain)
            self.assertEqual(row["primary_color"], club.primary_color)
            self.assertEqual(row["primary_content_color"], club.primary_content_color)
            self.assertIsNone(row["logo_url"])

    def test_my_clubs_excludes_archived_public_and_domainless(self):
        archived = create_club("Archived FC", self.owner)
        archived.archived_at = timezone.now()
        archived.save()
        nodomain = create_club("Nodomain FC", self.owner)
        Domain.objects.filter(tenant=nodomain).delete()
        public = Club.objects.get(schema_name="public")
        if not self.owner.tenants.filter(pk=public.pk).exists():
            public.add_user(self.owner)
        names = [c["name"] for c in self.get("/my-clubs", host=self.public_host, user=self.owner).json()]
        self.assertEqual(names, ["Beta FC"])

    def test_my_clubs_only_own_clubs_and_empty_for_none(self):
        other = make_user("lonely@club.test")
        self.assertEqual(self.get("/my-clubs", host=self.public_host, user=other).json(), [])
        create_club("Gamma FC", self.owner)
        names = [c["name"] for c in self.get("/my-clubs", host=self.public_host, user=self.plain_user).json()]
        self.assertEqual(names, ["Beta FC"])

    # -- /me --

    def test_me_permissions_and_admin_flag(self):
        self.enterContext(evaluations_feature())
        r = self.get("/me", user=self.owner)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertIn("members.manage_members", body["permissions"])
        self.assertEqual(body["permissions"], sorted(body["permissions"]))
        self.assertTrue(body["is_club_admin"])

        self.assertTrue(body["can_manage_members"])
        self.assertTrue(body["can_manage_settings"])
        self.assertTrue(body["can_execute_evaluations"])
        self.assertEqual(body["managed_team_ids"], [])

        # Plain members are not let in: the management app is for admins, owners and team staff.
        r = self.get("/me", user=self.plain_user)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "no_access")

    # -- /members/teams --

    def test_team_options(self):
        self.assertEqual(self.get("/members/teams", user=self.plain_user).status_code, 403)
        r = self.get("/members/teams", user=self.owner)
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()
        self.assertEqual([t["name"] for t in rows], ["U10", "U12", "U14"])
        for t in rows:
            self.assertEqual(set(t), {"id", "name"})
        self.assertEqual(self.get("/members/teams", user=self.owner).json(), rows)

    def test_team_options_anonymous_is_refused(self):
        self.assertEqual(self.get("/members/teams").status_code, 401)
