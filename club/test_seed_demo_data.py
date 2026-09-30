import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from club.management.commands.seed_demo_data import Command
from club.models import Season
from members.models import Member
from rosterchief.test_support import make_club

User = get_user_model()


class SeedDemoDataTests(TestCase):
    def setUp(self):
        self.club = make_club(name="Demo", slug="demo")
        self.user = User.objects.create_user(email="admin@example.com", password="pw-secret-123")
        Member.objects.create(user=self.user, first_name="Ada", last_name="Admin")
        self.old_season = Season.objects.create(club=self.club, start_date=timezone.localdate(), end_date=timezone.localdate())

    def run_command(self):
        with patch("rosterchief.concurrency.threading.Thread"):  # no background notification threads in tests
            call_command("seed_demo_data", "--club", "demo", "--admin-email", "admin@example.com", stdout=io.StringIO())

    def test_reseed_leaves_a_season_covering_today(self):
        self.run_command()
        self.assertIsNotNone(Season.covering(self.club, timezone.localdate()))

    def test_a_failed_reseed_leaves_the_old_club_intact(self):
        # One transaction: nobody ever sees the club half-built (e.g. without seasons),
        # and a failure rolls the wipe back.
        with patch.object(Command, "_seed", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            self.run_command()
        self.assertTrue(Season.objects.filter(pk=self.old_season.pk).exists())
