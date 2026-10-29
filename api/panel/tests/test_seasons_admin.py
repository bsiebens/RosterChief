import datetime

from api.panel.tests.base_slice import SliceTestCase
from club.models import ClubMembership, ClubRole, Season
from members.models import Member

D = datetime.date


class SeasonsAdminTests(SliceTestCase):
    def payload(self, **extra):
        year = self.season.end_date.year + 2
        return {"start_date": f"{year}-07-01", "end_date": f"{year + 1}-06-30", **extra}

    def test_no_token_is_401(self):
        self.assertError(self.api("post", "/manage/seasons", host="ajax-united.rosterchief.app", data=self.payload()), 401, "unauthenticated")
        self.assertError(self.api("delete", f"/manage/seasons/{self.season.pk}", host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_only_club_admins_write(self):
        for token in (self.login(), None):
            if token is None:
                token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
            self.assertError(self.call("post", "/seasons", token, data=self.payload()), 403, "forbidden")
            self.assertError(self.call("patch", f"/seasons/{self.season.pk}", token, data={"end_date": "2099-01-01"}), 403, "forbidden")
            self.assertError(self.call("delete", f"/seasons/{self.season.pk}", token), 403, "forbidden")
        self.assertEqual(Season.objects.count(), 1)

    def test_reads_stay_open_to_staff(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        self.assertEqual(self.call("get", "/seasons", token).status_code, 200)

    def test_other_clubs_season_is_404(self):
        _other, season, _stranger = self.other_club()
        token = self.admin()
        self.assertError(self.call("patch", f"/seasons/{season.pk}", token, data={"end_date": "2099-01-01"}), 404, "not_found")
        self.assertError(self.call("delete", f"/seasons/{season.pk}", token), 404, "not_found")
        self.assertTrue(Season.objects.filter(pk=season.pk).exists())

    def test_create_edit_delete(self):
        token = self.admin()
        created = self.call("post", "/seasons", token, data=self.payload())
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertFalse(body["is_current"])
        self.assertEqual(Season.objects.filter(club=self.club).count(), 2)
        listed = self.call("get", "/seasons", token).json()["items"]
        self.assertEqual(len(listed), 2)
        edited = self.call("patch", f"/seasons/{body['id']}", token, data={"end_date": f"{body['end_date'][:4]}-07-31"})
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(edited.json()["end_date"], f"{body['end_date'][:4]}-07-31")
        self.assertEqual(edited.json()["start_date"], body["start_date"])
        # A season that follows another inherits its guardians, which then pin it (see test_delete_in_use_is_409).
        self.assertError(self.call("delete", f"/seasons/{body['id']}", token), 409, "season_in_use")
        year = self.season.start_date.year - 5
        earlier = self.call("post", "/seasons", token, data={"start_date": f"{year}-07-01", "end_date": f"{year + 1}-06-30"}).json()
        self.assertEqual(self.call("delete", f"/seasons/{earlier['id']}", token).status_code, 204)
        self.assertEqual(Season.objects.filter(club=self.club).count(), 2)

    def test_create_carries_guardians_forward(self):
        token = self.admin()
        created = self.call("post", "/seasons", token, data=self.payload())
        self.assertEqual(created.status_code, 201)
        # Only a season directly following another inherits; this one follows self.season.
        later = Season.objects.get(pk=created.json()["id"])
        self.assertTrue(ClubMembership.objects.filter(season=later, member=self.member, kind=ClubMembership.Kind.GUARDIAN).exists())

    def test_validation_422(self):
        token = self.admin()
        backwards = self.call("post", "/seasons", token, data={"start_date": "2090-07-01", "end_date": "2090-06-30"})
        self.assertError(backwards, 422, "validation_error")
        self.assertIn("end_date", backwards.json()["error"]["fields"])
        overlap = self.call("post", "/seasons", token, data={"start_date": self.season.end_date.isoformat(), "end_date": "2099-01-01"})
        self.assertError(overlap, 422, "validation_error")
        self.assertIn("start_date", overlap.json()["error"]["fields"])
        self.assertError(self.call("post", "/seasons", token, data={"start_date": "nope"}), 422, "validation_error")
        later = Season.objects.create(club=self.club, start_date=self.season.end_date + datetime.timedelta(days=1), end_date=self.season.end_date + datetime.timedelta(days=100))
        clash = self.call("patch", f"/seasons/{later.pk}", token, data={"start_date": self.season.start_date.isoformat()})
        self.assertError(clash, 422, "validation_error")
        self.assertEqual(Season.objects.filter(club=self.club).count(), 2)

    def test_delete_in_use_is_409(self):
        token = self.admin()
        self.assertError(self.call("delete", f"/seasons/{self.season.pk}", token), 409, "season_in_use")
        self.assertTrue(Season.objects.filter(pk=self.season.pk).exists())
        self.assertTrue(Member.objects.exists())

    def test_create_is_idempotent(self):
        token = self.admin()
        first = self.call("post", "/seasons", token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="s-1")
        second = self.call("post", "/seasons", token, data=self.payload(), HTTP_IDEMPOTENCY_KEY="s-1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Season.objects.filter(club=self.club).count(), 2)
