"""Private member photos: files live in private storage and are only streamed after a permission check."""

import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from members.models import Member
from members.services.photos import can_view_member_photo
from rosterchief.storage import private_storage

User = get_user_model()
# 1x1 transparent PNG
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


class PrivatePhotoTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="rc-private-test-")
        cls._override = override_settings(PRIVATE_MEDIA_ROOT=cls._tmp)
        cls._override.enable()
        # Club admins need two-factor auth outside local development; these tests are about photo access, not MFA.
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()  # requests through a club domain leave the connection on that club's schema
        cls._mfa.stop()
        cls._override.disable()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@photo.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)  # tenant-users needs the public tenant row
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Photo FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        cls.other_club = create_club("Other FC", make_user("owner2@photo.test"))
        with schema_context(cls.club.schema_name):
            cls.member = Member.objects.create(first_name="Mia", last_name="Member", email="mia@photo.test")
            cls.member.player_photo.save("player.png", ContentFile(PNG))
            cls.member.profile_photo.save("profile.png", ContentFile(PNG))
            cls.plain = Member.objects.create(first_name="Pim", last_name="Plain")
            cls.plain.player_photo.save("only-player.png", ContentFile(PNG))
        cls.member_user, _ = grant_login(cls.club, cls.member)
        cls.teammate = make_user("teammate@photo.test", first="Tom", last="Teammate")
        cls.stranger = make_user("stranger@photo.test", first="Sam")
        with schema_context(cls.club.schema_name):
            mate = Member.objects.create(first_name="Tom", last_name="Teammate", email="teammate@photo.test")
        grant_login(cls.club, mate)  # an ordinary club user: role "member", no permissions

    def get(self, name, member=None, *, user=None, token=False, host=None):
        url = reverse(f"members:{name}", args=[(member or self.member).pk])
        client, headers = Client(), {"HTTP_HOST": host or self.host}
        if user is not None:
            if token:
                headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
            else:
                client.force_login(user, backend="features.backends.FeatureAwareAuthenticationBackend")
        return client.get(url, **headers)

    def assert_image(self, r):
        self.assertEqual(r.status_code, 200, getattr(r, "content", b"")[:200])
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertEqual(b"".join(r.streaming_content), PNG)

    # --- storage --------------------------------------------------------------------------------------------------

    def test_files_are_in_private_storage_and_have_no_storage_url(self):
        self.assertTrue(private_storage.exists(self.member.player_photo.name))
        with self.assertRaises(ValueError):
            private_storage.url(self.member.player_photo.name)

    def test_photo_urls_point_at_the_views_not_at_files(self):
        self.assertEqual(self.member.player_photo.url.split("?")[0], reverse("members:player_photo", args=[self.member.pk]))
        self.assertEqual(self.member.profile_photo.url.split("?")[0], reverse("members:profile_photo", args=[self.member.pk]))
        self.assertTrue(self.member.photo_url.startswith(reverse("members:photo", args=[self.member.pk])))
        self.assertIn("?v=", self.member.photo_url)  # cache-busting version, changes with the file name
        self.assertNotIn("private_media", self.member.photo_url)
        with schema_context(self.club.schema_name):
            self.assertIsNone(Member.objects.create(first_name="No", last_name="Photo").photo_url)

    # --- who may see what -----------------------------------------------------------------------------------------

    def test_anonymous_gets_404_not_a_login_redirect(self):
        for name in ("photo", "player_photo", "profile_photo"):
            self.assertEqual(self.get(name).status_code, 404, name)

    def test_the_member_themselves(self):
        for name in ("photo", "player_photo", "profile_photo"):
            self.assert_image(self.get(name, user=self.member_user))

    def test_club_admin_sees_player_and_profile_photos(self):
        for name in ("photo", "player_photo", "profile_photo"):
            self.assert_image(self.get(name, user=self.owner))

    def test_a_plain_club_user_sees_profile_but_not_the_player_photo(self):
        self.assert_image(self.get("profile_photo", user=self.teammate))
        self.assert_image(self.get("photo", user=self.teammate))  # effective picture = profile picture
        self.assertEqual(self.get("player_photo", user=self.teammate).status_code, 404)

    def test_effective_picture_falls_back_to_the_player_photo_for_club_users(self):
        self.assert_image(self.get("photo", self.plain, user=self.teammate))
        self.assertEqual(self.get("player_photo", self.plain, user=self.teammate).status_code, 404)  # direct asks stay strict

    def test_a_user_who_is_not_part_of_the_club_sees_nothing(self):
        for name in ("photo", "player_photo", "profile_photo"):
            self.assertEqual(self.get(name, user=self.stranger).status_code, 404, name)

    def test_a_manager_of_another_club_has_no_authority_here(self):
        other_owner = User.objects.get(email="owner2@photo.test")
        for name in ("photo", "player_photo", "profile_photo"):
            self.assertEqual(self.get(name, user=other_owner).status_code, 404, name)

    def test_public_consent_opens_only_the_player_photo(self):
        with schema_context(self.club.schema_name):
            Member.objects.filter(pk=self.member.pk).update(photo_public_consent=True)
        self.assert_image(self.get("player_photo"))  # anonymous
        self.assertEqual(self.get("profile_photo").status_code, 404)
        # A signed-in user who is not part of the club never gets this far with a session cookie (tenant-users'
        # TenantAccessMiddleware answers 404 for every page of a club they do not belong to); a token client is
        # anonymous to that middleware and falls under the consent rule like any visitor.
        self.assertEqual(self.get("player_photo", user=self.stranger).status_code, 404)
        self.assert_image(self.get("player_photo", user=self.stranger, token=True))

    def test_blocked_account_loses_access(self):
        User.objects.filter(pk=self.member_user.pk).update(is_active=False)
        self.assertEqual(self.get("profile_photo", user=self.member_user).status_code, 404)

    def test_the_session_token_works_like_the_cookie(self):
        self.assert_image(self.get("player_photo", user=self.owner, token=True))
        self.assertEqual(self.get("player_photo", user=self.stranger, token=True).status_code, 404)

    def test_unknown_member_and_missing_file_are_404(self):
        import uuid

        client = Client()
        client.force_login(self.owner, backend="features.backends.FeatureAwareAuthenticationBackend")
        self.assertEqual(client.get(reverse("members:photo", args=[uuid.uuid4()]), HTTP_HOST=self.host).status_code, 404)
        private_storage.delete(self.plain.player_photo.name)
        self.assertEqual(self.get("player_photo", self.plain, user=self.owner).status_code, 404)

    def test_a_photo_cannot_be_read_through_another_clubs_host(self):
        other_host = Domain.objects.get(tenant=self.other_club, is_primary=True).domain
        self.assertEqual(self.get("player_photo", user=self.owner, host=other_host).status_code, 404)  # no such member in that club

    # --- response hardening ---------------------------------------------------------------------------------------

    def test_response_headers(self):
        r = self.get("player_photo", user=self.owner)
        self.assertEqual(r["Cache-Control"], "private, max-age=3600")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", r["Content-Security-Policy"])
        self.assertIn("Cookie", r["Vary"])
        self.assertIn("X-Session-Token", r["Vary"])

    def test_svg_and_non_images_are_never_served_as_such(self):
        with schema_context(self.club.schema_name):
            m = Member.objects.create(first_name="Svg", last_name="Test")
            m.player_photo.save("evil.svg", ContentFile(b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>"))
        r = self.get("player_photo", m, user=self.owner)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/octet-stream")

    # --- the rules as a function ------------------------------------------------------------------------------------

    def test_permission_function_matrix(self):
        with schema_context(self.club.schema_name):
            m = Member.objects.get(pk=self.member.pk)
            self.assertFalse(can_view_member_photo(None, m, self.club, field="player_photo"))
            self.assertTrue(can_view_member_photo(self.member_user, m, self.club, field="player_photo"))
            self.assertTrue(can_view_member_photo(self.owner, m, self.club, field="player_photo"))
            self.assertFalse(can_view_member_photo(self.teammate, m, self.club, field="player_photo"))
            self.assertTrue(can_view_member_photo(self.teammate, m, self.club, field="profile_photo"))
            self.assertFalse(can_view_member_photo(self.teammate, m, None, field="profile_photo"))  # no club context
