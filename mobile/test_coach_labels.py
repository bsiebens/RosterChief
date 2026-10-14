import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from club.models import Season
from events.models import Event
from members.models import Member
from rosterchief.test_support import start_auto_mfa_enrolment, stop_auto_mfa_enrolment
from teams.models import Position, StaffAssignment, Team

from .test_support_coach import make_club

User = get_user_model()


def setUpModule():
    start_auto_mfa_enrolment()


def tearDownModule():
    stop_auto_mfa_enrolment()


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachScheduleCancelledRowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=member, season=season, position=position)

    def test_cancelled_row_is_dimmed_with_pill_and_live_row_is_not(self):
        live = Event.objects.create(club=self.club, title="Live game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=1))
        live.teams.add(self.team)
        cancelled = Event.objects.create(club=self.club, title="Gone game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=2), cancelled=True)
        cancelled.teams.add(self.team)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_schedule"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "opacity-60", count=1)
        self.assertContains(response, "Cancelled</span>", count=1)
