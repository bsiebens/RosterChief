import datetime

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase

from club.models import Club, ClubMembership, Season
from members.models import Family, FamilyMembership, Member

from .models import Notification
from .services import notify_members, recipient_emails

User = get_user_model()


def make_season(club, start_year=2026):
    return Season.objects.create(club=club, start_date=datetime.date(start_year, 8, 1), end_date=datetime.date(start_year + 1, 5, 31))


class RecipientEmailsTests(TestCase):
    """notifications.services.recipient_emails -- the member's own email if
    they hold a login, plus every parent/guardian's, always."""

    def test_a_member_with_a_login_gets_their_own_email(self):
        user = User.objects.create_user(email="jamie@example.com", password="pw-secret-123")
        member = Member.objects.create(user=user, first_name="Jamie", last_name="Doe", email="jamie@example.com")

        self.assertEqual(recipient_emails(member), ["jamie@example.com"])

    def test_a_member_with_no_login_gets_nothing_from_themselves(self):
        # Roster-imported, never signed up -- see members.models.ParentClaim's
        # own docstring for why this is routine, not an edge case.
        member = Member.objects.create(first_name="Jamie", last_name="Doe", email="jamie@example.com")

        self.assertEqual(recipient_emails(member), [])

    def test_guardians_are_always_included_even_with_the_childs_own_login(self):
        user = User.objects.create_user(email="jamie@example.com", password="pw-secret-123")
        family = Family.objects.create()
        child = Member.objects.create(user=user, first_name="Jamie", last_name="Doe", email="jamie@example.com")
        parent = Member.objects.create(first_name="Alex", last_name="Doe", email="alex@example.com")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)

        emails = recipient_emails(child)

        self.assertIn("jamie@example.com", emails)
        self.assertIn("alex@example.com", emails)

    def test_a_child_with_no_login_still_reaches_their_guardian(self):
        family = Family.objects.create()
        child = Member.objects.create(first_name="Jamie", last_name="Doe")
        parent = Member.objects.create(first_name="Alex", last_name="Doe", email="alex@example.com")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(recipient_emails(child), ["alex@example.com"])

    def test_duplicate_addresses_are_deduplicated(self):
        user = User.objects.create_user(email="shared@example.com", password="pw-secret-123")
        family = Family.objects.create()
        child = Member.objects.create(user=user, first_name="Jamie", last_name="Doe", email="shared@example.com")
        parent = Member.objects.create(first_name="Alex", last_name="Doe", email="shared@example.com")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(recipient_emails(child), ["shared@example.com"])

    def test_empty_when_nobody_is_reachable(self):
        member = Member.objects.create(first_name="Jamie", last_name="Doe")

        self.assertEqual(recipient_emails(member), [])


class NotifyMembersTests(TestCase):
    """notifications.services.notify_members -- one Notification per member,
    emailed to whoever recipient_emails() resolves for them."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)

    def make_member(self, *, with_login=True, email="jamie@example.com"):
        user = User.objects.create_user(email=email, password="pw-secret-123") if with_login else None
        return Member.objects.create(user=user, first_name="Jamie", last_name="Doe", email=email if with_login else "")

    def test_creates_one_notification_per_member(self):
        members = [self.make_member(email=f"m{i}@example.com") for i in range(3)]

        notifications = notify_members(members, club=self.club, title="News", body="Something happened.")

        self.assertEqual(len(notifications), 3)
        self.assertEqual(Notification.objects.filter(club=self.club).count(), 3)

    def test_emails_the_resolved_addresses(self):
        member = self.make_member(email="jamie@example.com")

        notify_members([member], club=self.club, title="Big win", body="We won 3-0.")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["jamie@example.com"])
        self.assertIn("Big win", mail.outbox[0].subject)
        self.assertIn("We won 3-0.", mail.outbox[0].body)

    def test_the_email_carries_an_html_alternative(self):
        member = self.make_member()

        notify_members([member], club=self.club, title="Big win", body="We won 3-0.")

        [(html_body, mimetype)] = mail.outbox[0].alternatives
        self.assertEqual(mimetype, "text/html")
        self.assertIn("Big win", html_body)

    def test_sent_at_and_sent_to_emails_are_recorded(self):
        member = self.make_member(email="jamie@example.com")

        [notification] = notify_members([member], club=self.club, title="News", body="Body.")

        self.assertIsNotNone(notification.sent_at)
        self.assertEqual(notification.sent_to_emails, ["jamie@example.com"])
        self.assertTrue(notification.is_sent)

    def test_a_notification_is_still_created_when_nobody_is_reachable(self):
        member = self.make_member(with_login=False)

        [notification] = notify_members([member], club=self.club, title="News", body="Body.")

        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(notification.sent_at)
        self.assertEqual(notification.sent_to_emails, [])
        self.assertFalse(notification.is_sent)

    def test_the_source_is_recorded_as_a_generic_relation(self):
        member = self.make_member()

        [notification] = notify_members([member], club=self.club, title="News", body="Body.", source=self.season)

        self.assertEqual(notification.source, self.season)


class NotificationModelTests(TestCase):
    def test_str_is_member_and_title(self):
        club = Club.objects.create(name="Ajax United", slug="ajax-united")
        member = Member.objects.create(first_name="Jamie", last_name="Doe")
        notification = Notification.objects.create(club=club, member=member, title="Big win", body="Body.")

        self.assertEqual(str(notification), f"{member} — Big win")

    def test_clean_rejects_a_member_with_no_membership_in_this_club(self):
        club = Club.objects.create(name="Ajax United", slug="ajax-united")
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        season = make_season(other_club)
        member = Member.objects.create(first_name="Jamie", last_name="Doe")
        ClubMembership.objects.create(club=other_club, member=member, season=season, status=ClubMembership.StatusChoices.ACTIVE)

        notification = Notification(club=club, member=member, title="News", body="Body.")

        with self.assertRaises(ValidationError):
            notification.clean()
