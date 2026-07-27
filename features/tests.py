from io import StringIO

from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from waffle import flag_is_active, get_waffle_flag_model

from club.models import Club

from .models import Maintenance

Flag = get_waffle_flag_model()
User = get_user_model()


class ClubScopedFlagTests(TestCase):
    def setUp(self):
        # waffle caches flags by name, and its cache is NOT rolled back with the
        # test transaction -- a flag row recreated under the same name in the next
        # test would otherwise be shadowed by the previous test's cached object.
        cache.clear()
        self.addCleanup(cache.clear)
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        self.flag = Flag.objects.create(name="shop")

    def request_for(self, club):
        request = RequestFactory().get("/")
        request.club = club
        request.user = None
        return request

    def active_for(self, club):
        return flag_is_active(self.request_for(club), "shop")

    def test_off_for_every_club_by_default(self):
        self.assertFalse(self.active_for(self.club))
        self.assertFalse(self.active_for(self.other))

    def test_on_only_for_the_targeted_club(self):
        self.flag.clubs.add(self.club)

        self.assertTrue(self.active_for(self.club))
        self.assertFalse(self.active_for(self.other))

    def test_removing_a_club_turns_it_off_again(self):
        self.flag.clubs.add(self.club)
        self.flag.clubs.remove(self.club)

        self.assertFalse(self.active_for(self.club))

    def test_everyone_true_overrides_club_targeting(self):
        self.flag.everyone = True
        self.flag.save()

        self.assertTrue(self.active_for(self.other))  # not targeted, still on

    def test_everyone_false_beats_club_targeting(self):
        # waffle's contract: `everyone` overrides ALL other settings, so a flag
        # switched off for everyone must stay off even for a targeted club.
        self.flag.clubs.add(self.club)
        self.flag.everyone = False
        self.flag.save()

        self.assertFalse(self.active_for(self.club))

    def test_no_club_on_the_request_is_not_active(self):
        # e.g. the base domain / control panel, where there is no tenant.
        self.flag.clubs.add(self.club)

        self.assertFalse(flag_is_active(self.request_for(None), "shop"))

    def test_is_active_for_club_without_a_request(self):
        self.flag.clubs.add(self.club)

        self.assertTrue(self.flag.is_active_for_club(self.club))
        self.assertFalse(self.flag.is_active_for_club(self.other))

    def test_is_active_for_club_respects_everyone(self):
        self.flag.everyone = False
        self.flag.save()
        self.assertFalse(self.flag.is_active_for_club(self.club))

        self.flag.everyone = True
        self.flag.save()
        self.assertTrue(self.flag.is_active_for_club(self.club))

    def test_cache_is_flushed_when_club_targeting_changes(self):
        # The M2M does not call save(), so without the flush signal waffle would
        # keep answering from a stale cached set.
        self.assertFalse(self.active_for(self.club))  # primes the cache

        self.flag.clubs.add(self.club)

        self.assertTrue(self.active_for(self.club))

    def test_cache_is_flushed_on_reverse_edit(self):
        self.assertFalse(self.active_for(self.club))

        self.club.flags.add(self.flag)

        self.assertTrue(self.active_for(self.club))


@override_settings(
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"],
)
class MaintenanceModeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.user = User.objects.create_user(email="root@example.com", password="pw-secret-123", is_staff=True)
        Authenticator.objects.create(user=self.user, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})

    def club_get(self, path="/"):
        return self.client.get(path, HTTP_HOST="ajax-united.rosterchief.app")

    def platform_get(self, path):
        return self.client.get(path, HTTP_HOST="rosterchief.app")

    def test_a_club_subdomain_is_closed(self):
        Maintenance.start(message="Upgrading the database.")

        response = self.club_get("/accounts/login/")

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Upgrading the database", status_code=503)
        self.assertEqual(response["Retry-After"], "3600")

    def test_a_club_is_open_again_when_maintenance_ends(self):
        Maintenance.start()
        Maintenance.stop()

        self.assertEqual(self.club_get("/accounts/login/").status_code, 200)

    def test_the_control_panel_stays_reachable(self):
        Maintenance.start()
        self.client.force_login(self.user)

        self.assertEqual(self.platform_get("/controlpanel/").status_code, 200)

    def test_signing_in_stays_possible(self):
        # Close /accounts/ as well and you cannot sign in to turn maintenance off: a
        # lock-down with no key, fixable only from a shell.
        Maintenance.start()

        self.assertEqual(self.platform_get("/accounts/login/").status_code, 200)

    def test_the_club_logo_still_loads_on_the_maintenance_page(self):
        # The maintenance page is rendered through the club's own skin specifically to show
        # its logo -- closing /media/ on the club subdomain too would serve the maintenance
        # page itself in place of that logo, making it look like the logo vanished.
        Maintenance.start()

        self.assertEqual(self.club_get("/media/clubs/ajax-united/crest.png").status_code, 404)

    def test_the_health_check_still_answers(self):
        # Close it and the load balancer decides the node is dead and stops routing to it —
        # taking the control panel down with everything else.
        Maintenance.start()

        self.assertEqual(self.platform_get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/healthz", HTTP_HOST="ajax-united.rosterchief.app").status_code, 200)

    def test_the_rest_of_the_base_domain_is_closed(self):
        Maintenance.start()

        self.assertEqual(self.platform_get("/").status_code, 503)

    def test_a_json_caller_gets_json(self):
        Maintenance.start(message="Back soon.")

        response = self.client.get("/", HTTP_HOST="ajax-united.rosterchief.app", headers={"accept": "application/json"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "maintenance")

    def test_the_message_is_optional(self):
        Maintenance.start()

        self.assertContains(self.club_get("/"), "down for maintenance", status_code=503)

    def test_it_says_which_state_it_is_in(self):
        self.assertEqual(str(Maintenance.start()), "Maintenance on")
        self.assertEqual(str(Maintenance.stop()), "Maintenance off")

    def test_it_records_who_closed_the_platform_and_when(self):
        maintenance = Maintenance.start(message="db upgrade", user=self.user)

        self.assertTrue(maintenance.is_active)
        self.assertEqual(maintenance.started_by, self.user)
        self.assertIsNotNone(maintenance.started_at)

    def test_the_state_is_shared_rather_than_per_process(self):
        # The cache is the shared one the flags use, so closing the platform reaches every
        # worker and every server. A per-process cache would leave some workers serving clubs.
        Maintenance.start()

        self.assertTrue(cache.get(Maintenance.CACHE_KEY).is_active)
        self.assertTrue(Maintenance.is_on())

        Maintenance.stop()

        self.assertFalse(cache.get(Maintenance.CACHE_KEY).is_active)


class MaintenanceCommandTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def run_command(self, name, *args):
        out = StringIO()
        call_command(name, *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_a_scheduled_job_stands_down(self):
        Maintenance.start()

        with self.assertRaises(CommandError):
            self.run_command("archive_overdue_clubs")

    def test_it_runs_again_once_the_platform_reopens(self):
        Maintenance.start()
        Maintenance.stop()

        self.assertIn("Nothing overdue", self.run_command("archive_overdue_clubs"))

    def test_an_override_exists_for_when_you_mean_it(self):
        Maintenance.start()

        self.assertIn("Nothing overdue", self.run_command("archive_overdue_clubs", "--ignore-maintenance"))

    def test_migrate_is_not_blocked(self):
        # Maintenance is usually declared IN ORDER to migrate. A guard on every command would
        # mean turning the mode off to do the work you turned it on for.
        Maintenance.start()

        self.run_command("migrate", "--check")  # raises SystemExit only if migrations are pending
