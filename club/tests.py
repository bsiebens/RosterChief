import uuid

from django.db import IntegrityError
from django.test import TestCase

from authentication.models import Member

from .models import Club, ClubMembership


class ClubModelTests(TestCase):
    def test_str_returns_name(self):
        club = Club.objects.create(name="City Swim Club")

        self.assertEqual(str(club), "City Swim Club")

    def test_pk_is_uuid(self):
        club = Club.objects.create(name="City Swim Club")

        self.assertIsInstance(club.pk, uuid.UUID)

    def test_clubs_are_ordered_by_name(self):
        Club.objects.create(name="Zulu Club")
        Club.objects.create(name="Alpha Club")
        Club.objects.create(name="Middle Club")

        self.assertEqual(
            list(Club.objects.values_list("name", flat=True)),
            ["Alpha Club", "Middle Club", "Zulu Club"],
        )

    def test_verbose_names(self):
        self.assertEqual(Club._meta.verbose_name, "club")
        self.assertEqual(Club._meta.verbose_name_plural, "clubs")


class ClubMembershipModelTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="City Swim Club")
        self.member = Member.objects.create(
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    def test_str_returns_club_and_member(self):
        membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        self.assertEqual(str(membership), "City Swim Club - Jane Doe")

    def test_license_is_optional(self):
        membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
        )

        self.assertEqual(membership.license, "")

    def test_pk_is_uuid(self):
        membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
        )

        self.assertIsInstance(membership.pk, uuid.UUID)

    def test_same_member_can_join_different_clubs(self):
        other_club = Club.objects.create(name="Other Swim Club")

        first_membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )
        second_membership = ClubMembership.objects.create(
            club=other_club,
            member=self.member,
            license="LIC-002",
        )

        self.assertEqual(first_membership.member, self.member)
        self.assertEqual(second_membership.member, self.member)
        self.assertEqual(self.member.member_of.count(), 2)

    def test_member_is_unique_per_club(self):
        ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        with self.assertRaises(IntegrityError):
            ClubMembership.objects.create(
                club=self.club,
                member=self.member,
                license="LIC-002",
            )

    def test_deleting_club_deletes_membership_but_keeps_member(self):
        ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        self.club.delete()

        self.assertFalse(ClubMembership.objects.exists())
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())

    def test_deleting_member_deletes_membership_but_keeps_club(self):
        ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        self.member.delete()

        self.assertFalse(ClubMembership.objects.exists())
        self.assertTrue(Club.objects.filter(pk=self.club.pk).exists())

    def test_memberships_are_ordered_by_club_then_member_name(self):
        alpha_club = Club.objects.create(name="Alpha Club")
        zulu_club = Club.objects.create(name="Zulu Club")

        jane = Member.objects.create(first_name="Jane", last_name="Doe")
        alice = Member.objects.create(first_name="Alice", last_name="Smith")
        bob = Member.objects.create(first_name="Bob", last_name="Smith")

        ClubMembership.objects.create(club=zulu_club, member=bob)
        ClubMembership.objects.create(club=alpha_club, member=bob)
        ClubMembership.objects.create(club=alpha_club, member=alice)
        ClubMembership.objects.create(club=alpha_club, member=jane)

        self.assertEqual(
            [(membership.club.name, membership.member.last_name, membership.member.first_name) for membership in ClubMembership.objects.all()],
            [
                ("Alpha Club", "Doe", "Jane"),
                ("Alpha Club", "Smith", "Alice"),
                ("Alpha Club", "Smith", "Bob"),
                ("Zulu Club", "Smith", "Bob"),
            ],
        )

    def test_reverse_relations(self):
        membership = ClubMembership.objects.create(
            club=self.club,
            member=self.member,
            license="LIC-001",
        )

        self.assertEqual(list(self.club.members.all()), [membership])
        self.assertEqual(list(self.member.member_of.all()), [membership])

    def test_verbose_names(self):
        self.assertEqual(ClubMembership._meta.verbose_name, "club membership")
        self.assertEqual(ClubMembership._meta.verbose_name_plural, "club memberships")


# Create your tests here.
