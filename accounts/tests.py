from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Family, Guardianship, Member, User


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


class FamilyAndGuardianshipTests(TestCase):
    def setUp(self):
        self.family = Family.objects.create(name="The Smiths")
        self.parent = Member.objects.create(
            first_name="Ann", last_name="Smith", date_of_birth="1980-01-01", family=self.family
        )
        self.child = Member.objects.create(
            first_name="Kid", last_name="Smith", date_of_birth="2015-05-01", family=self.family
        )

    def test_family_groups_members(self):
        self.assertEqual(self.family.members.count(), 2)

    def test_guardianship_relations_resolve(self):
        Guardianship.objects.create(guardian=self.parent, child=self.child)
        self.assertIn(self.parent, self.child.guardians.all())
        self.assertIn(self.child, self.parent.dependents.all())

    def test_no_self_guardianship(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Guardianship.objects.create(guardian=self.parent, child=self.parent)

    def test_guardianship_is_unique(self):
        Guardianship.objects.create(guardian=self.parent, child=self.child)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Guardianship.objects.create(guardian=self.parent, child=self.child)
