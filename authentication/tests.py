import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import SET_NULL
from django.test import TestCase

from authentication.models import Family, FamilyMembership, Member

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


class MemberModelTests(TestCase):
    def test_str_and_name_helpers(self):
        member = Member.objects.create(first_name="John", last_name="Smith")

        self.assertEqual(str(member), "John Smith")
        self.assertEqual(member.get_full_name(), "John Smith")
        self.assertEqual(member.get_short_name(), "John")

    def test_get_full_name_strips_when_partial(self):
        member = Member.objects.create(first_name="Cher", last_name="")
        self.assertEqual(member.get_full_name(), "Cher")

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
