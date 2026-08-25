"""Test-only runtime configuration.

Everything in here makes the suite faster at the cost of something production needs
(real password hashing, templates that pick up edits without a restart, error logs).
It lives in the test runner rather than in ``settings.py`` on purpose: a runner is
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

``--keepdb`` does nothing for local runs: the sqlite test database is in-memory and
cannot survive the process. It only pays off against a real PostgreSQL
``DJANGO_DATABASE_URL``, where it skips the migrate step between runs.
"""

import logging

from django.conf import settings
from django.test.runner import DiscoverRunner
from django.test.signals import setting_changed

#: Django's default hasher is PBKDF2 with ~1.2M iterations -- deliberately slow, which is
#: exactly right in production and ruinous in a suite that creates hundreds of users and
#: logs them in again in every other test. MD5 is worthless as a password hash and that is
#: fine here: the stored hashes live in a throwaway test database for a few seconds, and
#: nothing in the suite asserts anything about hash strength. This value is unreachable
#: outside the test runner -- see the module docstring.
TEST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


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


class RosterChiefTestRunner(DiscoverRunner):
    """The project's test runner. See the module docstring for what it changes and why."""

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

    def setup_test_environment(self, **kwargs):
        settings.PASSWORD_HASHERS = TEST_PASSWORD_HASHERS

        settings.TEMPLATES = _cached_template_loaders(settings.TEMPLATES)
        # The template engines are built lazily and cached; this is the same signal
        # ``override_settings`` fires to make Django rebuild them.
        setting_changed.send(sender=self.__class__, setting="TEMPLATES", value=settings.TEMPLATES, enter=True)

        # Tests that assert on a 4xx/5xx response deliberately provoke the error, and
        # django.request dutifully logs each one ("Service Unavailable: /healthz"),
        # burying the actual test output. No test asserts on these records. Silenced on
        # the live logger rather than via LOGGING, which was already applied at startup.
        logging.getLogger("django.request").setLevel(logging.CRITICAL + 1)

        super().setup_test_environment(**kwargs)
