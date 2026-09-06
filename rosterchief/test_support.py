"""Shared, read-only test fixtures duplicated byte-for-byte across enough
apps' test files (club, news, notifications, authentication, controlpanel,
management, registration, mobile) that a shared import beats yet another
copy. Not test *infrastructure* -- that's rosterchief/test_runner.py -- just
the handful of fixture builders every one of those files happened to
reinvent identically.

A test that needs different behaviour (e.g. a season that genuinely covers
*today*, not a fixed calendar year -- see management/tests.py's own
make_season) should keep building its own; these three are the ones that
turned out not to need that.
"""

import datetime

from allauth.mfa.models import Authenticator

from club.models import Club, Season


def make_club(**kwargs):
    """A minimally-viable club -- name/slug only. Pass kwargs to override or
    add fields a specific test needs set up front."""
    defaults = {"name": "Ajax United", "slug": "ajax-united"}
    defaults.update(kwargs)
    return Club.objects.create(**defaults)


def make_season(club, start_year=2026):
    """A fixed academic-year season (1 August ``start_year`` to 31 May the
    year after) -- deterministic, so a test can assert against known
    calendar dates instead of whatever "today" happens to be when the suite
    runs."""
    return Season.objects.create(club=club, start_date=datetime.date(start_year, 8, 1), end_date=datetime.date(start_year + 1, 5, 31))


def enrol_mfa(user):
    """Give ``user`` a second factor (enough for is_mfa_enabled) --
    RequireMFAMiddleware otherwise redirects any staff/elevated account
    straight to enrolment."""
    return Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})
