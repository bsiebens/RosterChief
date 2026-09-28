import tempfile
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.db.models import SET_NULL
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import User
from club.models import Club, ClubMembership, ClubRole, Season
from members.admin import FamilyAdmin
from members.models import Family, FamilyInvite, FamilyMembership, Group, GroupMembership, Member
from members.services import MemberImportResult
from members.services.deletion import member_has_ties_outside_club, member_is_deletable, remove_member_from_club
from members.services.family import claim_label_for, family_contacts
from teams.models import Position, StaffAssignment, Team, TeamMembership


class MemberModelTests(TestCase):
    def test_str_and_name_helpers(self):
        member = Member.objects.create(first_name="John", last_name="Smith")

        self.assertEqual(str(member), "John Smith")
        self.assertEqual(member.get_full_name(), "John Smith")
        self.assertEqual(member.get_short_name(), "John")

    def test_get_full_name_strips_when_partial(self):
        member = Member.objects.create(first_name="Cher", last_name="")
        self.assertEqual(member.get_full_name(), "Cher")

    def test_contact_email_prefers_own_email(self):
        user = User.objects.create_user(email="login@example.com", password="x")
        member = Member.objects.create(user=user, first_name="Own", last_name="Email", email="own@example.com")
        self.assertEqual(member.contact_email, "own@example.com")

    def test_contact_email_falls_back_to_login_email(self):
        user = User.objects.create_user(email="login@example.com", password="x")
        member = Member.objects.create(user=user, first_name="No", last_name="Email")
        self.assertEqual(member.contact_email, "login@example.com")

    def test_contact_email_empty_without_email_or_user(self):
        member = Member.objects.create(first_name="Zero", last_name="Contact")
        self.assertEqual(member.contact_email, "")

    def test_member_can_exist_without_user(self):
        member = Member.objects.create(first_name="No", last_name="Login")
        self.assertIsNone(member.user)

    def test_deleting_user_nulls_member_but_keeps_it(self):
        user = User.objects.create_user(email="temp@example.com", password="x")
        member = Member.objects.create(user=user, first_name="Keep", last_name="Me")

        # OneToOneField uses on_delete=SET_NULL.
        field = Member._meta.get_field("user")
        self.assertIs(field.remote_field.on_delete, SET_NULL)

        user.delete()
        member.refresh_from_db()
        self.assertIsNone(member.user)
        self.assertTrue(Member.objects.filter(pk=member.pk).exists())

    def test_user_member_is_one_to_one(self):
        user = User.objects.create_user(email="once@example.com", password="x")
        Member.objects.create(user=user, first_name="First", last_name="Member")

        with self.assertRaises(IntegrityError):
            Member.objects.create(user=user, first_name="Second", last_name="Member")

    def test_public_photo_is_none_without_a_photo(self):
        member = Member.objects.create(first_name="No", last_name="Photo", photo_public_consent=True)
        self.assertIsNone(member.public_photo)

    def test_public_photo_is_none_without_consent(self):
        member = Member.objects.create(first_name="Not", last_name="Consenting", photo=SimpleUploadedFile("photo.jpg", b"fake-image-bytes", content_type="image/jpeg"))
        self.assertIsNone(member.public_photo)

    def test_public_photo_is_the_photo_once_consent_is_given(self):
        member = Member.objects.create(first_name="Consents", last_name="Fully", photo=SimpleUploadedFile("photo.jpg", b"fake-image-bytes", content_type="image/jpeg"), photo_public_consent=True)
        self.assertEqual(member.public_photo, member.photo)


class FamilyNameOptionalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_family_can_be_created_without_a_name(self):
        family = Family.objects.create(club=self.club)
        self.assertEqual(family.name, "")

    def test_str_uses_name_when_present(self):
        self.assertEqual(str(Family.objects.create(club=self.club, name="The Smiths")), "The Smiths")

    def test_str_falls_back_to_member_surnames(self):
        family = Family.objects.create(club=self.club)
        smith = Member.objects.create(first_name="Pat", last_name="Smith")
        jones = Member.objects.create(first_name="Kim", last_name="Jones")
        FamilyMembership.objects.create(family=family, member=smith, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=jones, role=FamilyMembership.FamilyRole.CHILD)

        # Distinct surnames, alphabetically ordered.
        self.assertEqual(str(family), "Jones / Smith")

    def test_str_falls_back_to_short_id_when_empty(self):
        family = Family.objects.create(club=self.club)
        self.assertEqual(str(family), f"Family {str(family.pk)[:8]}")


class FamilyModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # One family with a member in every role -- read-only for all four tests.
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.family = Family.objects.create(club=cls.club, name="The Smiths")
        cls.parent = Member.objects.create(first_name="Pat", last_name="Smith")
        cls.guardian = Member.objects.create(first_name="Gale", last_name="Smith")
        cls.child = Member.objects.create(first_name="Kim", last_name="Smith")
        cls.other = Member.objects.create(first_name="Ola", last_name="Smith")

        FamilyMembership.objects.create(family=cls.family, member=cls.parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=cls.family, member=cls.guardian, role=FamilyMembership.FamilyRole.GUARDIAN)
        FamilyMembership.objects.create(family=cls.family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=cls.family, member=cls.other, role=FamilyMembership.FamilyRole.OTHER)

    def test_str(self):
        self.assertEqual(str(self.family), "The Smiths")

    def test_guardians_include_parents_and_guardians_only(self):
        guardians = set(self.family.guardians)
        self.assertEqual(guardians, {self.parent, self.guardian})

    def test_children_include_children_only(self):
        children = list(self.family.children)
        self.assertEqual(children, [self.child])

    def test_guardians_are_scoped_to_the_family(self):
        other_family = Family.objects.create(club=self.club, name="The Joneses")
        outsider = Member.objects.create(first_name="Out", last_name="Sider")
        FamilyMembership.objects.create(family=other_family, member=outsider, role=FamilyMembership.FamilyRole.PARENT)

        self.assertNotIn(outsider, set(self.family.guardians))


class MemberGuardiansTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_guardians_of_a_child_are_family_parents_and_guardians(self):
        family = Family.objects.create(club=self.club, name="The Does")
        mum = Member.objects.create(first_name="Mary", last_name="Doe")
        legal = Member.objects.create(first_name="Lee", last_name="Doe")
        kid = Member.objects.create(first_name="Kit", last_name="Doe")

        FamilyMembership.objects.create(family=family, member=mum, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=legal, role=FamilyMembership.FamilyRole.GUARDIAN)
        FamilyMembership.objects.create(family=family, member=kid, role=FamilyMembership.FamilyRole.CHILD)

        self.assertEqual(set(kid.guardians(self.club)), {mum, legal})

    def test_guardians_empty_for_a_parent(self):
        family = Family.objects.create(club=self.club, name="The Roes")
        parent = Member.objects.create(first_name="Ray", last_name="Roe")
        kid = Member.objects.create(first_name="Ren", last_name="Roe")

        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=kid, role=FamilyMembership.FamilyRole.CHILD)

        self.assertEqual(list(parent.guardians(self.club)), [])

    def test_guardians_do_not_leak_across_families(self):
        family_a = Family.objects.create(club=self.club, name="Family A")
        family_b = Family.objects.create(club=self.club, name="Family B")
        parent_a = Member.objects.create(first_name="Ann", last_name="A")
        parent_b = Member.objects.create(first_name="Ben", last_name="B")
        kid = Member.objects.create(first_name="Cody", last_name="A")

        FamilyMembership.objects.create(family=family_a, member=parent_a, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family_a, member=kid, role=FamilyMembership.FamilyRole.CHILD)
        # parent_b belongs to a different family and must not appear as kid's guardian.
        FamilyMembership.objects.create(family=family_b, member=parent_b, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(set(kid.guardians(self.club)), {parent_a})


class MemberFamilyMembersTests(TestCase):
    """Member.family_members -- every role, both directions, unlike the
    one-directional child->guardians shape of Member.guardians."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_includes_parents_children_and_guardians_but_not_self(self):
        family = Family.objects.create(club=self.club, name="The Does")
        parent = Member.objects.create(first_name="Mary", last_name="Doe")
        guardian = Member.objects.create(first_name="Lee", last_name="Doe")
        kid = Member.objects.create(first_name="Kit", last_name="Doe")
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=guardian, role=FamilyMembership.FamilyRole.GUARDIAN)
        FamilyMembership.objects.create(family=family, member=kid, role=FamilyMembership.FamilyRole.CHILD)

        self.assertEqual(set(parent.family_members(self.club)), {guardian, kid})
        self.assertEqual(set(kid.family_members(self.club)), {parent, guardian})

    def test_a_lone_member_has_no_family_members(self):
        lone = Member.objects.create(first_name="Lonnie", last_name="Loner")

        self.assertEqual(list(lone.family_members(self.club)), [])

    def test_does_not_leak_across_families(self):
        family_a = Family.objects.create(club=self.club, name="Family A")
        family_b = Family.objects.create(club=self.club, name="Family B")
        member_a = Member.objects.create(first_name="Ann", last_name="A")
        member_b = Member.objects.create(first_name="Ben", last_name="B")
        FamilyMembership.objects.create(family=family_a, member=member_a, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family_b, member=member_b, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(list(member_a.family_members(self.club)), [])


class FamilyMembershipModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_default_role_is_parent(self):
        family = Family.objects.create(club=self.club, name="Fam")
        member = Member.objects.create(first_name="D", last_name="Efault")
        membership = FamilyMembership.objects.create(family=family, member=member)

        self.assertEqual(membership.role, FamilyMembership.FamilyRole.PARENT)

    def test_str_includes_family_member_and_role(self):
        family = Family.objects.create(club=self.club, name="The Smiths")
        member = Member.objects.create(first_name="Pat", last_name="Smith")
        membership = FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.GUARDIAN)

        self.assertEqual(str(membership), "The Smiths - Pat Smith (guardian)")

    def test_member_unique_per_family(self):
        family = Family.objects.create(club=self.club, name="Fam")
        member = Member.objects.create(first_name="Solo", last_name="Once")
        FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.PARENT)

        with self.assertRaises(IntegrityError):
            FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.CHILD)

    def test_same_member_can_join_multiple_families(self):
        member = Member.objects.create(first_name="Multi", last_name="Fam")
        family_a = Family.objects.create(club=self.club, name="A")
        family_b = Family.objects.create(club=self.club, name="B")

        FamilyMembership.objects.create(family=family_a, member=member, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family_b, member=member, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(member.family_memberships.count(), 2)

    def test_deleting_family_cascades_to_memberships(self):
        family = Family.objects.create(club=self.club, name="Doomed")
        member = Member.objects.create(first_name="Cas", last_name="Cade")
        FamilyMembership.objects.create(family=family, member=member)

        family.delete()

        self.assertFalse(FamilyMembership.objects.exists())
        # The member itself survives; only the membership is removed.
        self.assertTrue(Member.objects.filter(pk=member.pk).exists())


class GroupModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_str_returns_name(self):
        group = Group.objects.create(club=self.club, name="Coaches")
        self.assertEqual(str(group), "Coaches")

    def test_name_is_unique_per_club(self):
        Group.objects.create(club=self.club, name="Coaches")

        with self.assertRaises(IntegrityError):
            Group.objects.create(club=self.club, name="Coaches")

    def test_same_name_allowed_in_another_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        Group.objects.create(club=self.club, name="Coaches")

        Group.objects.create(club=other, name="Coaches")

        self.assertEqual(Group.objects.filter(name="Coaches").count(), 2)


class GroupMembershipModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.group = Group.objects.create(club=cls.club, name="Referees")
        cls.member = Member.objects.create(first_name="Ref", last_name="Eree")

    def test_str(self):
        membership = GroupMembership.objects.create(group=self.group, member=self.member)
        self.assertEqual(str(membership), "Referees - Ref Eree")

    def test_member_unique_per_group(self):
        GroupMembership.objects.create(group=self.group, member=self.member)

        with self.assertRaises(IntegrityError):
            GroupMembership.objects.create(group=self.group, member=self.member)

    def test_same_member_can_join_multiple_groups(self):
        other_group = Group.objects.create(club=self.club, name="Coaches")
        GroupMembership.objects.create(group=self.group, member=self.member)

        GroupMembership.objects.create(group=other_group, member=self.member)

        self.assertEqual(self.member.group_memberships.count(), 2)

    def test_deleting_group_cascades_to_memberships(self):
        GroupMembership.objects.create(group=self.group, member=self.member)

        self.group.delete()

        self.assertFalse(GroupMembership.objects.exists())
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())


class FamilyAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_member_count_reflects_memberships(self):
        family = Family.objects.create(club=self.club, name="The Smiths")
        for i in range(3):
            member = Member.objects.create(first_name=f"Kid{i}", last_name="Smith")
            FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.CHILD)

        admin_instance = FamilyAdmin(Family, AdminSite())
        self.assertEqual(admin_instance.member_count(family), 3)

    def test_member_count_is_zero_without_members(self):
        family = Family.objects.create(club=self.club, name="Empty")
        admin_instance = FamilyAdmin(Family, AdminSite())
        self.assertEqual(admin_instance.member_count(family), 0)


class AdminSmokeTests(TestCase):
    """Exercise the admin config end-to-end to catch misregistration
    (bad search_fields, autocomplete targets, fieldsets, custom forms)."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(email="root@example.com", password="pw-secret-123")
        # Staff must hold a second factor (RequireMFAMiddleware).
        Authenticator.objects.create(user=cls.admin, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})

    def setUp(self):
        # The test client is per-test, so the sign-in itself cannot be hoisted.
        self.client.force_login(self.admin)

    def test_changelists_load(self):
        for app_label, model in (
            ("authentication", "user"),
            ("members", "member"),
            ("members", "family"),
            ("members", "familymembership"),
            ("members", "group"),
            ("members", "groupmembership"),
        ):
            with self.subTest(model=model):
                response = self.client.get(f"/admin/{app_label}/{model}/")
                self.assertEqual(response.status_code, 200)

    def test_user_add_page_loads(self):
        response = self.client.get("/admin/authentication/user/add/")
        self.assertEqual(response.status_code, 200)

    def test_member_changelist_shows_grouped_numbers_and_fallback_email(self):
        user = User.objects.create_user(email="fallback@example.com", password="pw")
        Member.objects.create(
            user=user,
            first_name="Grouped",
            last_name="Numbers",
            phone="+32470123456",
            emergency_phone="+3221234567",
        )
        response = self.client.get("/admin/members/member/")
        content = response.content.decode()

        # Numbers rendered in grouped international format, not raw E.164.
        self.assertIn("+32 470 12 34 56", content)
        self.assertIn("+32 2 123 45 67", content)
        # Email column falls back to the linked login email.
        self.assertIn("fallback@example.com", content)

    def test_create_user_through_admin_hashes_password(self):
        response = self.client.post(
            "/admin/authentication/user/add/",
            {
                "email": "new@example.com",
                "password1": "a-good-password-42",
                "password2": "a-good-password-42",
                # Empty MemberInline management form — no profile created.
                "member-TOTAL_FORMS": "0",
                "member-INITIAL_FORMS": "0",
                "member-MIN_NUM_FORMS": "0",
                "member-MAX_NUM_FORMS": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        created = User.objects.get(email="new@example.com")
        self.assertTrue(created.check_password("a-good-password-42"))

    def test_autocomplete_endpoints_respond(self):
        # Member.user autocomplete resolves against UserAdmin.search_fields.
        response = self.client.get(
            "/admin/autocomplete/",
            {"app_label": "members", "model_name": "member", "field_name": "user", "term": "root"},
        )
        self.assertEqual(response.status_code, 200)


class ImportMembersCsvCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Memberships are season-scoped: the importer attaches each one to the
        # club's current season, so the target club needs one covering today.
        cls.club = Club.objects.create(name="City Swim Club")
        today = timezone.localdate()
        cls.season = Season.objects.create(
            club=cls.club,
            start_date=today - timedelta(days=90),
            end_date=today + timedelta(days=275),
        )

    def write_csv(self, content):
        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
        temp_file.write(content)
        temp_file.close()
        self.addCleanup(lambda: Path(temp_file.name).unlink(missing_ok=True))
        return temp_file.name

    def call_import_command(self, csv_path, **options):
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "import_members_csv",
            csv_path,
            stdout=stdout,
            stderr=stderr,
            **options,
        )

        return stdout.getvalue(), stderr.getvalue()

    def test_import_creates_member_club_membership_and_user_when_requested(self):
        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "Jane,Doe,jane@example.com,2010-04-12,true,City Swim Club,LIC-001",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path)

        self.assertEqual(stderr, "")
        self.assertIn("Import complete.", stdout)
        self.assertIn("Members created: 1.", stdout)
        self.assertIn("Users created: 1.", stdout)
        self.assertIn("Memberships created: 1.", stdout)
        self.assertIn("Rows skipped: 0.", stdout)

        member = Member.objects.get(email="jane@example.com")
        self.assertEqual(member.first_name, "Jane")
        self.assertEqual(member.last_name, "Doe")
        self.assertEqual(member.date_of_birth, date(2010, 4, 12))
        self.assertIsNotNone(member.user)
        self.assertEqual(member.user.email, "jane@example.com")
        self.assertFalse(member.user.has_usable_password())

        membership = ClubMembership.objects.get(club=self.club, member=member)
        self.assertEqual(membership.license, "LIC-001")
        self.assertEqual(membership.season, self.season)

    def test_import_creates_member_without_user_when_create_account_is_false(self):
        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "John,Smith,john@example.com,2009-11-03,false,City Swim Club,LIC-002",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path)

        self.assertEqual(stderr, "")
        self.assertIn("Members created: 1.", stdout)
        self.assertIn("Users created: 0.", stdout)

        member = Member.objects.get(email="john@example.com")
        self.assertIsNone(member.user)
        self.assertFalse(User.objects.filter(email="john@example.com").exists())

    def test_import_updates_existing_member_and_membership(self):
        member = Member.objects.create(
            first_name="Old",
            last_name="Name",
            email="jane@example.com",
            date_of_birth=date(2010, 1, 1),
        )
        ClubMembership.objects.create(
            club=self.club,
            season=self.season,
            member=member,
            license="OLD-LIC",
        )

        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "Jane,Doe,jane@example.com,2010-04-12,false,City Swim Club,LIC-NEW",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path)

        self.assertEqual(stderr, "")
        self.assertIn("Members created: 0.", stdout)
        self.assertIn("Members updated: 1.", stdout)
        self.assertIn("Memberships created: 0.", stdout)
        self.assertIn("Memberships updated: 1.", stdout)

        member.refresh_from_db()
        self.assertEqual(member.first_name, "Jane")
        self.assertEqual(member.last_name, "Doe")
        self.assertEqual(member.date_of_birth, date(2010, 4, 12))

        membership = ClubMembership.objects.get(club=self.club, member=member)
        self.assertEqual(membership.license, "LIC-NEW")

    def test_import_links_existing_user_when_create_account_is_true(self):
        user = User.objects.create_user(email="jane@example.com", password="secret123")

        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "Jane,Doe,jane@example.com,2010-04-12,true,City Swim Club,LIC-001",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path)

        self.assertEqual(stderr, "")
        self.assertIn("Users created: 0.", stdout)

        member = Member.objects.get(email="jane@example.com")
        self.assertEqual(member.user, user)
        self.assertTrue(user.check_password("secret123"))

    def test_import_supports_custom_date_format(self):
        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "Jane,Doe,jane@example.com,12/04/2010,false,City Swim Club,LIC-001",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path, date_format="%d/%m/%Y")

        self.assertEqual(stderr, "")
        self.assertIn("Members created: 1.", stdout)

        member = Member.objects.get(email="jane@example.com")
        self.assertEqual(member.date_of_birth, date(2010, 4, 12))

    def test_import_skips_invalid_row_and_imports_valid_rows(self):
        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "Jane,Doe,jane@example.com,2010-04-12,false,City Swim Club,LIC-001",
                    "Broken,Date,broken@example.com,not-a-date,false,City Swim Club,LIC-002",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path)

        self.assertIn("Row 3 skipped:", stderr)
        self.assertIn("Invalid date_of_birth 'not-a-date'.", stderr)
        self.assertIn("Members created: 1.", stdout)
        self.assertIn("Rows skipped: 1.", stdout)

        self.assertTrue(Member.objects.filter(email="jane@example.com").exists())
        self.assertFalse(Member.objects.filter(email="broken@example.com").exists())

    def test_import_fails_for_missing_file(self):
        stdout = StringIO()
        stderr = StringIO()

        with self.assertRaises(CommandError) as context:
            call_command(
                "import_members_csv",
                "does-not-exist.csv",
                stdout=stdout,
                stderr=stderr,
            )

        self.assertIn("CSV file does not exist", str(context.exception))

    def test_import_fails_for_missing_required_columns(self):
        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email",
                    "Jane,Doe,jane@example.com",
                ]
            )
        )

        stdout = StringIO()
        stderr = StringIO()

        with self.assertRaises(CommandError) as context:
            call_command(
                "import_members_csv",
                csv_path,
                stdout=stdout,
                stderr=stderr,
            )

        self.assertIn("CSV file is missing required columns:", str(context.exception))
        self.assertIn("club_name", str(context.exception))
        self.assertIn("date_of_birth", str(context.exception))
        self.assertIn("license_number", str(context.exception))

    def test_import_fails_for_empty_csv_file(self):
        csv_path = self.write_csv("")

        stdout = StringIO()
        stderr = StringIO()

        with self.assertRaises(CommandError) as context:
            call_command(
                "import_members_csv",
                csv_path,
                stdout=stdout,
                stderr=stderr,
            )

        self.assertIn("CSV file is empty or missing a header row.", str(context.exception))

    def test_import_skips_row_with_empty_required_field(self):
        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "Jane,Doe,jane@example.com,2010-04-12,false,City Swim Club,LIC-001",
                    ",Missing,nofirst@example.com,2011-05-13,false,City Swim Club,LIC-002",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path)

        self.assertIn("Row 3 skipped:", stderr)
        self.assertIn("first_name is required.", stderr)
        self.assertIn("Members created: 1.", stdout)
        self.assertIn("Rows skipped: 1.", stdout)
        self.assertFalse(Member.objects.filter(email="nofirst@example.com").exists())

    def test_import_skips_row_when_club_has_no_current_season(self):
        # The club exists but has no season, so the row is skipped and — because
        # the row is atomic — the member creation rolls back too.
        Club.objects.create(name="New Club")
        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "Jane,Doe,jane@example.com,2010-04-12,false,New Club,LIC-001",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path)

        self.assertIn("Row 2 skipped:", stderr)
        self.assertIn("No current season for club 'New Club'.", stderr)
        self.assertIn("Rows skipped: 1.", stdout)
        self.assertFalse(Member.objects.filter(email="jane@example.com").exists())

    def test_import_skips_row_for_an_unknown_club(self):
        # The importer must not silently spin up a club for a typo'd or unknown
        # name — that is a data problem, not something to paper over.
        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "Jane,Doe,jane@example.com,2010-04-12,false,Nonexistent Club,LIC-001",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path)

        self.assertIn("Row 2 skipped:", stderr)
        self.assertIn("Unknown club 'Nonexistent Club'.", stderr)
        self.assertIn("Rows skipped: 1.", stdout)
        self.assertFalse(Member.objects.filter(email="jane@example.com").exists())
        self.assertFalse(Club.objects.filter(name="Nonexistent Club").exists())

    def test_import_matches_a_club_name_case_insensitively(self):
        # A CSV export's casing rarely matches the platform's own; that is
        # harmless variation, not a different club.
        csv_path = self.write_csv(
            "\n".join(
                [
                    "first_name,last_name,email,date_of_birth,create_account,club_name,license_number",
                    "Jane,Doe,jane@example.com,2010-04-12,false,city swim club,LIC-001",
                ]
            )
        )

        stdout, stderr = self.call_import_command(csv_path)

        self.assertEqual(stderr, "")
        self.assertIn("Rows skipped: 0.", stdout)
        membership = ClubMembership.objects.get(club=self.club, member__email="jane@example.com")
        self.assertEqual(membership.season, self.season)


class MemberImportResultTests(TestCase):
    def test_successful_rows_sums_created_and_updated(self):
        result = MemberImportResult(created_members=2, updated_members=3)
        self.assertEqual(result.successful_rows, 5)


class FamilyContactsTests(TestCase):
    """members.services.family.family_contacts -- every parent/guardian on
    record for a member's own family, used both by the registration invoice
    email (who it actually reaches) and Dues & billing's own Email column
    (what it shows)."""

    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.family = Family.objects.create(club=self.club)
        self.child = Member.objects.create(first_name="Cam", last_name="Childless")
        FamilyMembership.objects.create(family=self.family, member=self.child, role=FamilyMembership.FamilyRole.CHILD)

    def make_contact(self, first_name, role, email="contact@example.com"):
        member = Member.objects.create(first_name=first_name, last_name="Contact", email=email)
        FamilyMembership.objects.create(family=self.family, member=member, role=role)
        return member

    def test_a_parent_is_included_and_labelled_parent(self):
        parent = self.make_contact("Pia", FamilyMembership.FamilyRole.PARENT, email="pia@example.com")

        contacts = family_contacts(self.child, self.club)

        self.assertEqual(contacts, [{"member": parent, "email": "pia@example.com", "role_label": "parent"}])

    def test_a_guardian_is_included_and_labelled_guardian(self):
        guardian = self.make_contact("Gia", FamilyMembership.FamilyRole.GUARDIAN, email="gia@example.com")

        contacts = family_contacts(self.child, self.club)

        self.assertEqual(contacts, [{"member": guardian, "email": "gia@example.com", "role_label": "guardian"}])

    def test_another_child_in_the_family_is_excluded(self):
        self.make_contact("Sib", FamilyMembership.FamilyRole.CHILD, email="sib@example.com")

        self.assertEqual(family_contacts(self.child, self.club), [])

    def test_a_contact_with_no_email_is_excluded(self):
        Member.objects.create(first_name="Noe", last_name="Mail")
        FamilyMembership.objects.create(family=self.family, member=Member.objects.get(first_name="Noe"), role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(family_contacts(self.child, self.club), [])

    def test_both_parents_are_included(self):
        self.make_contact("Bernard", FamilyMembership.FamilyRole.PARENT, email="bernard@example.com")
        self.make_contact("Charlotte", FamilyMembership.FamilyRole.PARENT, email="charlotte@example.com")

        emails = {contact["email"] for contact in family_contacts(self.child, self.club)}

        self.assertEqual(emails, {"bernard@example.com", "charlotte@example.com"})

    def test_duplicate_emails_across_contacts_are_not_repeated(self):
        self.make_contact("Pia", FamilyMembership.FamilyRole.PARENT, email="shared@example.com")
        self.make_contact("Gia", FamilyMembership.FamilyRole.GUARDIAN, email="SHARED@example.com")

        self.assertEqual(len(family_contacts(self.child, self.club)), 1)

    def test_a_member_asking_about_their_own_family_is_not_their_own_contact(self):
        parent = self.make_contact("Pia", FamilyMembership.FamilyRole.PARENT, email="pia@example.com")

        contacts = family_contacts(parent, self.club)

        self.assertEqual(contacts, [])
        self.assertEqual(FamilyMembership.objects.get(member=parent).family, self.family)


class ClaimLabelForTests(TestCase):
    """members.services.family.claim_label_for -- how a member reads once
    they've claimed an EventTask slot: "<Family> family" when they belong to
    one, their own name otherwise. Doesn't broadcast which specific parent
    or child in a family is covering it."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_a_standalone_member_shows_their_own_name(self):
        member = Member.objects.create(first_name="Sam", last_name="Solo")

        self.assertEqual(claim_label_for(member, self.club), "Sam Solo")

    def test_a_member_in_a_named_family_shows_the_family_name(self):
        family = Family.objects.create(club=self.club, name="Siebens")
        member = Member.objects.create(first_name="Bernard", last_name="Siebens")
        FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(claim_label_for(member, self.club), "Siebens family")

    def test_a_child_in_the_family_shows_the_same_family_label(self):
        # The label doesn't distinguish which family member claimed it --
        # a child claiming reads identically to their parent claiming.
        family = Family.objects.create(club=self.club, name="Siebens")
        child = Member.objects.create(first_name="Charlotte", last_name="Siebens")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)

        self.assertEqual(claim_label_for(child, self.club), "Siebens family")

    def test_an_unnamed_family_falls_back_to_the_surname_join(self):
        # Family.__str__'s own fallback (no explicit name -- shared surnames
        # instead) is what claim_label_for leans on, so this stays correct
        # without duplicating that logic.
        family = Family.objects.create(club=self.club)
        member = Member.objects.create(first_name="Bernard", last_name="Siebens")
        FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(claim_label_for(member, self.club), "Siebens family")


class MemberIsDeletableTests(TestCase):
    """members.services.deletion.member_is_deletable -- the one rule every
    hard-delete of a Member (cancelling a sign-up, merging away a duplicate)
    goes through: Member is global, so a row with anything outside the
    memberships being removed is never deleted."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        cls.season = Season.objects.create(club=cls.club, start_date=date(2026, 8, 1), end_date=date(2027, 5, 31))
        cls.other_season = Season.objects.create(club=cls.other_club, start_date=date(2026, 8, 1), end_date=date(2027, 5, 31))

    def make_signup(self, **member_kwargs):
        member = Member.objects.create(first_name="New", last_name="Kid", **member_kwargs)
        membership = ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.PENDING)
        return member, membership

    def test_a_member_with_nothing_but_the_ignored_membership_is_deletable(self):
        member, membership = self.make_signup()

        self.assertTrue(member_is_deletable(member, ignoring=[membership]))

    def test_a_member_is_not_deletable_while_the_membership_is_not_ignored(self):
        member, _membership = self.make_signup()

        self.assertFalse(member_is_deletable(member))

    def test_a_member_with_a_login_is_never_deletable(self):
        user = User.objects.create_user(email="kid@example.com", password="pw-secret-123")
        member, membership = self.make_signup(user=user)

        self.assertFalse(member_is_deletable(member, ignoring=[membership]))

    def test_a_membership_in_another_club_keeps_the_member(self):
        member, membership = self.make_signup()
        ClubMembership.objects.create(club=self.other_club, member=member, season=self.other_season, status=ClubMembership.StatusChoices.ACTIVE)

        self.assertFalse(member_is_deletable(member, ignoring=[membership]))

    def test_an_elevated_role_keeps_the_member(self):
        member, membership = self.make_signup()
        ClubRole.objects.create(club=self.other_club, member=member, role=ClubRole.Roles.ADMIN)

        self.assertFalse(member_is_deletable(member, ignoring=[membership]))

    def test_the_implicit_member_role_of_the_ignored_club_does_not_count(self):
        member, membership = self.make_signup()
        membership.status = ClubMembership.StatusChoices.ACTIVE
        membership.save()  # club/signals.py grants the implicit MEMBER role

        self.assertTrue(ClubRole.objects.filter(club=self.club, member=member, role=ClubRole.Roles.MEMBER).exists())
        self.assertTrue(member_is_deletable(member, ignoring=[membership]))

    def test_a_family_in_another_club_keeps_the_member(self):
        member, membership = self.make_signup()
        FamilyMembership.objects.create(family=Family.objects.create(club=self.other_club), member=member, role=FamilyMembership.FamilyRole.CHILD)

        self.assertFalse(member_is_deletable(member, ignoring=[membership]))

    def test_a_child_row_in_this_clubs_family_does_not_keep_the_member(self):
        member, membership = self.make_signup()
        family = Family.objects.create(club=self.club)
        FamilyMembership.objects.create(family=family, member=Member.objects.create(first_name="Pat", last_name="Parent"), role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.CHILD)

        self.assertTrue(member_is_deletable(member, ignoring=[membership]))

    def test_a_parent_other_family_members_depend_on_is_kept(self):
        member, membership = self.make_signup()
        family = Family.objects.create(club=self.club)
        FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=Member.objects.create(first_name="Timmy", last_name="Kid"), role=FamilyMembership.FamilyRole.CHILD)

        self.assertFalse(member_is_deletable(member, ignoring=[membership]))

    def test_a_roster_spot_in_another_club_keeps_the_member(self):
        member, membership = self.make_signup()
        team = Team.objects.create(club=self.other_club, name="U10", short_name="U10")
        TeamMembership.objects.create(team=team, member=member, season=self.other_season)

        self.assertFalse(member_is_deletable(member, ignoring=[membership]))

    def test_a_roster_spot_for_the_ignored_season_does_not_keep_the_member(self):
        member, membership = self.make_signup()
        team = Team.objects.create(club=self.club, name="U10", short_name="U10")
        TeamMembership.objects.create(team=team, member=member, season=self.season)

        self.assertTrue(member_is_deletable(member, ignoring=[membership]))

    def test_a_staff_assignment_in_another_club_keeps_the_member(self):
        member, membership = self.make_signup()
        team = Team.objects.create(club=self.other_club, name="U10", short_name="U10")
        position = Position.objects.create(club=self.other_club, name="Coach", short_name="CO", staff_position=True)
        StaffAssignment.objects.create(team=team, member=member, season=self.other_season, position=position)

        self.assertFalse(member_is_deletable(member, ignoring=[membership]))


class MemberTiesOutsideClubTests(TestCase):
    """members.services.deletion.member_has_ties_outside_club /
    remove_member_from_club -- what one club may do to a global Member."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        cls.season = Season.objects.create(club=cls.club, start_date=date(2026, 8, 1), end_date=date(2027, 5, 31))
        cls.other_season = Season.objects.create(club=cls.other_club, start_date=date(2026, 8, 1), end_date=date(2027, 5, 31))

    def make_member(self, **kwargs):
        member = Member.objects.create(first_name="Pat", last_name="Person", **kwargs)
        ClubMembership.objects.create(club=self.club, member=member, season=self.season)
        return member

    def test_a_member_known_only_to_this_club_has_no_ties_outside_it(self):
        member = self.make_member()
        family = Family.objects.create(club=self.club)
        FamilyMembership.objects.create(family=family, member=member)

        self.assertFalse(member_has_ties_outside_club(member, self.club))

    def test_a_login_is_a_tie(self):
        user = User.objects.create_user(email="pat@example.com", password="x")

        self.assertTrue(member_has_ties_outside_club(self.make_member(user=user), self.club))

    def test_rows_in_another_club_are_ties(self):
        cases = {
            "membership": lambda member: ClubMembership.objects.create(club=self.other_club, member=member, season=self.other_season),
            "role": lambda member: ClubRole.objects.create(club=self.other_club, member=member, role=ClubRole.Roles.EDITOR),
            "family": lambda member: FamilyMembership.objects.create(family=Family.objects.create(club=self.other_club), member=member),
            "team": lambda member: TeamMembership.objects.create(team=Team.objects.create(club=self.other_club, name="T", short_name="T"), member=member, season=self.other_season),
        }
        for label, make_tie in cases.items():
            with self.subTest(label):
                member = self.make_member()
                make_tie(member)
                self.assertTrue(member_has_ties_outside_club(member, self.club))

    def test_every_cascading_member_relation_is_accounted_for(self):
        # A new club-owned relation on Member must be added to deletion.py's
        # lists, or removing someone from a club would silently miss it.
        from members.services import deletion

        known = {accessor for accessor, _lookup in (*deletion._CLUB_RELATIONS, *deletion._SHOP_RELATIONS, *deletion._CLUB_AUDIENCES)}
        for relation in Member._meta.related_objects:
            if relation.on_delete is SET_NULL:
                continue
            with self.subTest(relation.get_accessor_name()):
                self.assertIn(relation.get_accessor_name(), known)

    def test_remove_member_from_club_leaves_other_clubs_alone(self):
        member = self.make_member()
        ClubMembership.objects.create(club=self.other_club, member=member, season=self.other_season)
        own_family = Family.objects.create(club=self.club)
        FamilyMembership.objects.create(family=own_family, member=member)
        other_family = Family.objects.create(club=self.other_club)
        FamilyMembership.objects.create(family=other_family, member=member)

        remove_member_from_club(member, self.club)

        self.assertTrue(Member.objects.filter(pk=member.pk).exists())
        self.assertFalse(ClubMembership.objects.filter(club=self.club, member=member).exists())
        self.assertFalse(Family.objects.filter(pk=own_family.pk).exists())
        self.assertTrue(ClubMembership.objects.filter(club=self.other_club, member=member).exists())
        self.assertTrue(FamilyMembership.objects.filter(family=other_family, member=member).exists())

    def test_remove_member_from_club_keeps_a_family_others_still_belong_to(self):
        member = self.make_member()
        family = Family.objects.create(club=self.club)
        FamilyMembership.objects.create(family=family, member=member)
        kid = Member.objects.create(first_name="Kid", last_name="Person")
        FamilyMembership.objects.create(family=family, member=kid, role=FamilyMembership.FamilyRole.CHILD)

        remove_member_from_club(member, self.club)

        self.assertTrue(Family.objects.filter(pk=family.pk).exists())
        self.assertEqual(list(family.memberships.values_list("member", flat=True)), [kid.pk])


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "rival-fc.rosterchief.app", "testserver"])
class FamilyInviteAcceptTests(TestCase):
    """members.views.FamilyInviteAcceptView / members.services.invites: only
    the invited account, signed in, on the inviting club, once, before expiry."""

    HOST = "ajax-united.rosterchief.app"

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - timedelta(days=30), end_date=today + timedelta(days=300))
        cls.family = Family.objects.create(club=cls.club)
        cls.kid = Member.objects.create(first_name="Kid", last_name="Invitee")
        FamilyMembership.objects.create(family=cls.family, member=cls.kid, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=cls.club, member=cls.kid, season=cls.season)
        cls.user = User.objects.create_user(email="Invited@Example.com", password="pw-secret-123")
        EmailAddress.objects.create(user=cls.user, email=cls.user.email, verified=True, primary=True)
        cls.member = Member.objects.create(user=cls.user, first_name="Ivy", last_name="Invited")
        cls.stranger = User.objects.create_user(email="stranger@example.com", password="pw-secret-123")

    def make_invite(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("family", self.family)
        kwargs.setdefault("email", "invited@example.com")
        return FamilyInvite.objects.create(**kwargs)

    def url(self, invite):
        return reverse("members:family_invite_accept", args=[invite.token])

    def test_an_unconfirmed_address_cannot_accept(self):
        EmailAddress.objects.filter(user=self.user).update(verified=False)
        invite = self.make_invite()
        self.client.force_login(self.user)

        response = self.client.post(self.url(invite), HTTP_HOST=self.HOST)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(FamilyMembership.objects.filter(family=self.family, member=self.member).exists())
        invite.refresh_from_db()
        self.assertIsNone(invite.accepted_at)

    def test_the_invited_account_accepts_and_is_linked(self):
        invite = self.make_invite()
        self.client.force_login(self.user)

        page = self.client.get(self.url(invite), HTTP_HOST=self.HOST)
        response = self.client.post(self.url(invite), HTTP_HOST=self.HOST)

        self.assertContains(page, "Accept invite")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(FamilyMembership.objects.filter(family=self.family, member=self.member, role=FamilyMembership.FamilyRole.PARENT).exists())
        self.assertEqual(ClubMembership.objects.get(club=self.club, member=self.member, season=self.season).kind, ClubMembership.Kind.GUARDIAN)
        invite.refresh_from_db()
        self.assertIsNotNone(invite.accepted_at)
        self.assertEqual(invite.accepted_member, self.member)

    def test_an_invite_is_single_use(self):
        invite = self.make_invite()
        self.client.force_login(self.user)
        self.client.post(self.url(invite), HTTP_HOST=self.HOST)

        response = self.client.post(self.url(invite), HTTP_HOST=self.HOST)

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already been used", status_code=400)

    def test_another_account_is_refused_and_sees_no_family_details(self):
        invite = self.make_invite()
        self.client.force_login(self.stranger)

        page = self.client.get(self.url(invite), HTTP_HOST=self.HOST)
        response = self.client.post(self.url(invite), HTTP_HOST=self.HOST)

        self.assertNotContains(page, "Invitee")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Member.objects.filter(user=self.stranger).exists())
        self.assertFalse(FamilyMembership.objects.filter(family=self.family).exclude(member=self.kid).exists())
        invite.refresh_from_db()
        self.assertIsNone(invite.accepted_at)

    def test_an_expired_invite_is_refused(self):
        invite = self.make_invite(expires_at=timezone.now() - timedelta(minutes=1))
        self.client.force_login(self.user)

        response = self.client.post(self.url(invite), HTTP_HOST=self.HOST)

        self.assertContains(response, "expired", status_code=400)
        self.assertFalse(FamilyMembership.objects.filter(member=self.member).exists())

    def test_an_invite_only_works_on_the_club_that_sent_it(self):
        invite = self.make_invite()
        self.client.force_login(self.user)

        response = self.client.post(self.url(invite), HTTP_HOST="rival-fc.rosterchief.app")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(FamilyMembership.objects.filter(member=self.member).exists())

    def test_signing_in_is_required(self):
        invite = self.make_invite()

        response = self.client.get(self.url(invite), HTTP_HOST=self.HOST)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


MEMBER_PHOTO_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "rival-fc.rosterchief.app", "testserver"])
class MemberPhotoAccessTests(TestCase):
    """Member.photo lives in private storage and is only readable through
    members:photo -- staff who may see the member (on that club's host), the
    member themselves and their parents/guardians (any host). Teammates,
    other clubs and strangers get a 404."""

    CLUB_HOST = "ajax-united.rosterchief.app"
    OTHER_HOST = "rival-fc.rosterchief.app"
    BASE_HOST = "rosterchief.app"

    @classmethod
    def setUpTestData(cls):
        from rosterchief.test_support import enrol_mfa

        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - timedelta(days=30), end_date=today + timedelta(days=300))
        cls.other_season = Season.objects.create(club=cls.other_club, start_date=today - timedelta(days=30), end_date=today + timedelta(days=300))
        cls.team = Team.objects.create(club=cls.club, name="U12", short_name="U12")
        cls.player_position = Position.objects.create(club=cls.club, name="Forward", short_name="FW")
        cls.coach_position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)

        def person(email, first_name, **kwargs):
            user = User.objects.create_user(email=email, password="pw-secret-123")
            return user, Member.objects.create(user=user, first_name=first_name, last_name="Tester", **kwargs)

        cls.kid_user, cls.kid = person("kid@example.com", "Kid")
        ClubMembership.objects.create(club=cls.club, member=cls.kid, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        TeamMembership.objects.create(team=cls.team, member=cls.kid, season=cls.season, position=cls.player_position)

        cls.teammate_user, cls.teammate = person("teammate@example.com", "Teammate")
        ClubMembership.objects.create(club=cls.club, member=cls.teammate, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        TeamMembership.objects.create(team=cls.team, member=cls.teammate, season=cls.season, position=cls.player_position)

        cls.coach_user, cls.coach = person("coach@example.com", "Coach")
        StaffAssignment.objects.create(team=cls.team, member=cls.coach, season=cls.season, position=cls.coach_position)
        enrol_mfa(cls.coach_user)

        cls.parent_user, cls.parent = person("parent@example.com", "Parent")
        family = Family.objects.create(club=cls.club)
        FamilyMembership.objects.create(family=family, member=cls.parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=cls.kid, role=FamilyMembership.FamilyRole.CHILD)

        cls.sibling_user, cls.sibling = person("sibling@example.com", "Sibling")
        FamilyMembership.objects.create(family=family, member=cls.sibling, role=FamilyMembership.FamilyRole.CHILD)

        cls.admin_user, cls.admin = person("admin@example.com", "Admin")
        ClubMembership.objects.create(club=cls.club, member=cls.admin, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        ClubRole.objects.filter(club=cls.club, member=cls.admin).update(role=ClubRole.Roles.ADMIN)
        enrol_mfa(cls.admin_user)

        cls.member_admin_user, cls.member_admin = person("member-admin@example.com", "Memberadmin")
        ClubMembership.objects.create(club=cls.club, member=cls.member_admin, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        ClubRole.objects.filter(club=cls.club, member=cls.member_admin).update(role=ClubRole.Roles.MEMBER_ADMIN)
        enrol_mfa(cls.member_admin_user)

        cls.other_admin_user, cls.other_admin = person("other-admin@example.com", "Otheradmin")
        ClubMembership.objects.create(club=cls.other_club, member=cls.other_admin, season=cls.other_season, status=ClubMembership.StatusChoices.ACTIVE)
        ClubRole.objects.filter(club=cls.other_club, member=cls.other_admin).update(role=ClubRole.Roles.ADMIN)
        enrol_mfa(cls.other_admin_user)

        cls.stranger_user, cls.stranger = person("stranger@example.com", "Stranger")

    def setUp(self):
        self.kid.photo.save("kid.png", SimpleUploadedFile("kid.png", MEMBER_PHOTO_PNG, content_type="image/png"))
        self.addCleanup(self.kid.photo.delete, save=False)

    def fetch(self, user=None, host=CLUB_HOST, member=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(reverse("members:photo", args=[(member or self.kid).pk]), HTTP_HOST=host)

    # --- storage ---

    def test_the_photo_is_stored_privately(self):
        from django.core.files.storage import default_storage

        from rosterchief.storage import PrivateStorage

        self.assertIsInstance(self.kid.photo.storage, PrivateStorage)
        self.assertTrue(self.kid.photo.storage.exists(self.kid.photo.name))
        self.assertFalse(default_storage.exists(self.kid.photo.name))

    def test_photo_url_points_at_the_authorising_view(self):
        self.assertTrue(self.kid.photo_url.startswith(reverse("members:photo", args=[self.kid.pk])))
        self.assertEqual(self.kid.photo.url, self.kid.photo_url)
        self.assertNotIn("/media/", self.kid.photo_url)

    def test_photo_url_is_none_without_a_photo(self):
        self.assertIsNone(self.teammate.photo_url)

    # --- allowed ---

    def test_coach_of_the_members_team_can_see_it(self):
        response = self.fetch(self.coach_user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(b"".join(response.streaming_content), MEMBER_PHOTO_PNG)

    def test_club_admin_can_see_it(self):
        self.assertEqual(self.fetch(self.admin_user).status_code, 200)

    def test_member_admin_can_see_it(self):
        self.assertEqual(self.fetch(self.member_admin_user).status_code, 200)

    def test_the_member_themselves_can_see_it_on_any_host(self):
        self.assertEqual(self.fetch(self.kid_user, host=self.CLUB_HOST).status_code, 200)
        self.assertEqual(self.fetch(self.kid_user, host=self.BASE_HOST).status_code, 200)

    def test_a_parent_can_see_it_on_any_host(self):
        self.assertEqual(self.fetch(self.parent_user, host=self.CLUB_HOST).status_code, 200)
        self.assertEqual(self.fetch(self.parent_user, host=self.BASE_HOST).status_code, 200)
        self.assertEqual(self.fetch(self.parent_user, host=self.OTHER_HOST).status_code, 200)

    def test_anyone_can_see_it_once_public_consent_is_given(self):
        Member.objects.filter(pk=self.kid.pk).update(photo_public_consent=True)

        self.assertEqual(self.fetch(None, host=self.BASE_HOST).status_code, 200)

    # --- denied ---

    def test_anonymous_gets_404(self):
        self.assertEqual(self.fetch(None).status_code, 404)

    def test_a_stranger_gets_404(self):
        self.assertEqual(self.fetch(self.stranger_user).status_code, 404)

    def test_a_teammate_gets_404(self):
        self.assertEqual(self.fetch(self.teammate_user).status_code, 404)

    def test_a_sibling_gets_404(self):
        self.assertEqual(self.fetch(self.sibling_user).status_code, 404)

    def test_another_clubs_admin_gets_404(self):
        self.assertEqual(self.fetch(self.other_admin_user, host=self.OTHER_HOST).status_code, 404)

    def test_staff_authority_needs_that_clubs_host(self):
        self.assertEqual(self.fetch(self.coach_user, host=self.BASE_HOST).status_code, 404)
        self.assertEqual(self.fetch(self.admin_user, host=self.OTHER_HOST).status_code, 404)

    def test_a_member_admin_cannot_reach_a_member_outside_the_club(self):
        self.stranger.photo.save("stranger.png", SimpleUploadedFile("stranger.png", MEMBER_PHOTO_PNG, content_type="image/png"))
        self.addCleanup(self.stranger.photo.delete, save=False)

        self.assertEqual(self.fetch(self.member_admin_user, member=self.stranger).status_code, 404)

    def test_no_photo_is_404_even_for_the_member(self):
        self.assertEqual(self.fetch(self.teammate_user, member=self.teammate).status_code, 404)

    # --- templates ---

    def test_avatars_render_the_view_url(self):
        from django.template.loader import render_to_string

        for template in ("mobile/_avatar.html", "management/_avatar.html"):
            with self.subTest(template=template):
                html = render_to_string(template, {"person": self.kid})
                self.assertIn(self.kid.photo_url.replace("&", "&amp;"), html)
                self.assertNotIn("/media/", html)

    def test_member_edit_forms_render_with_a_private_photo(self):
        from management.forms import MemberForm
        from mobile.forms import MemberProfileForm

        for form_class in (MemberForm, MemberProfileForm):
            with self.subTest(form=form_class.__name__):
                html = str(form_class(instance=self.kid)["photo"])
                self.assertIn(reverse("members:photo", args=[self.kid.pk]), html)


class MovePhotosToPrivateStorageMigrationTests(TestCase):
    """members/migrations/0012: copies each Member.photo from the default
    (public) storage into private storage under the same name, then deletes
    the public copy. Safe to run twice; a missing source file is skipped."""

    def run_migration(self):
        import importlib

        from django.apps import apps

        module = importlib.import_module("members.migrations.0012_move_member_photos_to_private_storage")
        module.move_photos_to_private_storage(apps, None)

    def test_moves_a_public_file_into_private_storage(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from rosterchief.storage import private_storage

        member = Member.objects.create(first_name="Old", last_name="Upload")
        name = default_storage.save(f"members/{member.pk}/old.png", ContentFile(MEMBER_PHOTO_PNG))
        Member.objects.filter(pk=member.pk).update(photo=name)
        self.addCleanup(private_storage.delete, name)

        self.run_migration()
        self.run_migration()

        member.refresh_from_db()
        self.assertEqual(member.photo.name, name)
        self.assertTrue(private_storage.exists(name))
        self.assertFalse(default_storage.exists(name))
        with private_storage.open(name, "rb") as moved:
            self.assertEqual(moved.read(), MEMBER_PHOTO_PNG)

    def test_a_missing_source_file_is_skipped(self):
        member = Member.objects.create(first_name="Lost", last_name="File")
        Member.objects.filter(pk=member.pk).update(photo=f"members/{member.pk}/gone.png")

        self.run_migration()

        member.refresh_from_db()
        self.assertEqual(member.photo.name, f"members/{member.pk}/gone.png")


class PurgePublicMemberPhotosCommandTests(TestCase):
    def test_deletes_everything_under_members_in_default_storage_only(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        stale = default_storage.save("members/some-id/old.png", ContentFile(MEMBER_PHOTO_PNG))
        unrelated = default_storage.save("clubs/ajax-united/logo.png", ContentFile(MEMBER_PHOTO_PNG))
        self.addCleanup(default_storage.delete, unrelated)

        call_command("purge_public_member_photos", "--dry-run", stdout=StringIO())
        self.assertTrue(default_storage.exists(stale))

        call_command("purge_public_member_photos", stdout=StringIO())

        self.assertFalse(default_storage.exists(stale))
        self.assertTrue(default_storage.exists(unrelated))

    def test_nothing_to_purge(self):
        out = StringIO()

        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            call_command("purge_public_member_photos", stdout=out)

        self.assertIn("0 file(s)", out.getvalue())
