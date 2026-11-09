"""Club settings API (``/club/settings``): admin-only access, platform 404, validation, logo upload and previews."""

import io
import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django_tenants.utils import schema_context
from PIL import Image

from club import previews
from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import login_session, make_user
from members.models import AuditEntry, Member

BASE = "/api/v1/club/settings"


def image_file(fmt="PNG", name="logo.png", size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, "#336699").save(buf, format=fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class ClubSettingsApiTests(TestCase):
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
        cls.owner = make_user("owner@set.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Settings FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        with schema_context(cls.club.schema_name):
            cls.coach = Member.objects.create(first_name="Coach", last_name="Test", email="coach@set.test")
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        self._media = override_settings(MEDIA_ROOT=self._tmp())
        self._media.enable()
        self.addCleanup(self._media.disable)

    def _tmp(self):
        import tempfile

        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return d

    def call(self, method, path, user=None, data=None, host=None, **extra):
        headers = {"HTTP_HOST": host or self.host}
        if user is not None:
            headers["HTTP_X_SESSION_TOKEN"] = login_session(user)
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **extra, **headers)

    def admin(self, method, path, data=None, **extra):
        return self.call(method, path, self.owner, data, **extra)

    def club_now(self):
        return Club.objects.get(pk=self.club.pk)

    def audit(self, action):
        with schema_context(self.club.schema_name):
            return list(AuditEntry.objects.filter(action=action))

    # -- access --

    def test_admin_only(self):
        for method, path, data in [("get", "", None), ("patch", "", {"name": "X"}), ("get", "/previews", None), ("get", "/previews/login_granted/html", None), ("delete", "/logo", None)]:
            self.assertEqual(self.call(method, path, self.coach_user, data).status_code, 403, (method, path))
            self.assertIn(self.call(method, path, None, data).status_code, (401, 403), (method, path))
        r = Client().post(BASE + "/logo", {"file": image_file()}, HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.coach_user))
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.club_now().logo)

    def test_platform_schema_is_404(self):
        with mock.patch("members.services.access.can_manage_settings", return_value=True):  # an admin on the platform host
            for method, path, data in [("get", "", None), ("patch", "", {"name": "X"}), ("get", "/previews", None), ("get", "/previews/login_granted/html", None), ("delete", "/logo", None)]:
                r = self.call(method, path, self.owner, data, host="testserver")
                self.assertEqual(r.status_code, 404, (method, path, r.content))

    # -- read / update --

    def test_get_settings(self):
        body = self.admin("get", "").json()
        self.assertEqual(body["name"], "Settings FC")
        self.assertIsNone(body["logo_url"])
        for key in ("legal_name", "contact_email", "website", "legal_address", "legal_zip_code", "legal_city", "legal_country", "vat_id", "payment_instructions", "primary_color", "secondary_color", "primary_text_color", "secondary_text_color"):
            self.assertIn(key, body)

    def test_patch_updates_and_audits(self):
        r = self.admin("patch", "", {"name": "Renamed FC", "contact_email": "info@club.test", "website": "https://club.test", "legal_country": "BE", "primary_color": "#1e40af", "vat_id": "BE0123456789"})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["name"], body["legal_country"], body["primary_color"]), ("Renamed FC", "BE", "#1e40af"))
        club = self.club_now()
        self.assertEqual((club.name, club.contact_email, str(club.legal_country)), ("Renamed FC", "info@club.test", "BE"))
        (entry,) = self.audit("club.settings_updated")
        self.assertEqual(entry.changes["name"], {"from": "Settings FC", "to": "Renamed FC"})
        # same values again: no extra entry; untouched fields stay
        self.admin("patch", "", {"name": "Renamed FC"})
        self.assertEqual(len(self.audit("club.settings_updated")), 1)
        self.assertEqual(self.club_now().primary_color, "#1e40af")

    def test_empty_string_clears_a_colour(self):
        self.admin("patch", "", {"primary_color": "#1e40af", "primary_text_color": "#ffffff"})
        r = self.admin("patch", "", {"primary_color": "", "primary_text_color": ""})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["primary_color"], r.json()["primary_text_color"]), ("", ""))

    def test_validation_errors_are_per_field(self):
        r = self.admin("patch", "", {"name": " ", "primary_color": "blue", "contact_email": "nope", "website": "not a url", "legal_country": "ZZZ", "secondary_color": "#FFFFFF"})
        self.assertEqual(r.status_code, 422)
        body = r.json()
        self.assertEqual(body["code"], "invalid")
        for field in ("name", "primary_color", "contact_email", "website", "legal_country", "secondary_color"):
            self.assertIn(field, body["errors"], body)
        self.assertEqual(self.club_now().name, "Settings FC")  # all or nothing

    # -- logo --

    def test_logo_upload_and_delete(self):
        r = Client().post(BASE + "/logo", {"file": image_file()}, HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.owner))
        self.assertEqual(r.status_code, 200, r.content)
        url = r.json()["logo_url"]
        self.assertTrue(url and "logo-" in url)
        self.assertTrue(self.club_now().logo)
        self.assertEqual(len(self.audit("club.logo_changed")), 1)
        self.assertEqual(self.admin("delete", "/logo").json()["logo_url"], None)
        self.assertFalse(self.club_now().logo)
        self.assertEqual(len(self.audit("club.logo_changed")), 2)
        self.assertEqual(self.admin("delete", "/logo").status_code, 200)  # no-op
        self.assertEqual(len(self.audit("club.logo_changed")), 2)

    def upload(self, f):
        return Client().post(BASE + "/logo", {"file": f}, HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.owner))

    def test_logo_rejects_bad_files(self):
        r = self.upload(SimpleUploadedFile("x.png", b"not an image", content_type="image/png"))
        self.assertEqual(r.status_code, 422)
        self.assertIn("file", r.json()["errors"])
        r = self.upload(image_file("GIF", "x.gif"))
        self.assertEqual(r.status_code, 422)
        big = SimpleUploadedFile("big.png", b"0" * (5 * 1024 * 1024 + 1), content_type="image/png")
        self.assertEqual(self.upload(big).status_code, 422)
        self.assertFalse(self.club_now().logo)

    # -- previews --

    def test_previews_list(self):
        items = self.admin("get", "/previews").json()
        keys = [i["key"] for i in items]
        self.assertIn("login_granted", keys)
        self.assertIn("password_reset_key", keys)
        self.assertIn("email_confirmation", keys)
        for item in items:
            self.assertEqual(set(item), {"key", "kind", "title", "description"})
            self.assertIn(item["kind"], ("email", "pdf"))

    def test_every_preview_renders_with_current_branding(self):
        self.admin("patch", "", {"name": "Brand FC", "primary_color": "#123456", "secondary_color": "#abcdef"})
        self.upload(image_file())
        from urllib.parse import unquote

        for item in self.admin("get", "/previews").json():
            if item["kind"] != "email":
                continue
            r = self.admin("get", f"/previews/{item['key']}/html")
            self.assertEqual(r.status_code, 200, (item["key"], r.content[:300]))
            self.assertTrue(r["Content-Type"].startswith("text/html"))
            html = r.content.decode()
            self.assertIn("Brand FC", html, item["key"])
            self.assertIn("#123456", html, item["key"])
            self.assertIn("data:image/png;base64,", html, item["key"])
            self.assertNotIn("cid:", html)
            subject = unquote(r["X-Preview-Subject"])
            self.assertIn("Brand FC", subject, item["key"])
        r = self.admin("get", "/previews/login_granted/html?lang=nl")
        self.assertIn('lang="nl"', r.content.decode())

    def test_preview_without_logo_shows_initials(self):
        html = self.admin("get", "/previews/password_reset_key/html").content.decode()
        self.assertIn(">SF<", html)

    def test_unknown_preview_is_404(self):
        self.assertEqual(self.admin("get", "/previews/nope/html").status_code, 404)
        self.assertEqual(self.admin("get", "/previews/nope/file").status_code, 404)

    def test_email_previews_have_no_file(self):
        self.assertEqual(self.admin("get", "/previews/login_granted/file").status_code, 404)

    def test_pdf_preview_degrades_to_503_or_returns_pdf(self):
        fake = previews.Preview("sample_pdf", "pdf", "Sample", "A sample PDF")
        with mock.patch.dict(previews.BY_KEY, {"sample_pdf": fake}), mock.patch("club.previews.render_email", return_value=("S", "<html></html>")):
            with mock.patch("club.previews.render_pdf", side_effect=RuntimeError("PDF rendering is not available on this server.")):
                r = self.admin("get", "/previews/sample_pdf/file")
                self.assertEqual(r.status_code, 503)
                self.assertIn("not available", r.json()["detail"])
            with mock.patch("club.previews.render_pdf", return_value=b"%PDF-1.7 fake"):
                r = self.admin("get", "/previews/sample_pdf/file")
                self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))

    # -- shop previews (only for clubs with the shop feature) --

    def with_shop(self):
        from features.entitlements import Entitlement, Entitlements

        shop = Entitlements({"shop": Entitlement(True)})
        for target in ("features.middleware.entitlements_for_request", "features.context.get_current_entitlements"):
            patcher = mock.patch(target, side_effect=lambda *a, **k: shop)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_shop_previews_hidden_without_the_shop_feature(self):
        keys = [i["key"] for i in self.admin("get", "/previews").json()]
        self.assertFalse(set(keys) & previews.SHOP_KEYS)
        for key in previews.SHOP_KEYS:
            self.assertEqual(self.admin("get", f"/previews/{key}/html").status_code, 404)

    def test_shop_previews_listed_and_rendered_with_the_shop_feature(self):
        from urllib.parse import unquote

        self.with_shop()
        self.admin("patch", "", {"name": "Brand FC", "primary_color": "#123456"})
        items = {i["key"]: i for i in self.admin("get", "/previews").json()}
        self.assertEqual({k for k in items if k in previews.SHOP_KEYS}, {"shop_order_registered", "shop_order_ready", "shop_invoice"})
        self.assertEqual((items["shop_order_registered"]["kind"], items["shop_order_ready"]["kind"], items["shop_invoice"]["kind"]), ("email", "email", "pdf"))
        self.assertIn("PDF", items["shop_order_ready"]["description"])
        for lang, word in (("en", "Thanks for your order"), ("nl", "Bedankt voor je bestelling"), ("fr", "Merci pour votre commande")):
            r = self.admin("get", f"/previews/shop_order_registered/html?lang={lang}")
            self.assertEqual(r.status_code, 200, r.content[:300])
            html = r.content.decode()
            self.assertIn(word, html)
            self.assertIn("#123456", html)
            self.assertIn("Brand FC", unquote(r["X-Preview-Subject"]))
        ready = self.admin("get", "/previews/shop_order_ready/html?lang=nl").content.decode()
        self.assertIn("Tuesday 18:00", ready)
        self.assertIn("bijlage", ready)
        invoice = self.admin("get", "/previews/shop_invoice/html")
        self.assertEqual(invoice.status_code, 200, invoice.content[:300])
        html = invoice.content.decode()
        for text in ("INV-2026-0017", "Payments received", "Voucher V-0003", "Cash", "Member discount", "7 DOE"):
            self.assertIn(text, html)
        with mock.patch("club.previews.render_pdf", return_value=b"%PDF-1.7 fake"):
            r = self.admin("get", "/previews/shop_invoice/file")
            self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(self.admin("get", "/previews/shop_order_ready/file").status_code, 404)
