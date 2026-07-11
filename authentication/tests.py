import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from members.models import Member

User = get_user_model()


class UserManagerTests(TestCase):
    def test_create_user_defaults(self):
        user = User.objects.create_user(email="alice@example.com", password="secret123")

        self.assertEqual(user.email, "alice@example.com")
        self.assertTrue(user.check_password("secret123"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)

    def test_create_user_requires_email(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="secret123")

    def test_create_user_normalizes_email_domain(self):
        # BaseUserManager lowercases the domain part of the address.
        user = User.objects.create_user(email="Bob@Example.COM", password="secret123")

        self.assertEqual(user.email, "Bob@example.com")

    def test_create_user_password_is_hashed(self):
        user = User.objects.create_user(email="carol@example.com", password="secret123")

        self.assertNotEqual(user.password, "secret123")

    def test_create_user_without_password_is_unusable(self):
        user = User.objects.create_user(email="dave@example.com")

        self.assertFalse(user.has_usable_password())

    def test_create_superuser_defaults(self):
        admin = User.objects.create_superuser(email="admin@example.com", password="secret123")

        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_active)

    def test_create_superuser_rejects_non_staff(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email="admin@example.com", password="x", is_staff=False)

    def test_create_superuser_rejects_non_superuser(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email="admin@example.com", password="x", is_superuser=False)


class UserModelTests(TestCase):
    def test_email_is_username_field(self):
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertEqual(User.REQUIRED_FIELDS, [])

    def test_email_is_unique(self):
        User.objects.create_user(email="dup@example.com", password="x")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(email="dup@example.com", password="y")

    def test_pk_is_uuid(self):
        user = User.objects.create_user(email="uuid@example.com", password="x")
        self.assertIsInstance(user.pk, uuid.UUID)

    def test_str_and_names_fall_back_to_email_without_member(self):
        user = User.objects.create_user(email="lonely@example.com", password="x")

        self.assertEqual(str(user), "lonely@example.com")
        self.assertEqual(user.get_full_name(), "lonely@example.com")
        self.assertEqual(user.get_short_name(), "lonely@example.com")

    def test_str_and_names_use_linked_member(self):
        user = User.objects.create_user(email="linked@example.com", password="x")
        Member.objects.create(user=user, first_name="Jane", last_name="Doe")

        # Re-fetch so the reverse OneToOne relation is resolved from the DB.
        user = User.objects.get(pk=user.pk)

        self.assertEqual(str(user), "Jane Doe")
        self.assertEqual(user.get_full_name(), "Jane Doe")
        self.assertEqual(user.get_short_name(), "Jane")
