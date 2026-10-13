import io
import shutil
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from PIL import Image

from club.models import ClubMembership
from members.models import Member
from rosterchief.test_support import make_club, make_season


def _png_bytes(colour="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), colour).save(buffer, format="PNG")
    return buffer.getvalue()


class ImportMemberPhotosTests(TestCase):
    def setUp(self):
        self.club = make_club()
        self.season = make_season(self.club)
        self.member = Member.objects.create(first_name="Lars", last_name="Bakker")
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, license="12345")
        self.directory = Path(tempfile.mkdtemp(prefix="photo-import-"))
        self.addCleanup(shutil.rmtree, self.directory)

    def _run(self, *args):
        out = io.StringIO()
        call_command("import_member_photos", self.club.slug, str(self.directory), *args, stdout=out)
        return out.getvalue()

    def test_attaches_a_photo_by_licence_and_leaves_consent_alone(self):
        (self.directory / "12345.png").write_bytes(_png_bytes())

        output = self._run()

        self.member.refresh_from_db()
        self.assertTrue(self.member.player_photo)
        self.assertEqual(self.member.player_photo.read(), _png_bytes())
        self.assertFalse(self.member.photo_public_consent)
        self.assertIn("Stored 1 photo(s)", output)

    def test_public_switch_turns_on_consent_only_for_stored_photos(self):
        skipped = Member.objects.create(first_name="Eva", last_name="Janssens")
        ClubMembership.objects.create(club=self.club, member=skipped, season=self.season, license="777")
        (self.directory / "12345.png").write_bytes(_png_bytes())
        (self.directory / "777.png").write_bytes(b"not an image")

        self._run("--public")

        self.member.refresh_from_db()
        skipped.refresh_from_db()
        self.assertTrue(self.member.photo_public_consent)
        self.assertEqual(self.member.public_photo, self.member.player_photo)
        self.assertFalse(skipped.photo_public_consent)

    def test_dry_run_with_public_changes_nothing(self):
        (self.directory / "12345.png").write_bytes(_png_bytes())

        self._run("--dry-run", "--public")

        self.member.refresh_from_db()
        self.assertFalse(self.member.player_photo)
        self.assertFalse(self.member.photo_public_consent)

    def test_dry_run_stores_nothing(self):
        (self.directory / "12345.png").write_bytes(_png_bytes())

        output = self._run("--dry-run")

        self.member.refresh_from_db()
        self.assertFalse(self.member.player_photo)
        self.assertIn("Would store 1 photo(s)", output)

    def test_existing_photo_is_kept_unless_replace(self):
        (self.directory / "12345.png").write_bytes(_png_bytes("red"))
        self._run()
        self.member.refresh_from_db()
        first_name = self.member.player_photo.name

        (self.directory / "12345.png").write_bytes(_png_bytes("blue"))
        self.assertIn("already has a player picture", self._run())
        self.member.refresh_from_db()
        self.assertEqual(self.member.player_photo.name, first_name)

        self._run("--replace")
        self.member.refresh_from_db()
        self.assertEqual(self.member.player_photo.read(), _png_bytes("blue"))

    def test_unknown_ambiguous_and_broken_files_are_reported_not_stored(self):
        other = Member.objects.create(first_name="Eva", last_name="Janssens")
        ClubMembership.objects.create(club=self.club, member=other, season=self.season, license="777")
        third = Member.objects.create(first_name="Tom", last_name="Peeters")
        ClubMembership.objects.create(club=self.club, member=third, season=self.season, license="777")
        (self.directory / "99999.png").write_bytes(_png_bytes())
        (self.directory / "777.png").write_bytes(_png_bytes())
        (self.directory / "12345.jpg").write_bytes(b"not an image")
        (self.directory / "notes.txt").write_text("hi")

        output = self._run()

        self.assertIn("no member with licence '99999'", output)
        self.assertIn("is on 2 different members", output)
        self.assertIn("not a readable image", output)
        self.assertIn("Stored 0 photo(s); 1 skipped; 3 problem(s).", output)
        for member in (self.member, other, third):
            member.refresh_from_db()
            self.assertFalse(member.player_photo)

    def test_licence_from_another_club_does_not_match(self):
        elsewhere = make_club(name="Other", slug="other")
        stranger = Member.objects.create(first_name="Ann", last_name="Smet")
        ClubMembership.objects.create(club=elsewhere, member=stranger, season=make_season(elsewhere), license="555")
        (self.directory / "555.png").write_bytes(_png_bytes())

        self.assertIn("no member with licence '555'", self._run())
        stranger.refresh_from_db()
        self.assertFalse(stranger.player_photo)

    def test_unknown_club_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command("import_member_photos", "nope", str(self.directory), stdout=io.StringIO())
