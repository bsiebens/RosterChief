import json
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZipFile

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from club.models import Club, ClubMembership, ClubRole, FeePayment, RefereeType, Season, Sport
from events.models import Event, EventAttachment, EventReferee, RefereeSignup
from features.models import Flag
from formbuilder.models import Answer, Field, Form, FormSend, Submission
from members.models import Family, FamilyMembership, Member
from registration.models import RegistrationBatch, RegistrationDetails
from rosterchief.test_support import enrol_mfa
from shop.models import Invoice, Order, OrderLine, Payment, Product, ProductVariant
from teams.models import Position, StaffAssignment, Team

User = get_user_model()


def make_person(email, name):
    user = User.objects.create_user(email=email, password="synthetic-test-password")
    return Member.objects.create(user=user, first_name=name, last_name="Example", email=email)


def png(color):
    stream = BytesIO()
    Image.new("RGB", (4, 4), color).save(stream, format="PNG")
    return stream.getvalue()


def make_activity(club, shared, child, referee_type, *, color, file_bytes, actor):
    today = timezone.localdate()
    season = Season.objects.create(club=club, start_date=today - timedelta(days=10), end_date=today + timedelta(days=90))
    family = Family.objects.create(club=club, name=f"{club.name} family")
    FamilyMembership.objects.create(family=family, member=shared, role=FamilyMembership.FamilyRole.PARENT)
    FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
    membership = ClubMembership.objects.create(club=club, season=season, member=child, fee_amount="125.50", amount_paid="45.10")
    sources = [{"season_id": str(season.pk), "source": "RBIHF", "url": "https://fixtures.example.test/123", "extra": {"count": 3, "active": True, "value": None}}]
    team = Team.objects.create(club=club, name=f"{club.name} U14", short_name="U14", import_sources=sources)
    event = Event.objects.create(club=club, season=season, kind=Event.EventKind.GAME, title=f"{club.name} game", start=timezone.now())
    event.teams.add(team)
    EventReferee.objects.create(event=event, referee_type=referee_type, assigned_by=shared, external_name="Synthetic Referee")
    RefereeSignup.objects.create(event=event, member=child, status=RefereeSignup.Status.DECLINED)
    attachment = EventAttachment(event=event, name="game-note.txt", uploaded_by=shared, size=len(file_bytes))
    attachment.file.save("game-note.txt", ContentFile(file_bytes))
    club.logo.save("logo.png", ContentFile(png(color)))

    registration = RegistrationBatch.objects.create(
        club=club, season=season, contact_member=shared, submitted_by_user=shared.user,
        contact_first_name=shared.first_name, contact_last_name=shared.last_name, contact_email=shared.email,
        subtotal="125.50", total="125.50", invoice_number="REG-SAMPLE", invoice_sent_at=timezone.now(),
    )
    registration_product = Product.objects.create(club=club, season=season, name="Season registration", product_type=Product.ProductType.MEMBERSHIP)
    variant = ProductVariant.objects.create(product=registration_product, name="Player", price="125.50")
    RegistrationDetails.objects.create(batch=registration, membership=membership, product_variant=variant, requested_team=team, price="125.50")
    FeePayment.objects.create(membership=membership, recorded_by=actor, amount="45.10")

    product = Product.objects.create(club=club, name=f"{club.name} scarf", price="12.50")
    order = Order.objects.create(club=club, purchaser=shared, total="12.50")
    OrderLine.objects.create(order=order, product=product, unit_price="12.50", line_total="12.50")
    Payment.objects.create(order=order, amount="12.50", status=Payment.PaymentStatus.CONFIRMED)
    Invoice.objects.create(club=club, order=order)
    return team, event, attachment, order


def make_fixture():
    selected = Club.objects.create(name="Sample Ice Club", slug="sample-ice-club", sport_type=Club.SportType.ICE_HOCKEY, shop_open=True)
    other = Club.objects.create(name="Other Club Confidential", sport_type=Club.SportType.ICE_HOCKEY)
    owner = make_person("owner@example.test", "Owner")
    shared = make_person("shared@example.test", "Shared Guardian")
    coach = make_person("coach@example.test", "Coach")
    player = make_person("player@example.test", "Sample Player")
    other_player = make_person("other-player@example.test", "Other Player Confidential")
    former = make_person("former@example.test", "Former Treasurer")
    ClubRole.objects.create(club=selected, member=owner, role=ClubRole.Roles.ADMIN)
    ClubRole.objects.create(club=other, member=shared, role=ClubRole.Roles.ADMIN)
    ClubRole.objects.create(club=other, member=former, role=ClubRole.Roles.MEMBER)
    sport = Sport.objects.get(code=Club.SportType.ICE_HOCKEY)
    selected_type, _ = RefereeType.objects.get_or_create(sport=sport, kind=RefereeType.Kind.REFEREE, name="Sample Linesman", defaults={"ordering": 1})
    other_type, _ = RefereeType.objects.get_or_create(sport=sport, kind=RefereeType.Kind.REFEREE, name="Other Referee Confidential")
    selected_activity = make_activity(selected, shared, player, selected_type, color="blue", file_bytes=b"Selected club private attachment", actor=former.user)
    other_activity = make_activity(other, shared, other_player, other_type, color="red", file_bytes=b"Other club private confidential bytes", actor=other_player.user)
    player.profile_photo.save("profile.png", ContentFile(png("green")))
    other_player.profile_photo.save("profile.png", ContentFile(png("orange")))
    position = Position.objects.create(club=selected, name="Coach", short_name="C", staff_position=True)
    StaffAssignment.objects.create(team=selected_activity[0], member=coach, season=selected_activity[1].season, position=position)

    form = Form.objects.create(club=selected, title="Sample consent")
    field = Field.objects.create(form=form, label="Document", field_type=Field.FieldType.FILE)
    send = FormSend.objects.create(club=selected, form=form, season=selected_activity[1].season)
    submission = Submission.objects.create(send=send, member=player, submitted_by=shared)
    answer = Answer(submission=submission, field=field, value={"name": "consent.txt", "size": 15, "content_type": "text/plain"})
    answer.file.save("consent.txt", ContentFile(b"Sample consent."))
    for name in ("shop", "officials", "RBIHF"):
        flag, _ = Flag.objects.get_or_create(name=name)
        flag.clubs.add(selected)
    return selected, other, owner, shared, selected_type, selected_activity, other_activity


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ClubBackupTests(TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(self.settings(MEDIA_ROOT=self.enterContext(TemporaryDirectory()), PRIVATE_MEDIA_ROOT=self.enterContext(TemporaryDirectory())))
        self.club, self.other, self.owner, self.shared, self.referee_type, self.activity, self.other_activity = make_fixture()
        self.root = User.objects.create_superuser(email="operator@example.test", password="operator-password")
        enrol_mfa(self.root)
        self.client.force_login(self.root)
        self.url = reverse("controlpanel:club_backup", args=[self.club.pk])

    def download(self, owner=None):
        return self.client.post(self.url, {"owner": str((owner or self.owner).user_id)})

    def test_download_keeps_only_the_selected_clubs_graph_and_bytes(self):
        self.assertContains(self.client.get(reverse("controlpanel:club_detail", args=[self.club.pk])), "Download club backup")
        response = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("sample-ice-club-rosterchief-v1.zip", response.headers["Content-Disposition"])
        audit = LogEntry.objects.get(object_id=str(self.club.pk))
        self.assertEqual(audit.user_id, self.root.pk)
        self.assertIn("Downloaded club backup", audit.change_message)
        with ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual((manifest["format"], manifest["version"]), ("rosterchief-club-backup", 1))
            self.assertEqual(manifest["club"]["owner"], str(self.owner.user_id))
            self.assertTrue(manifest["club"]["registration_open"])
            self.assertEqual(manifest["club"]["sport"], {"slug": "ice-hockey", "name": "Ice hockey"})
            self.assertEqual(manifest["features"]["competition_rbihf"], {"enabled": True, "limit": None})
            self.assertEqual(manifest["features"]["competition_dfel"], {"enabled": False, "limit": None})
            self.assertEqual(set(manifest["referee_types"]), {str(self.referee_type.pk)})
            accounts = {row["email"]: row for row in manifest["accounts"]}
            self.assertEqual(set(accounts), {"shared@example.test", "owner@example.test", "coach@example.test", "former@example.test", "player@example.test"})
            self.assertTrue(accounts["shared@example.test"]["enrolled"])
            self.assertTrue(accounts["coach@example.test"]["enrolled"])
            self.assertFalse(accounts["former@example.test"]["enrolled"])
            for account in accounts.values():
                self.assertEqual(set(account), {"id", "email", "first_name", "last_name", "active", "enrolled"})

            team, event, _, order = self.activity
            records = manifest["records"]
            self.assertEqual([row["fields"]["id"] for row in records["teams.team"]], [str(team.pk)])
            self.assertEqual(records["teams.team"][0]["fields"]["import_sources"], team.import_sources)
            self.assertEqual(records["events.event"][0]["fields"]["id"], str(event.pk))
            self.assertEqual(records["events.event"][0]["many"], {"teams": [str(team.pk)]})
            self.assertEqual([row["fields"]["member_id"] for row in records["club.clubrole"]], [str(self.owner.pk)])
            self.assertEqual(len(records["members.family"]), 1)
            self.assertEqual(len(records["members.familymembership"]), 2)
            self.assertEqual(records["club.feepayment"][0]["fields"]["amount"], "45.10")
            self.assertEqual(records["registration.registrationdetails"][0]["fields"]["price"], "125.50")
            self.assertEqual(records["shop.orderline"][0]["fields"]["order_id"], str(order.pk))
            self.assertEqual(records["events.refereesignup"][0]["fields"]["status"], "declined")
            files = {name: archive.read(name) for name in archive.namelist() if name.startswith("media/")}
            player = next(row["fields"] for row in records["members.member"] if row["fields"]["email"] == "player@example.test")
            self.assertEqual(files, {
                manifest["club"]["fields"]["logo"]: png("blue"),
                records["events.eventattachment"][0]["fields"]["file"]: b"Selected club private attachment",
                records["formbuilder.answer"][0]["fields"]["file"]: b"Sample consent.",
                player["profile_photo"]: png("green"),
            })
            self.assertTrue(records["events.eventattachment"][0]["fields"]["file"].endswith("/game-note.txt"))
            serialized = archive.read("manifest.json")
            self.assertNotIn(b"Confidential", serialized)
            self.assertNotIn(b"club_id", serialized)
            for obj in self.other_activity:
                self.assertNotIn(str(obj.pk).encode(), serialized)

    def test_staff_cannot_see_or_use_the_action(self):
        self.root.is_superuser = False
        self.root.save(update_fields=["is_superuser"])
        self.assertNotContains(self.client.get(reverse("controlpanel:club_detail", args=[self.club.pk])), "Download club backup")
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.download().status_code, 403)
        self.assertFalse(LogEntry.objects.exists())

    def test_owner_selection_is_limited_to_eligible_logins(self):
        ClubRole.objects.create(club=self.club, member=self.shared, role=ClubRole.Roles.ADMIN)
        page = self.client.get(self.url)
        self.assertContains(page, self.owner.email)
        self.assertContains(page, self.shared.email)
        self.assertNotContains(page, "former@example.test")
        self.assertEqual(self.client.post(self.url, {"owner": str(uuid4())}).status_code, 422)
        self.assertEqual(self.client.post(self.url).status_code, 422)
        ClubRole.objects.filter(club=self.club).delete()
        self.assertContains(self.client.get(self.url), "This club has no active admin.")
        response = self.download(self.shared)
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["club"]["owner"], str(self.shared.user_id))
            self.assertTrue(next(row for row in manifest["accounts"] if row["id"] == str(self.shared.user_id))["enrolled"])

    def test_missing_upload_fails_before_download(self):
        attachment = self.activity[2]
        attachment.file.storage.delete(attachment.file.name)
        self.assertContains(self.download(), "Could not open", status_code=422)
        self.assertFalse(LogEntry.objects.exists())

    def test_registration_requires_an_active_variant(self):
        ProductVariant.objects.filter(product__club=self.club).update(is_active=False)
        response = self.download()
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertFalse(manifest["club"]["registration_open"])

    def test_cross_club_relationship_fails_before_download(self):
        event, other_team = self.activity[1], self.other_activity[0]
        event.teams.through.objects.create(event=event, team=other_team)
        self.assertContains(self.download(), "missing or out-of-club teams.team", status_code=422)
        self.assertFalse(LogEntry.objects.exists())
