from datetime import date, timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.serializers.json import DjangoJSONEncoder
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context
from PIL import Image

from club.models import Club, Domain, User
from club.services import club_domain, create_club, schema_name_for
from controlpanel.services.club_backup import FORMAT, BackupError, _static_svg, export_club, preview, read_backup, restore_club
from controlpanel.tests import login_session, make_user
from events.models import Attendance, Event, EventReferee, Season
from members.models import ClubMembership, Family, FamilyMembership, FeePayment, Member
from shop.models import Order, OrderLine, Product
from shop.settings_models import ShopNumberSequence
from sponsoring.models import Sponsor
from teams.models import Team


def archive_of(manifest, **files):
    import json

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, cls=DjangoJSONEncoder))
        for name, body in files.items():
            archive.writestr(name, body)
    return SimpleUploadedFile("backup.zip", buffer.getvalue(), content_type="application/zip")


def legacy_manifest():
    today = timezone.now()
    user_id, member_id, season_id, team_id = [str(uuid4()) for _ in range(4)]

    def row(pk, **fields):
        return {"fields": {"id": pk, "created": today, "modified": today, **fields}, "many": {}}

    return {
        "format": FORMAT,
        "version": 1,
        "exported_at": today,
        "club": {"slug": "legacy", "owner": user_id, "fields": {"name": "Legacy Club", "shop_open": True}},
        "accounts": [{"id": user_id, "email": "legacy@backup.test", "active": True, "enrolled": True}],
        "referee_types": {},
        "records": {
            "members.member": [row(member_id, user_id=user_id, first_name="Legacy", last_name="Owner")],
            "club.season": [row(season_id, start_date=date(2026, 8, 1), end_date=date(2027, 7, 31))],
            "teams.team": [row(team_id, name="Legacy team", short_name="U14", import_sources=[])],
        },
    }


class BackupValidationTests(SimpleTestCase):
    def test_preview_accepts_v1_and_rejects_missing_relationship(self):
        manifest = legacy_manifest()
        details = preview(archive_of(manifest))
        self.assertEqual((details["version"], details["records"], details["accounts"]), (1, 3, 1))
        manifest["records"]["members.member"][0]["fields"]["user_id"] = str(uuid4())
        report = preview(archive_of(manifest))["report"]
        self.assertGreater(report["blocked"], 0)
        members = next(section for section in report["sections"] if section["key"] == "members.member")
        self.assertEqual(members["blocked"], 1)
        self.assertIn("Missing referenced record", members["items"][0]["reasons"][0])
        # Everything else stays listed, and restorable.
        self.assertGreater(report["restorable"], 0)
        with self.assertRaisesMessage(BackupError, "Missing referenced record") as raised, read_backup(archive_of(manifest)):
            pass
        self.assertEqual(raised.exception.report["blocked"], report["blocked"])

    def test_v1_repeated_memberships_preserve_each_invoice_and_share_one_ledger(self):
        manifest = legacy_manifest()
        records = manifest["records"]
        season = records["club.season"][0]["fields"]["id"]
        member = records["members.member"][0]["fields"]["id"]
        membership = str(uuid4())
        submitted_at = manifest["exported_at"]

        def row(**fields):
            return {"fields": {"id": str(uuid4()), "created": submitted_at, "modified": submitted_at, **fields}, "many": {}}

        records["club.clubmembership"] = [row(id=membership, season_id=season, member_id=member)]
        records["registration.registrationbatch"] = [
            row(season_id=season, invoice_sent_at=submitted_at, invoice_number=number, subtotal=price, total=price)
            for number, price in (("REG-1", "125.00"), ("REG-2", "50.00"))
        ]
        records["registration.registrationdetails"] = [
            row(batch_id=batch["fields"]["id"], membership_id=membership, price=price, discount_amount="0.00", entry_kind=kind)
            for batch, price, kind in zip(records["registration.registrationbatch"], ["125.00", "50.00"], ["player", "volunteer"], strict=True)
        ]

        with read_backup(archive_of(manifest)) as (normalized, _, version, warnings):
            self.assertEqual(version, 1)
            self.assertEqual(len(warnings), 1)
            output = normalized["records"]
            self.assertEqual(len(output["registration.registrationdetails"]), 1)
            self.assertEqual(
                {item["fields"]["legacy_invoice_history"]["total"] for item in output["registration.registrationbatch"]},
                {"125.00", "50.00"},
            )

    def test_legacy_svg_keeps_what_is_drawn_and_strips_whatever_is_active(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path style="fill:#112233" d="M 0 0 L 24 24"/></svg>'
        self.assertIn(b"112233", _static_svg(BytesIO(svg)))
        dirty = _static_svg(BytesIO(svg.replace(b"<path", b'<path onclick="alert(1)"').replace(b"</svg>", b'<script>alert(1)</script><image href="https://evil.example/x.png"/></svg>')))
        self.assertIn(b"112233", dirty)
        for forbidden in (b"onclick", b"script", b"image", b"evil"):
            self.assertNotIn(forbidden, dirty)

    def test_svg_from_a_design_tool_is_cleaned_instead_of_refused(self):
        svg = (
            b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
            b'<!-- Generator: Illustrator --><svg xmlns="http://www.w3.org/2000/svg" xmlns:sodipodi="http://sodipodi.example/" viewBox="0 0 10 10">'
            b'<sodipodi:namedview id="x"/><style>.a{fill:#445566}</style><path class="a" d="M0 0h10v10z"/></svg>'
        )
        cleaned = _static_svg(BytesIO(svg))
        self.assertIn(b"445566", cleaned)
        self.assertNotIn(b"sodipodi", cleaned)
        with self.assertRaises(BackupError):
            _static_svg(BytesIO(b'<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>'))
        with self.assertRaises(BackupError):
            _static_svg(BytesIO(b"<html/>"))

    def test_archive_path_traversal_and_missing_media_are_rejected(self):
        manifest = legacy_manifest()
        with self.assertRaisesMessage(BackupError, "invalid or duplicate paths"):
            preview(archive_of(manifest, **{"media/../../example.txt": b"not a club file"}))
        manifest["club"]["fields"]["logo"] = "media/1/missing.png"
        report = preview(archive_of(manifest))["report"]
        self.assertIn("is missing", " ".join(report["club"]))


class ClubBackupRestoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@backup.test", level="superuser")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)

    def test_partial_restore_skips_blocked_records_and_what_depends_on_them(self):
        manifest = legacy_manifest()
        now = manifest["exported_at"]
        event_id, assignment_id = str(uuid4()), str(uuid4())
        manifest["records"]["events.event"] = [{"fields": {"id": event_id, "created": now, "modified": now, "start": now + timedelta(days=2), "title": "Fixture", "kind": "game", "location_id": str(uuid4())}, "many": {}}]
        manifest["records"]["events.eventreferee"] = [{"fields": {"id": assignment_id, "created": now, "modified": now, "event_id": event_id, "external_name": "Ref"}, "many": {}}]
        report = preview(archive_of(manifest))["report"]
        blocked = {section["key"]: section["blocked"] for section in report["sections"]}
        self.assertEqual((blocked["events.event"], blocked["events.eventreferee"]), (1, 1))  # the referee depends on the blocked event
        with self.assertRaises(BackupError) as raised:
            restore_club(archive_of(manifest))
        self.assertEqual(raised.exception.report["blocked"], report["blocked"])
        restored, _, warnings = restore_club(archive_of(manifest), partial=True)
        self.assertIn("Not restored", warnings[-1])
        with schema_context(restored.schema_name):
            self.assertFalse(Event.objects.filter(pk=event_id).exists())
            self.assertFalse(EventReferee.objects.filter(pk=assignment_id).exists())
            self.assertTrue(Team.objects.filter(pk=manifest["records"]["teams.team"][0]["fields"]["id"]).exists())

    def test_excluding_a_section_leaves_it_and_its_dependents_out_without_partial(self):
        manifest = legacy_manifest()
        now = manifest["exported_at"]
        event_id, assignment_id = str(uuid4()), str(uuid4())
        manifest["records"]["events.event"] = [{"fields": {"id": event_id, "created": now, "modified": now, "start": now + timedelta(days=2), "title": "Fixture", "kind": "game"}, "many": {}}]
        manifest["records"]["events.eventreferee"] = [{"fields": {"id": assignment_id, "created": now, "modified": now, "event_id": event_id, "external_name": "Ref"}, "many": {}}]
        report = preview(archive_of(manifest))["report"]
        self.assertEqual(report["blocked"], 0)
        self.assertIn("events.event", report["refs"]["events.eventreferee"])
        restored, _, warnings = restore_club(archive_of(manifest), exclude=["events.event"])
        self.assertIn("Not restored", warnings[-1])
        with schema_context(restored.schema_name):
            self.assertFalse(Event.objects.filter(pk=event_id).exists())
            self.assertFalse(EventReferee.objects.filter(pk=assignment_id).exists())
            self.assertTrue(Team.objects.filter(pk=manifest["records"]["teams.team"][0]["fields"]["id"]).exists())

    def test_svg_logos_restore_for_any_public_image_and_are_stored_cleaned(self):
        import mimetypes

        manifest = legacy_manifest()
        now = manifest["exported_at"]
        sponsor_id = str(uuid4())
        asset = "media/legacy-sponsor.svg"
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect width="4" height="4" fill="#123456" onload="alert(1)"/></svg>'
        manifest["records"]["club.sponsor"] = [{"fields": {"id": sponsor_id, "created": now, "modified": now, "name": "Svg sponsor", "logo": asset, "url": "", "start_date": date(2026, 8, 1), "end_date": None, "logo_width": None, "logo_height": None}, "many": {}}]
        media_root = self.enterContext(TemporaryDirectory())
        self.enterContext(self.settings(MEDIA_ROOT=media_root))
        manifest["club"]["fields"]["logo"] = "media/legacy-crest.svg"
        restored, _, warnings = restore_club(archive_of(manifest, **{asset: svg, "media/legacy-crest.svg": svg}))
        self.assertTrue(any("club logo is an SVG" in warning for warning in warnings))
        with schema_context(restored.schema_name):
            sponsor = Sponsor.objects.get(pk=sponsor_id)
            self.assertTrue(sponsor.logo.name.endswith(".svg"))
            self.assertEqual(mimetypes.guess_type(sponsor.logo.url)[0], "image/svg+xml")
            with sponsor.logo.open("rb") as stored:
                content = stored.read()
        self.assertIn(b"123456", content)
        self.assertNotIn(b"onload", content)

    def test_v1_restore_keeps_public_ids_and_creates_login_without_credentials(self):
        manifest = legacy_manifest()
        sport = {"slug": "ice-hockey", "name": "Ice hockey"}
        manifest["club"]["sport"] = sport
        manifest["referee_types"]["42"] = {"sport": sport, "kind": "referee", "name": "Linesman", "ordering": 2}
        event_id, assignment_id = str(uuid4()), str(uuid4())
        sponsor_id = str(uuid4())
        now = manifest["exported_at"]
        image = BytesIO()
        Image.new("RGB", (4, 4), "blue").save(image, format="PNG")
        asset = "media/legacy-sponsor.png"
        manifest["records"]["club.sponsor"] = [{"fields": {
            "id": sponsor_id, "created": now, "modified": now, "name": "Legacy sponsor", "logo": asset,
            "url": "https://sponsor.example", "start_date": date(2026, 8, 1), "end_date": None, "logo_width": 4, "logo_height": 4,
        }, "many": {}}]
        manifest["records"]["events.event"] = [{"fields": {
            "id": event_id, "created": now, "modified": now, "start": now + timedelta(days=2),
            "title": "Legacy fixture", "kind": "game",
        }, "many": {}}]
        manifest["records"]["events.eventreferee"] = [{"fields": {
            "id": assignment_id, "created": now, "modified": now, "event_id": event_id, "referee_type_id": "42",
            "external_name": "Legacy referee",
        }, "many": {}}]
        media_root = self.enterContext(TemporaryDirectory())
        self.enterContext(self.settings(MEDIA_ROOT=media_root))
        restored, new_accounts, warnings = restore_club(archive_of(manifest, **{asset: image.getvalue()}))
        user = User.objects.get(email="legacy@backup.test")
        self.assertEqual((new_accounts, warnings, restored.owner_id), (1, [], user.pk))
        self.assertFalse(user.has_usable_password())
        restored.refresh_from_db()
        self.assertTrue(restored.shop_enabled)
        with schema_context(restored.schema_name):
            self.assertTrue(Team.objects.filter(pk=manifest["records"]["teams.team"][0]["fields"]["id"]).exists())
            self.assertTrue(Member.objects.filter(pk=manifest["records"]["members.member"][0]["fields"]["id"], user=user).exists())
            self.assertTrue(user.tenant_perms.groups.filter(role__slug="owner").exists())
            self.assertTrue(EventReferee.objects.filter(pk=assignment_id, event_id=event_id, referee_type__name="Linesman").exists())
            sponsor = Sponsor.objects.get(pk=sponsor_id)
            self.assertEqual((sponsor.name, sponsor.url), ("Legacy sponsor", "https://sponsor.example"))
            with sponsor.logo.open() as logo:
                self.assertEqual(logo.read(), image.getvalue())

    def test_corrupt_image_is_left_empty_with_a_warning_naming_the_record(self):
        manifest = legacy_manifest()
        now = manifest["exported_at"]
        sponsor_id = str(uuid4())
        asset = "media/broken-sponsor.png"
        manifest["records"]["club.sponsor"] = [{"fields": {
            "id": sponsor_id, "created": now, "modified": now, "name": "Broken sponsor", "logo": asset,
            "url": "https://sponsor.example", "start_date": date(2026, 8, 1), "end_date": None, "logo_width": 4, "logo_height": 4,
        }, "many": {}}]
        media_root = self.enterContext(TemporaryDirectory())
        self.enterContext(self.settings(MEDIA_ROOT=media_root))
        restored, _, warnings = restore_club(archive_of(manifest, **{asset: b"not an image"}))
        self.assertTrue(any("broken-sponsor.png" in w and "Broken sponsor" in w for w in warnings), warnings)
        with schema_context(restored.schema_name):
            self.assertFalse(Sponsor.objects.get(pk=sponsor_id).logo)

    def test_v2_round_trip_restores_relationships_media_and_club_roles(self):
        source = create_club("Source Club", self.owner)
        with TemporaryDirectory() as media_root, TemporaryDirectory() as private_root, self.settings(MEDIA_ROOT=media_root, PRIVATE_MEDIA_ROOT=private_root):
            image = BytesIO()
            Image.new("RGB", (4, 4), "blue").save(image, format="PNG")
            source.logo.save("crest.png", ContentFile(image.getvalue()))
            source.event_background.save("arena.png", ContentFile(image.getvalue()))
            with schema_context(source.schema_name):
                sponsor = Sponsor.objects.create(name="Club partner", url="https://partner.example", end_date=date(2027, 7, 31))
                sponsor.logo.save("partner.png", ContentFile(image.getvalue()))
                owner = Member.objects.get(user=self.owner)
                child = Member.objects.create(first_name="Club", last_name="Child")
                child.profile_photo.save("photo.png", ContentFile(image.getvalue()))
                family = Family.objects.create(name="Club family")
                FamilyMembership.objects.create(family=family, member=owner, role="parent")
                FamilyMembership.objects.create(family=family, member=child, role="child")
                season = Season.objects.create(start_date=date(2026, 8, 1), end_date=date(2027, 7, 31))
                membership = ClubMembership.objects.create(season=season, member=child, fee_amount="100.00", amount_paid="40.00")
                fee = FeePayment.objects.create(membership=membership, amount="40.00", recorded_by=self.owner)
                team = Team.objects.create(name="Juniors", short_name="U14")
                event = Event.objects.create(title="Game", start=timezone.now() + timedelta(days=2), season=season)
                event.teams.add(team)
                event.invited_members.add(child)
                answer = Attendance.objects.get(event=event, member=child)
                answer.status = Attendance.AttendanceStatus.PRESENT
                answer.save(update_fields=["status"])
                product = Product.objects.create(name="Club sweater", price="12.50")
                order = Order.objects.create(number="ORD-ORIGINAL", purchaser=owner, total="12.50")
                line = OrderLine.objects.create(order=order, product=product, unit_price="12.50", line_total="12.50")
                sequence = ShopNumberSequence.objects.create(kind="order", scope="all", last_number=7)

            with export_club(source) as backup:
                upload = SimpleUploadedFile("backup.zip", backup.read(), content_type="application/zip")
            headers = {"HTTP_HOST": "testserver", "HTTP_X_SESSION_TOKEN": login_session(self.owner)}
            with patch("controlpanel.api.auth.is_local_development", return_value=True):
                inspection = self.client.post("/api/platform/v1/club-backups/preview", {"archive": upload}, **headers)
                self.assertEqual(inspection.status_code, 200, inspection.content)
                details = inspection.json()
                self.assertEqual((details["version"], details["slug"], details["suggested_slug"]), (2, source.slug, f"{source.slug}-restored"))
                upload.seek(0)
                conflict = self.client.post("/api/platform/v1/club-backups/restore", {"archive": upload, "slug": source.slug}, **headers)
                self.assertEqual(conflict.status_code, 422, conflict.content)
                self.assertIn("slug", conflict.json()["error"]["fields"])
                upload.seek(0)
                result = self.client.post("/api/platform/v1/club-backups/restore", {"archive": upload, "slug": details["suggested_slug"]}, **headers)
                self.assertEqual(result.status_code, 201, result.content)
            data = result.json()
            self.assertEqual((data["new_accounts"], data["warnings"], data["slug"]), (0, [], details["suggested_slug"]))
            restored = Club.objects.get(pk=data["id"])
            self.assertEqual(restored.owner_id, self.owner.pk)
            self.assertEqual(restored.schema_name, schema_name_for(data["slug"]))
            self.assertEqual(restored.get_primary_domain().domain, club_domain(data["slug"]))
            self.assertTrue(User.objects.get(pk=self.owner.pk).has_usable_password())
            with restored.logo.open() as crest:
                self.assertEqual(crest.read(), image.getvalue())
            with restored.event_background.open() as background:
                self.assertEqual(background.read(), image.getvalue())
            with schema_context(restored.schema_name):
                restored_sponsor = Sponsor.objects.get(pk=sponsor.pk)
                self.assertEqual((restored_sponsor.name, restored_sponsor.url, restored_sponsor.start_date, restored_sponsor.end_date), (sponsor.name, sponsor.url, sponsor.start_date, sponsor.end_date))
                with restored_sponsor.logo.open() as logo:
                    self.assertEqual(logo.read(), image.getvalue())
                restored_child = Member.objects.get(pk=child.pk)
                self.assertTrue(FamilyMembership.objects.filter(family_id=family.pk, member=restored_child, role="child").exists())
                self.assertTrue(ClubMembership.objects.filter(pk=membership.pk, member=restored_child, season_id=season.pk).exists())
                self.assertTrue(Event.objects.get(pk=event.pk).teams.filter(pk=team.pk).exists())
                self.assertTrue(Attendance.objects.filter(pk=answer.pk, event_id=event.pk, member=restored_child, status=Attendance.AttendanceStatus.PRESENT).exists())
                self.assertEqual(Product.objects.get(pk=product.pk).name, product.name)
                self.assertTrue(OrderLine.objects.filter(pk=line.pk, order_id=order.pk, product_id=product.pk, line_total="12.50").exists())
                self.assertTrue(FeePayment.objects.filter(pk=fee.pk, membership_id=membership.pk, recorded_by=self.owner).exists())
                self.assertEqual(ShopNumberSequence.objects.get(pk=sequence.pk).last_number, 7)
                self.assertGreater(ShopNumberSequence.objects.create(kind="voucher", scope="all").pk, sequence.pk)
                with restored_child.profile_photo.open() as photo:
                    self.assertEqual(photo.read(), image.getvalue())
