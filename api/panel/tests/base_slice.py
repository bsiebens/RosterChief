"""Shared helpers of the groups / positions / numbers / seasons API tests."""

import datetime

from api.protected.tests import CLUB_HOST, AppApiTestCase
from club.models import Club, ClubRole, Season
from members.models import Member


class SliceTestCase(AppApiTestCase):
    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def call(self, method, path, token, **kwargs):
        return self.api(method, "/manage" + path, token=token, host=CLUB_HOST, **kwargs)

    def other_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = datetime.date.today()
        season = Season.objects.create(club=other, start_date=today - datetime.timedelta(days=10), end_date=today + datetime.timedelta(days=200))
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger")
        return other, season, stranger
