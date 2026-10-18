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
from django.db.models.signals import post_save

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
    straight to enrolment. Idempotent: a user already enrolled (e.g. by
    start_auto_mfa_enrolment) keeps their existing authenticator."""
    return Authenticator.objects.get_or_create(user=user, type=Authenticator.Type.TOTP, defaults={"data": {"secret": "JBSWY3DPEHPK3PXP"}})[0]


def _enrol_newly_privileged_user(sender, instance, created, **kwargs):
    from allauth.mfa.utils import is_mfa_enabled

    from teams.models import StaffAssignment

    if not created:
        return
    if sender is StaffAssignment and not instance.position.management_position:
        return
    user = instance.member.user
    if user is not None and not is_mfa_enabled(user):
        enrol_mfa(user)


def _mfa_grant_senders():
    from club.models import EvaluationManager, RefereeManager, ShopManager
    from teams.models import StaffAssignment

    return (ShopManager, EvaluationManager, RefereeManager, StaffAssignment)


def start_auto_mfa_enrolment():
    """For test modules whose fixtures hand out ShopManager/EvaluationManager
    grants or management-position StaffAssignments by the dozen: enrol each
    such user's second factor as the grant is created, so RequireMFAMiddleware
    doesn't redirect every request those tests make. Call from the module's
    ``setUpModule`` and pair with ``stop_auto_mfa_enrolment`` in
    ``tearDownModule`` -- the enforcement itself is covered in
    authentication.tests, which doesn't use this."""
    for sender in _mfa_grant_senders():
        post_save.connect(_enrol_newly_privileged_user, sender=sender, dispatch_uid=f"test-auto-mfa-{sender.__name__}")


def stop_auto_mfa_enrolment():
    for sender in _mfa_grant_senders():
        post_save.disconnect(sender=sender, dispatch_uid=f"test-auto-mfa-{sender.__name__}")
