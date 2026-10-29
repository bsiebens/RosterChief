from io import StringIO
from unittest import mock

from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from waffle import flag_is_active, get_waffle_flag_model

from club.models import Club

from .jobs import JOB_REGISTRY
from .models import EmailSuppression, JobRun, JobToggle, Maintenance

Flag = get_waffle_flag_model()
User = get_user_model()


class ClubScopedFlagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        cls.flag = Flag.objects.create(name="shop")

    def setUp(self):
        # waffle caches flags by name, and its cache is NOT rolled back with the
        # test transaction -- a flag row whose targeting changed in the previous
        # test would otherwise be shadowed by that test's cached object. Has to
        # stay per-test: it is the cache, not the rows, that leaks.
        cache.clear()
        self.addCleanup(cache.clear)

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

    def test_a_flag_name_with_no_matching_row_is_off_not_an_error(self):
        # Regression: waffle's own BaseModel.get() falls back to a transient,
        # unsaved Flag(name=...) instance when nothing in the DB matches the
        # name -- an M2M lookup (self.clubs) against that unsaved instance used
        # to raise ValueError instead of just resolving to "off". This is the
        # normal state for any flag nobody has created in the control panel yet.
        self.assertFalse(flag_is_active(self.request_for(self.club), "no-such-flag"))

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
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.user = User.objects.create_user(email="root@example.com", password="pw-secret-123", is_staff=True)
        Authenticator.objects.create(user=cls.user, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})

    def setUp(self):
        # Maintenance state lives in the shared cache, which no transaction rolls
        # back -- each test has to start from a reopened platform.
        cache.clear()
        self.addCleanup(cache.clear)

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

        self.assertEqual(self.platform_get("/control/").status_code, 200)

    def test_the_control_panel_stays_reachable_without_a_trailing_slash(self):
        # Maintenance runs before APPEND_SLASH can add the slash, and "/control" is what people type.
        Maintenance.start()
        self.client.force_login(self.user)

        response = self.platform_get("/control")

        self.assertNotEqual(response.status_code, 503)
        self.assertRedirects(response, "/control/", fetch_redirect_response=False, status_code=301)

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


class EmailSuppressionTests(TestCase):
    """features.models.EmailSuppression -- the platform-wide "pause automated
    email" switch. Mirrors MaintenanceModeTests' own model-level coverage above;
    see rosterchief.tests.SendMessageTests for the actual send-gating behaviour
    and authentication.tests for the password-reset exemption."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="root@example.com", password="pw-secret-123", is_staff=True)

    def setUp(self):
        # Same cache-not-rolled-back-by-the-transaction reasoning as
        # MaintenanceModeTests.setUp -- see that method's own comment.
        cache.clear()
        self.addCleanup(cache.clear)

    def test_off_by_default(self):
        self.assertFalse(EmailSuppression.is_on())

    def test_it_says_which_state_it_is_in(self):
        self.assertEqual(str(EmailSuppression.start()), "Automated email paused")
        self.assertEqual(str(EmailSuppression.stop()), "Automated email on")

    def test_it_records_who_paused_it_and_when(self):
        suppression = EmailSuppression.start(user=self.user)

        self.assertTrue(suppression.is_active)
        self.assertEqual(suppression.started_by, self.user)
        self.assertIsNotNone(suppression.started_at)

    def test_stopping_clears_who_and_when(self):
        EmailSuppression.start(user=self.user)

        suppression = EmailSuppression.stop()

        self.assertFalse(suppression.is_active)
        self.assertIsNone(suppression.started_by)
        self.assertIsNone(suppression.started_at)

    def test_the_state_is_shared_rather_than_per_process(self):
        # Same shared-cache reasoning as Maintenance's own equivalent test.
        EmailSuppression.start()

        self.assertTrue(cache.get(EmailSuppression.CACHE_KEY).is_active)
        self.assertTrue(EmailSuppression.is_on())

        EmailSuppression.stop()

        self.assertFalse(cache.get(EmailSuppression.CACHE_KEY).is_active)


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


class JobRunTests(TestCase):
    """features.commands.ScheduledJobCommand's own JobRun bookkeeping (replaces what used to
    be Celery signal wiring in features/signals.py -- see that module's own docstring),
    exercised against a real registered command (archive_overdue_clubs, same as
    MaintenanceCommandTests above -- no complex setup, "nothing overdue" is a normal, cheap
    result) rather than a throwaway one: what matters here is that ScheduledJobCommand.execute()
    writes a JobRun row correctly, not the domain logic of any one scheduled job (each of
    those has its own tests alongside its own management command)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_a_successful_run_is_recorded(self):
        call_command("archive_overdue_clubs", stdout=StringIO())

        run = JobRun.objects.get(name="billing.tasks.archive_overdue_clubs")
        self.assertEqual(run.status, JobRun.Status.SUCCESS)
        self.assertIn("Nothing overdue", run.detail)
        self.assertEqual(run.error, "")
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)

    def test_a_failed_run_is_recorded(self):
        # A disabled job is a failure ScheduledJobCommand.execute() itself raises (see
        # features/commands.py) -- convenient, deterministic way to exercise the FAILURE
        # branch of its own bookkeeping without contriving a real domain-logic failure.
        JobToggle.set_enabled("billing.tasks.archive_overdue_clubs", False)

        with self.assertRaises(CommandError):
            call_command("archive_overdue_clubs", stdout=StringIO())

        run = JobRun.objects.get(name="billing.tasks.archive_overdue_clubs")
        self.assertEqual(run.status, JobRun.Status.FAILURE)
        self.assertIn("disabled", run.error)

    def test_detailed_logging_logs_every_query(self):
        # --detailed-logging is what the control panel's "Run now" button always passes
        # (see controlpanel.views.JobRunNowView) -- features.scheduler's own ticks never
        # do, on purpose, since every query on every scheduled run would drown the log.
        with self.assertLogs("django.db.backends", level="DEBUG") as captured:
            call_command("archive_overdue_clubs", "--detailed-logging", stdout=StringIO())

        self.assertTrue(any("SELECT" in message for message in captured.output))
        # Restored afterwards, not left on for whatever else shares this connection/process.
        self.assertFalse(connection.force_debug_cursor)

    def test_detailed_logging_is_off_by_default(self):
        call_command("archive_overdue_clubs", stdout=StringIO())

        self.assertFalse(connection.force_debug_cursor)


class JobToggleTests(TestCase):
    """Per-job pause/resume switch -- see features.models.JobToggle and its use in
    events/billing/club's scheduled tasks (each checks JobToggle.is_enabled(name)
    right alongside the existing Maintenance.is_on() gate)."""

    def setUp(self):
        # Same reasoning as MaintenanceModeTests -- the toggle's cache isn't
        # rolled back with the test transaction.
        cache.clear()
        self.addCleanup(cache.clear)

    def test_a_job_with_no_row_is_enabled_by_default(self):
        self.assertTrue(JobToggle.is_enabled("events.tasks.extend_event_series"))

    def test_disabling_a_job(self):
        JobToggle.set_enabled("events.tasks.extend_event_series", False)

        self.assertFalse(JobToggle.is_enabled("events.tasks.extend_event_series"))

    def test_re_enabling_a_job(self):
        JobToggle.set_enabled("events.tasks.extend_event_series", False)
        JobToggle.set_enabled("events.tasks.extend_event_series", True)

        self.assertTrue(JobToggle.is_enabled("events.tasks.extend_event_series"))

    def test_toggling_one_job_does_not_affect_another(self):
        JobToggle.set_enabled("events.tasks.extend_event_series", False)

        self.assertTrue(JobToggle.is_enabled("events.tasks.send_deadline_reminders"))


class CeleryTaskTests(TestCase):
    """The platform jobs run as Celery tasks (beat triggers them, a worker runs them). These check the
    wiring, not Celery's own scheduling: every task is registered, and install_tasks schedules
    exactly the tasks that exist."""

    def registered(self):
        from rosterchief.celery import app

        app.loader.import_default_modules()
        # Tasks made with api.core.jobs.job_task are started by API requests, not scheduled, so they aren't scheduler jobs (api/core/test_jobs.py defines some).
        job_tasks = {name for name, task in app.tasks.items() if hasattr(task, "job_function")}
        return {name for name in app.tasks if not name.startswith("celery.") and name != "rosterchief.celery.debug_task" and name not in job_tasks}

    def test_every_registry_job_has_exactly_one_registered_task(self):
        self.assertEqual({meta["task"] for meta in JOB_REGISTRY.values()}, self.registered())

    def test_every_task_runs_its_registry_command_with_its_args(self):
        from rosterchief.celery import app

        self.registered()
        for key, meta in JOB_REGISTRY.items():
            with mock.patch("features.celery_support.call_command", return_value="") as call_command_mock:
                app.tasks[meta["task"]]()

            call_command_mock.assert_called_once_with(meta["command"], *meta["args"], **{})
            self.assertEqual(key.split(".")[-1], meta["command"].replace("notify_published_news", "notify_news_published"), key)

    def test_install_tasks_is_idempotent(self):
        from django_celery_beat.models import PeriodicTask

        call_command("install_tasks", stdout=StringIO())
        call_command("install_tasks", stdout=StringIO())

        self.assertEqual(PeriodicTask.objects.filter(task__in=[meta["task"] for meta in JOB_REGISTRY.values()]).count(), len(JOB_REGISTRY))

    def test_install_tasks_never_overwrites_an_edited_schedule(self):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        call_command("install_tasks", stdout=StringIO())
        task = PeriodicTask.objects.get(task="events.extend-event-series")
        task.crontab = None
        task.interval = IntervalSchedule.objects.get_or_create(every=7, period=IntervalSchedule.MINUTES)[0]
        task.save()

        call_command("install_tasks", stdout=StringIO())

        task.refresh_from_db()
        self.assertEqual(task.interval.every, 7)


class RunJobTests(TestCase):
    """features.celery_support.run_job is what every Celery task calls: the same call_command path
    as the control panel's Run now, so a failure must propagate for Celery to record FAILURE."""

    def test_runs_the_command_with_its_args_and_reports_success(self):
        from .celery_support import run_job

        with mock.patch("features.celery_support.call_command", return_value="done") as call_command_mock:
            result = run_job("send_billing_reminders", "--commit")

        call_command_mock.assert_called_once_with("send_billing_reminders", "--commit")
        self.assertEqual(result, {"status": "SUCCESS", "result": "done"})

    def test_a_command_error_propagates(self):
        from .celery_support import run_job

        with mock.patch("features.celery_support.call_command", side_effect=CommandError("maintenance")):
            with self.assertRaises(CommandError):
                run_job("extend_event_series")


class FlagPerUserGrantScopingTests(TestCase):
    """A per-user (or per-group) waffle grant carries no club, so on a club host
    it must not switch a feature on -- otherwise it would leak into every club
    that user belongs to."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import Group

        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        cls.user = get_user_model().objects.create_user(email="beta@example.com", password="pw-secret-123")
        cls.group = Group.objects.create(name="beta")
        cls.flag = get_waffle_flag_model().objects.get_or_create(name="evaluations")[0]
        cls.flag.clubs.add(cls.club)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def request_for(self, club):
        request = RequestFactory().get("/")
        request.club = club
        request.user = self.user
        return request

    def test_a_per_user_grant_does_not_enable_the_feature_in_an_unlicensed_club(self):
        self.flag.users.add(self.user)

        self.assertTrue(flag_is_active(self.request_for(self.club), "evaluations"))
        self.assertFalse(flag_is_active(self.request_for(self.other), "evaluations"))

    def test_a_per_group_grant_does_not_enable_the_feature_in_an_unlicensed_club(self):
        self.flag.groups.add(self.group)
        self.user.groups.add(self.group)

        self.assertFalse(flag_is_active(self.request_for(self.other), "evaluations"))

    def test_a_per_user_grant_still_applies_off_any_club(self):
        self.flag.users.add(self.user)

        self.assertTrue(flag_is_active(self.request_for(None), "evaluations"))


class GunicornConfigTests(TestCase):
    """gunicorn.conf.py: no master-side hook may start a thread. A master-side hook that starts a thread
    makes every worker fork a gamble -- a lock the thread holds at that instant (the DNS resolver's,
    a Redis pool's) is copied locked into the child, which then hangs forever on first use. That cost
    a production worker (every thread stuck in getaddrinfo, ~half of all requests hanging)."""

    def load_config(self):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location("gunicorn_conf_hooks_under_test", Path(__file__).resolve().parent.parent / "gunicorn.conf.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_no_hook_runs_code_in_the_master_that_could_start_a_thread(self):
        config = self.load_config()

        # when_ready fires in the master BEFORE the workers are forked; the others run in the master too.
        for master_hook in ("when_ready", "on_starting", "on_reload", "on_exit", "pre_fork"):
            self.assertFalse(hasattr(config, master_hook), f"gunicorn.conf.py defines {master_hook}: it runs in the process that forks the workers")


class GunicornPostForkTests(TestCase):
    """gunicorn.conf.py's post_fork: a worker must not inherit the master's cache clients (the
    master's scheduler thread may hold the Redis pool's lock when the worker is forked, which
    wedges every Redis call in that worker with ChildDeadlockedError)."""

    def load_config(self):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", Path(__file__).resolve().parent.parent / "gunicorn.conf.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_post_fork_replaces_the_inherited_cache_clients(self):
        from django.core.cache import caches

        inherited = caches["default"]

        self.load_config().post_fork(server=None, worker=None)

        self.assertIsNot(caches["default"], inherited)

    def test_post_fork_drops_the_inherited_redis_pools(self):
        from django_redis.pool import ConnectionFactory

        ConnectionFactory._pools["inherited-from-master"] = object()
        self.addCleanup(ConnectionFactory._pools.pop, "inherited-from-master", None)

        self.load_config().post_fork(server=None, worker=None)

        self.assertNotIn("inherited-from-master", ConnectionFactory._pools)

    def test_the_cache_still_works_after_post_fork(self):
        from django.core.cache import cache

        self.load_config().post_fork(server=None, worker=None)

        cache.set("post-fork-check", "ok", 5)
        self.assertEqual(cache.get("post-fork-check"), "ok")
