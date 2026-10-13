"""Test-only runtime configuration.

Most of what's in here makes the suite faster at the cost of something production
needs (real password hashing, templates that pick up edits without a restart, error
logs); MEDIA_ROOT is the one exception -- redirected to a fresh tempdir per run so
tests that write real files (cached invoice PDFs) never touch the project's actual
media/ directory or leak a cached file from one test into another. It lives in the
test runner rather than in ``settings.py`` on purpose: a runner is
instantiated *only* by ``manage.py test``, so there is no environment variable to get
wrong, no ``DEBUG`` to mis-set, and no import path by which a deployed process could
ever pick these values up. Wiring is a single ``TEST_RUNNER`` line in settings.py.

The tweaks are applied in ``setup_test_environment()``, before the suite is built and
before any database or template engine is created.

Running the suite
-----------------
``uv run python manage.py test`` runs in parallel by default (one worker per core --
see ``RosterChiefTestRunner.add_arguments`` below), because at the suite's current size
that's a real wall-clock win: ~38s serial vs. ~25s parallel on a 10-core machine, even
though every worker re-runs the whole migration set against its own database clone (the
fixed cost Django's own ``--parallel`` docs warn about). That math flips as the suite
keeps growing -- worth re-measuring (`time manage.py test` vs. `time manage.py test
--parallel=1`) if it ever stops paying off, rather than trusting these numbers forever.

Pass ``--parallel=1`` to force serial output -- e.g. reproducing a failure with a clean,
non-interleaved traceback, or ``--pdb`` (incompatible with real parallelism).

``--shard=I/N`` runs only the I-th of N slices of the suite (1-based), split by test
class so a class's ``setUpTestData`` still runs once. CI uses it to spread the suite
over several jobs; every class lands in exactly one slice, decided by a stable hash of
its dotted name, so the N slices together are the whole suite.

``--fast`` skips the tests listed in ``fast_suite_skip.txt`` (next to this file): the ones whose
every covered line is also covered by another test, found by ``deploy/refresh_fast_suite.py``
from per-test coverage. Roughly a quarter of the suite then reproduces the whole suite's line
coverage in about a third of the time -- right for a quick local check and for feature-branch CI.
It is a *quick* tier, not a replacement: line coverage says the code ran, not that every
behaviour was asserted, so releases, ``main`` and the nightly run still run everything. Any test
not in the file (every new test) runs in the fast tier too, so nothing new is ever skipped
until the list is regenerated.

``--keepdb`` does nothing for local runs: the sqlite test database is in-memory and
cannot survive the process. It only pays off against a real PostgreSQL
``DJANGO_DATABASE_URL``, where it skips the migrate step between runs.
"""

import logging
import tempfile
import unittest
import zlib
from pathlib import Path

from django.conf import settings
from django.test.runner import DiscoverRunner, ParallelTestSuite, iter_test_cases
from django.test.signals import setting_changed

#: Django's default hasher is PBKDF2 with ~1.2M iterations -- deliberately slow, which is
#: exactly right in production and ruinous in a suite that creates hundreds of users and
#: logs them in again in every other test. MD5 is worthless as a password hash and that is
#: fine here: the stored hashes live in a throwaway test database for a few seconds, and
#: nothing in the suite asserts anything about hash strength. This value is unreachable
#: outside the test runner -- see the module docstring.
TEST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _apply_per_process_test_overrides(*args):
    """Two settings that need to be forced back to a test-safe value inside
    *every* process the suite runs in, not just the main one -- see
    ``RosterChiefParallelTestSuite`` below for why that distinction matters.

    ``default_storage`` is pointed at a fresh, private tempdir instead of the
    project's real ``media/`` -- shop.services.invoices.render_invoice_pdf /
    club.services.invoicing.invoice_pdf cache their rendered PDF there, keyed
    by the invoice's own pk. Without this, a test run writes real files into
    the project's actual media directory, and a cached file from one test
    would silently survive into the next (disk writes aren't rolled back the
    way the per-test DB transaction is). It's a lazy wrapper that only
    re-reads MEDIA_ROOT when its cache is invalidated, which is what
    actually honours a plain ``override_settings(MEDIA_ROOT=...)`` too --
    setting the value alone is not enough, the signal is what forces the
    rebuild.

    ``VAPID_PRIVATE_KEY`` is forced empty regardless of whatever a
    developer's own ``.env`` has configured for the real "Enable push
    notifications" button -- mobile.services.push.send_push_to_member treats
    an empty key as "push isn't set up, no-op" (see its own comment), which
    is what practically every test that creates a Notification is silently
    relying on. A real key turns that into a genuine outbound webpush() call
    on every one of them -- at best pointless network I/O, at worst a hang
    or a flood of failures against a real push service, and it was a real
    key in a developer's own .env that surfaced exactly that the first time
    this override didn't exist yet.

    ``*args`` because ``ParallelTestSuite.process_setup`` is invoked as a
    plain function (its own ``__func__``, unbound from any instance -- see
    ``_init_worker`` in django/test/runner.py) with whatever
    ``process_setup_args`` was set to; called with none here, but the
    signature still needs to accept them.
    """
    settings.MEDIA_ROOT = tempfile.mkdtemp(prefix="rosterchief-test-media-")
    # rosterchief.storage.invoice_cache_storage reads this on every access, so the
    # cached invoice PDFs land in a throwaway directory too, never the real one.
    settings.PRIVATE_MEDIA_ROOT = tempfile.mkdtemp(prefix="rosterchief-test-private-media-")
    setting_changed.send(sender=None, setting="STORAGES", value=settings.STORAGES, enter=True)
    settings.VAPID_PRIVATE_KEY = ""
    # Notifications are dispatched (native push, web push, email) on the spot instead of on
    # a thread after commit (notifications.dispatch.schedule_notifications).
    settings.NOTIFICATIONS_DISPATCH_EAGER = True
    _clear_caches_before_every_test()


def _clear_caches_before_every_test():
    """Every test starts with an empty cache.

    The test cache (LocMemCache) lives for the whole process, while the database is rolled
    back after each test. So anything cached from the database -- a waffle feature flag, a
    sponsors list, the maintenance switch -- outlived the data it was read from, and a test's
    result then depended on which test happened to run before it (run the suite in reverse and
    eight tests failed; a test that "covered" the officials code did so only because an earlier
    test had left the flag cached on). Clearing it in Django's own per-test setup makes every test
    start from a known state, in the main process and in every parallel worker alike. Idempotent:
    the override can be applied more than once per process.
    """
    from django.core.cache import caches
    from django.test import SimpleTestCase

    original = SimpleTestCase.__dict__["_pre_setup"].__func__  # a classmethod: keep the plain function
    if getattr(original, "clears_caches", False):
        return

    @classmethod
    def _pre_setup(cls):
        original(cls)
        for cache in caches.all():
            cache.clear()

    _pre_setup.__func__.clears_caches = True
    SimpleTestCase._pre_setup = _pre_setup


class RosterChiefParallelTestSuite(ParallelTestSuite):
    """Runs ``_apply_per_process_test_overrides`` inside every spawned worker
    process too -- ``RosterChiefTestRunner.setup_test_environment`` only ever
    runs once, in the main process, before workers are spawned. On macOS/
    Windows (multiprocessing's ``spawn`` start method), each worker is a
    fresh interpreter that re-reads settings.py from scratch and never
    inherits that one-time override, so without this, parallel runs quietly
    leaked cached PDFs into the real media/ directory (and, pre-VAPID-override,
    would have sent real push notifications) while a serial run looked
    perfectly clean."""

    process_setup = _apply_per_process_test_overrides


def _cached_template_loaders(templates):
    """Return ``TEMPLATES`` with the Django backend wrapped in the cached loader.

    Without it every ``render()`` re-reads and re-parses the template files from disk;
    the suite renders the same management/controlpanel pages hundreds of times. The
    cached loader is what ``APP_DIRS`` turns on automatically when ``DEBUG`` is False,
    but the test runner forces ``DEBUG`` off *after* settings are read, so we have to
    spell it out. ``loaders`` and ``APP_DIRS`` are mutually exclusive, hence the swap.
    """
    patched = []
    for engine in templates:
        if engine["BACKEND"] != "django.template.backends.django.DjangoTemplates" or "loaders" in engine.get("OPTIONS", {}):
            patched.append(engine)
            continue

        engine = {**engine, "OPTIONS": {**engine.get("OPTIONS", {})}}
        engine.pop("APP_DIRS", None)
        engine["OPTIONS"]["loaders"] = [
            (
                "django.template.loaders.cached.Loader",
                [
                    "django.template.loaders.filesystem.Loader",
                    "django.template.loaders.app_directories.Loader",
                ],
            )
        ]
        patched.append(engine)
    return patched


FAST_SUITE_SKIP_FILE = Path(__file__).with_name("fast_suite_skip.txt")


def read_fast_suite_skip(path=FAST_SUITE_SKIP_FILE) -> frozenset:
    """The dotted ids of the tests ``--fast`` skips: one per line, ``#`` comments and blank lines
    ignored. A missing file means nothing is skipped (``--fast`` then runs everything)."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return frozenset()
    return frozenset(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))


class RosterChiefTestRunner(DiscoverRunner):
    """The project's test runner. See the module docstring for what it changes and why."""

    parallel_test_suite = RosterChiefParallelTestSuite

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        # Django's own default is 0 (serial) -- reach into the parser to flip it to
        # "auto" (one worker per core) instead, so a plain `manage.py test` with no
        # flags gets the parallel win by default. `--parallel=1` on the command line
        # still overrides this back to serial.
        for action in parser._actions:
            if action.dest == "parallel":
                action.default = "auto"
                break
        parser.add_argument("--shard", metavar="I/N", help="Run only slice I of N (1-based), split by test class. Used by CI to spread the suite over jobs.")
        parser.add_argument("--fast", action="store_true", help="Skip the tests that add no coverage of their own (rosterchief/fast_suite_skip.txt): a quick tier for local checks and feature-branch CI. Releases, main and the nightly run everything.")

    def __init__(self, *args, shard=None, fast=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.skip = read_fast_suite_skip() if fast else frozenset()
        self.shard = None
        if shard:
            index, _sep, total = shard.partition("/")
            index, total = int(index), int(total)
            if not 1 <= index <= total:
                raise ValueError(f"--shard must be I/N with 1 <= I <= N, got {shard!r}")
            self.shard = (index - 1, total)

    def load_tests_for_label(self, label, discover_kwargs):
        tests = super().load_tests_for_label(label, discover_kwargs)
        if self.shard is None and not self.skip:
            return tests
        # Load failures (_FailedTest) are always kept -- and, for a shard, kept in every slice --
        # so a broken module fails the run instead of silently vanishing from it.
        kept = [test for test in iter_test_cases(tests) if isinstance(test, unittest.loader._FailedTest) or test.id() not in self.skip]
        if self.shard is None:
            return unittest.TestSuite(kept)
        index, total = self.shard
        # Filtered per class, before Django partitions the suite over --parallel workers.
        return unittest.TestSuite(test for test in kept if isinstance(test, unittest.loader._FailedTest) or zlib.crc32(f"{type(test).__module__}.{type(test).__qualname__}".encode()) % total == index)

    def setup_test_environment(self, **kwargs):
        settings.PASSWORD_HASHERS = TEST_PASSWORD_HASHERS

        # Covers the serial/main-process case; RosterChiefParallelTestSuite
        # (above) covers each spawned worker under --parallel, which never
        # runs this method at all -- see its own docstring.
        _apply_per_process_test_overrides()

        settings.TEMPLATES = _cached_template_loaders(settings.TEMPLATES)
        # The template engines are built lazily and cached; this is the same signal
        # ``override_settings`` fires to make Django rebuild them.
        setting_changed.send(sender=self.__class__, setting="TEMPLATES", value=settings.TEMPLATES, enter=True)

        # Tests that assert on a 4xx/5xx response deliberately provoke the error, and
        # django.request dutifully logs each one ("Service Unavailable: /healthz"),
        # burying the actual test output. No test asserts on these records. Silenced on
        # the live logger rather than via LOGGING, which was already applied at startup.
        logging.getLogger("django.request").setLevel(logging.CRITICAL + 1)
        # Every rendered PDF logs WeasyPrint's seven progress steps and a few dozen lines
        # of fontTools subsetting at INFO, plus a "Using fontTools instead of HarfBuzz"
        # WARNING -- tens of thousands of lines per CI run that no test looks at. Errors
        # still come through.
        for noisy in ("weasyprint", "fontTools"):
            logging.getLogger(noisy).setLevel(logging.ERROR)

        super().setup_test_environment(**kwargs)
