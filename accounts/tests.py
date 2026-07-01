from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Family, Member, User


class UserManagerTests(TestCase):
    def test_create_user_with_email(self):
        user = User.objects.create_user(email="Parent@Example.com", password="pw")
        self.assertEqual(user.email, "Parent@example.com")  # domain normalized
        self.assertTrue(user.check_password("pw"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_superuser(self):
        admin = User.objects.create_superuser(email="admin@example.com", password="pw")
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_create_user_requires_email(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="pw")

    def test_create_superuser_rejects_non_superuser_flag(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email="a@b.com", password="pw", is_superuser=False)


class MemberTests(TestCase):
    def test_member_without_login_or_optional_fields(self):
        member = Member.objects.create(
            first_name="Kid",
            last_name="Smith",
            date_of_birth="2015-05-01",
        )
        self.assertIsNone(member.user)
        self.assertEqual(member.email, "")
        self.assertEqual(member.phone, "")
        self.assertEqual(member.license_number, "")

    def test_valid_phone_accepted(self):
        member = Member(
            first_name="Ann",
            last_name="Smith",
            date_of_birth="1980-01-01",
            phone="+32470123456",
        )
        member.full_clean()  # should not raise

    def test_invalid_phone_rejected(self):
        member = Member(
            first_name="Ann",
            last_name="Smith",
            date_of_birth="1980-01-01",
            phone="not-a-number",
        )
        with self.assertRaises(ValidationError):
            member.full_clean()

    def test_contact_email_prefers_own_then_login(self):
        # No own email, no user -> empty.
        member = Member.objects.create(
            first_name="Kid", last_name="Smith", date_of_birth="2015-05-01"
        )
        self.assertEqual(member.contact_email, "")

        # Linked user, no own email -> falls back to login email.
        member.user = User.objects.create_user(email="login@example.com", password="pw")
        member.save()
        self.assertEqual(member.contact_email, "login@example.com")

        # Own contact email wins over login email.
        member.email = "contact@example.com"
        self.assertEqual(member.contact_email, "contact@example.com")


class FamilyTests(TestCase):
    def setUp(self):
        self.family = Family.objects.create(name="The Smiths")
        self.parent = Member.objects.create(
            first_name="Ann", last_name="Smith", date_of_birth="1980-01-01",
            family=self.family, is_guardian=True,
        )
        self.child = Member.objects.create(
            first_name="Kid", last_name="Smith", date_of_birth="2015-05-01",
            family=self.family, is_guardian=False,
        )

    def test_family_groups_members(self):
        self.assertEqual(self.family.members.count(), 2)

    def test_child_guardians_are_family_guardians(self):
        self.assertIn(self.parent, self.child.guardians)
        self.assertNotIn(self.child, self.child.guardians)

    def test_guardian_dependents_are_family_non_guardians(self):
        self.assertIn(self.child, self.parent.dependents)
        self.assertNotIn(self.parent, self.parent.dependents)

    def test_guardian_has_no_guardians_and_child_has_no_dependents(self):
        self.assertEqual(list(self.parent.guardians), [])
        self.assertEqual(list(self.child.dependents), [])

    def test_member_without_family_has_no_relations(self):
        loner = Member.objects.create(
            first_name="Solo", last_name="Jones", date_of_birth="1990-01-01"
        )
        self.assertEqual(list(loner.guardians), [])
        self.assertEqual(list(loner.dependents), [])
