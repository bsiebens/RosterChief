import tempfile
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

from allauth.mfa.models import Authenticator
from django.contrib.admin.sites import AdminSite
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.db.models import SET_NULL
from django.test import TestCase
from django.utils import timezone

from authentication.models import User
from club.models import Club, ClubMembership, Season
from members.admin import FamilyAdmin
from members.models import Family, FamilyMembership, Member
from members.services import MemberImportResult


# Create your tests here.
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


class FamilyNameOptionalTests(TestCase):
    def test_family_can_be_created_without_a_name(self):
        family = Family.objects.create()
        self.assertEqual(family.name, "")

    def test_str_uses_name_when_present(self):
        self.assertEqual(str(Family.objects.create(name="The Smiths")), "The Smiths")

    def test_str_falls_back_to_member_surnames(self):
        family = Family.objects.create()
        smith = Member.objects.create(first_name="Pat", last_name="Smith")
        jones = Member.objects.create(first_name="Kim", last_name="Jones")
        FamilyMembership.objects.create(family=family, member=smith, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=jones, role=FamilyMembership.FamilyRole.CHILD)

        # Distinct surnames, alphabetically ordered.
        self.assertEqual(str(family), "Jones / Smith family")

    def test_str_falls_back_to_short_id_when_empty(self):
        family = Family.objects.create()
        self.assertEqual(str(family), f"Family {str(family.pk)[:8]}")


class FamilyModelTests(TestCase):
    def setUp(self):
        self.family = Family.objects.create(name="The Smiths")
        self.parent = Member.objects.create(first_name="Pat", last_name="Smith")
        self.guardian = Member.objects.create(first_name="Gale", last_name="Smith")
        self.child = Member.objects.create(first_name="Kim", last_name="Smith")
        self.other = Member.objects.create(first_name="Ola", last_name="Smith")

        FamilyMembership.objects.create(family=self.family, member=self.parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=self.family, member=self.guardian, role=FamilyMembership.FamilyRole.GUARDIAN)
        FamilyMembership.objects.create(family=self.family, member=self.child, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=self.family, member=self.other, role=FamilyMembership.FamilyRole.OTHER)

    def test_str(self):
        self.assertEqual(str(self.family), "The Smiths")

    def test_guardians_include_parents_and_guardians_only(self):
        guardians = set(self.family.guardians)
        self.assertEqual(guardians, {self.parent, self.guardian})

    def test_children_include_children_only(self):
        children = list(self.family.children)
        self.assertEqual(children, [self.child])

    def test_guardians_are_scoped_to_the_family(self):
        other_family = Family.objects.create(name="The Joneses")
        outsider = Member.objects.create(first_name="Out", last_name="Sider")
        FamilyMembership.objects.create(family=other_family, member=outsider, role=FamilyMembership.FamilyRole.PARENT)

        self.assertNotIn(outsider, set(self.family.guardians))


class MemberGuardiansTests(TestCase):
    def test_guardians_of_a_child_are_family_parents_and_guardians(self):
        family = Family.objects.create(name="The Does")
        mum = Member.objects.create(first_name="Mary", last_name="Doe")
        legal = Member.objects.create(first_name="Lee", last_name="Doe")
        kid = Member.objects.create(first_name="Kit", last_name="Doe")

        FamilyMembership.objects.create(family=family, member=mum, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=legal, role=FamilyMembership.FamilyRole.GUARDIAN)
        FamilyMembership.objects.create(family=family, member=kid, role=FamilyMembership.FamilyRole.CHILD)

        self.assertEqual(set(kid.guardians), {mum, legal})

    def test_guardians_empty_for_a_parent(self):
        family = Family.objects.create(name="The Roes")
        parent = Member.objects.create(first_name="Ray", last_name="Roe")
        kid = Member.objects.create(first_name="Ren", last_name="Roe")

        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=kid, role=FamilyMembership.FamilyRole.CHILD)

        self.assertEqual(list(parent.guardians), [])

    def test_guardians_do_not_leak_across_families(self):
        family_a = Family.objects.create(name="Family A")
        family_b = Family.objects.create(name="Family B")
        parent_a = Member.objects.create(first_name="Ann", last_name="A")
        parent_b = Member.objects.create(first_name="Ben", last_name="B")
        kid = Member.objects.create(first_name="Cody", last_name="A")

        FamilyMembership.objects.create(family=family_a, member=parent_a, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family_a, member=kid, role=FamilyMembership.FamilyRole.CHILD)
        # parent_b belongs to a different family and must not appear as kid's guardian.
        FamilyMembership.objects.create(family=family_b, member=parent_b, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(set(kid.guardians), {parent_a})


class FamilyMembershipModelTests(TestCase):
    def test_default_role_is_parent(self):
        family = Family.objects.create(name="Fam")
        member = Member.objects.create(first_name="D", last_name="Efault")
        membership = FamilyMembership.objects.create(family=family, member=member)

        self.assertEqual(membership.role, FamilyMembership.FamilyRole.PARENT)

    def test_str_includes_family_member_and_role(self):
        family = Family.objects.create(name="The Smiths")
        member = Member.objects.create(first_name="Pat", last_name="Smith")
        membership = FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.GUARDIAN)

        self.assertEqual(str(membership), "The Smiths - Pat Smith (guardian)")

    def test_member_unique_per_family(self):
        family = Family.objects.create(name="Fam")
        member = Member.objects.create(first_name="Solo", last_name="Once")
        FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.PARENT)

        with self.assertRaises(IntegrityError):
            FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.CHILD)

    def test_same_member_can_join_multiple_families(self):
        member = Member.objects.create(first_name="Multi", last_name="Fam")
        family_a = Family.objects.create(name="A")
        family_b = Family.objects.create(name="B")

        FamilyMembership.objects.create(family=family_a, member=member, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family_b, member=member, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(member.family_memberships.count(), 2)

    def test_deleting_family_cascades_to_memberships(self):
        family = Family.objects.create(name="Doomed")
        member = Member.objects.create(first_name="Cas", last_name="Cade")
        FamilyMembership.objects.create(family=family, member=member)

        family.delete()

        self.assertFalse(FamilyMembership.objects.exists())
        # The member itself survives; only the membership is removed.
        self.assertTrue(Member.objects.filter(pk=member.pk).exists())


class FamilyAdminTests(TestCase):
    def test_member_count_reflects_memberships(self):
        family = Family.objects.create(name="The Smiths")
        for i in range(3):
            member = Member.objects.create(first_name=f"Kid{i}", last_name="Smith")
            FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.CHILD)

        admin_instance = FamilyAdmin(Family, AdminSite())
        self.assertEqual(admin_instance.member_count(family), 3)

    def test_member_count_is_zero_without_members(self):
        family = Family.objects.create(name="Empty")
        admin_instance = FamilyAdmin(Family, AdminSite())
        self.assertEqual(admin_instance.member_count(family), 0)


class AdminSmokeTests(TestCase):
    """Exercise the admin config end-to-end to catch misregistration
    (bad search_fields, autocomplete targets, fieldsets, custom forms)."""

    def setUp(self):
        self.admin = User.objects.create_superuser(email="root@example.com", password="pw-secret-123")
        # Staff must hold a second factor (RequireMFAMiddleware).
        Authenticator.objects.create(user=self.admin, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})
        self.client.force_login(self.admin)

    def test_changelists_load(self):
        for app_label, model in (
            ("authentication", "user"),
            ("members", "member"),
            ("members", "family"),
            ("members", "familymembership"),
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
    def setUp(self):
        # Memberships are season-scoped: the importer attaches each one to the
        # club's current season, so the target club needs one covering today.
        self.club = Club.objects.create(name="City Swim Club")
        today = timezone.localdate()
        self.season = Season.objects.create(
            club=self.club,
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
        # "New Club" has no season, so the row is skipped and — because the row
        # is atomic — the member and club creation roll back too.
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
        self.assertFalse(Club.objects.filter(name="New Club").exists())


class MemberImportResultTests(TestCase):
    def test_successful_rows_sums_created_and_updated(self):
        result = MemberImportResult(created_members=2, updated_members=3)
        self.assertEqual(result.successful_rows, 5)
