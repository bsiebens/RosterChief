"""Icons generated from a club's logo and handed out by the public branding endpoint."""

import io
import tempfile

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.test import Client, TestCase, override_settings
from PIL import Image

from club.icons import ICO_NAME, icon_urls
from club.models import Club, Domain
from club.services import create_club
from controlpanel.tests import make_user


def png(width=120, height=60, color=(200, 30, 30, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


class ClubIconTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.TemporaryDirectory()
        cls._override = override_settings(MEDIA_ROOT=cls._media.name)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._override.disable()
        cls._media.cleanup()

    @classmethod
    def setUpTestData(cls):
        owner = make_user("icons@owner.test")
        public = Club(schema_name="public", name="Platform", owner=owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Icon FC", owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain

    def branding(self):
        return Client().get("/api/v1/club", HTTP_HOST=self.host).json()

    def test_no_logo_no_icons(self):
        self.assertEqual(self.branding()["icons"], {})

    def test_logo_upload_generates_square_icons_and_replacing_cleans_up(self):
        club = Club.objects.get(pk=self.club.pk)
        club.logo.save("logo.png", ContentFile(png()), save=True)
        icons = self.branding()["icons"]
        self.assertEqual(set(icons), {"ico", "png_32", "png_192", "apple_touch"})
        for key, edge in (("png_32", 32), ("png_192", 192), ("apple_touch", 180)):
            path = icons[key].removeprefix("/media/")
            with default_storage.open(path) as fh:
                self.assertEqual(Image.open(fh).size, (edge, edge))
        with default_storage.open(icons["ico"].removeprefix("/media/")) as fh:
            self.assertEqual(Image.open(fh).format, "ICO")
        self.assertTrue(icons["ico"].endswith(ICO_NAME))

        club = Club.objects.get(pk=self.club.pk)
        club.logo.save("other.png", ContentFile(png(40, 40, (0, 0, 255, 255))), save=True)
        new_icons = self.branding()["icons"]
        self.assertNotEqual(new_icons["ico"], icons["ico"])  # new URLs: no stale browser caches
        self.assertFalse(default_storage.exists(icons["ico"].removeprefix("/media/")))

        club = Club.objects.get(pk=self.club.pk)
        club.logo.delete(save=True)
        self.assertEqual(self.branding()["icons"], {})
        self.assertEqual(icon_urls(Club.objects.get(pk=self.club.pk)), {})

    def test_existing_logo_without_icons_is_generated_lazily(self):
        club = Club.objects.get(pk=self.club.pk)
        # A logo that predates icon generation: set the file without going through save().
        club.logo.name = default_storage.save("clubs/old/logo.png", ContentFile(png()))
        Club.objects.filter(pk=club.pk).update(logo=club.logo.name)
        self.assertEqual(set(self.branding()["icons"]), {"ico", "png_32", "png_192", "apple_touch"})

    def test_unreadable_logo_does_not_break_branding(self):
        club = Club.objects.get(pk=self.club.pk)
        name = default_storage.save("clubs/bad/logo.png", ContentFile(b"not an image"))
        Club.objects.filter(pk=club.pk).update(logo=name)
        response = Client().get("/api/v1/club", HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["icons"], {})
