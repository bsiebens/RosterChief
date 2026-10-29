import datetime
from unittest import mock

from api.panel.tests.base_slice import SliceTestCase
from club.models import ClubRole
from management.pdf import PDFExportError
from teams.models import NumberPool, NumberReservation, Team, TeamMembership


class NumbersTests(SliceTestCase):
    def setUp(self):
        super().setUp()
        self.pool = NumberPool.objects.create(club=self.club, name="Youth", min_number=1, max_number=5)
        self.team = Team.objects.create(club=self.club, name="U12", pool=self.pool)

    def tiles(self, token, query=""):
        response = self.call("get", "/numbers" + query, token)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_no_token_is_401(self):
        for method, path, data in (("get", "/manage/numbers", None), ("get", "/manage/numbers/export", None), ("post", "/manage/numbers/reservations", {"pool_id": str(self.pool.pk), "number": 2})):
            self.assertError(self.api(method, path, host="ajax-united.rosterchief.app", data=data), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        for method, path, data in (("get", "/numbers", None), ("get", "/numbers/export", None), ("post", "/numbers/reservations", {"pool_id": str(self.pool.pk), "number": 2})):
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")

    def test_other_clubs_pool_season_and_reservation_are_404(self):
        other, other_season, _stranger = self.other_club()
        other_pool = NumberPool.objects.create(club=other, name="Away", min_number=1, max_number=9)
        reservation = NumberReservation.objects.create(club=other, pool=other_pool, number=3)
        token = self.admin()
        self.assertError(self.call("get", f"/numbers?pool_id={other_pool.pk}", token), 404, "not_found")
        self.assertError(self.call("get", f"/numbers?season_id={other_season.pk}", token), 404, "not_found")
        self.assertError(self.call("get", f"/numbers/export?pool_id={other_pool.pk}", token), 404, "not_found")
        self.assertError(self.call("post", "/numbers/reservations", token, data={"pool_id": str(other_pool.pk), "number": 2}), 404, "not_found")
        self.assertError(self.call("delete", f"/numbers/reservations/{reservation.pk}", token), 404, "not_found")
        self.assertTrue(NumberReservation.objects.filter(pk=reservation.pk).exists())

    def test_overview_states(self):
        TeamMembership.objects.create(team=self.team, member=self.child, season=self.season, jersey_number=2)
        NumberReservation.objects.create(club=self.club, pool=self.pool, number=4, note="Retired", reserved_by=self.user)
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        body = self.tiles(token)
        self.assertEqual(body["pool"]["name"], "Youth")
        self.assertEqual(body["season"]["id"], str(self.season.pk))
        states = {tile["number"]: tile["state"] for tile in body["tiles"]}
        self.assertEqual(states, {1: "available", 2: "taken", 3: "available", 4: "reserved", 5: "available"})
        self.assertEqual(body["state_counts"]["available"], 3)
        self.assertEqual(body["state_counts"]["conflict"], 0)
        taken = body["tiles"][1]
        self.assertEqual(taken["holders"][0]["name"], str(self.child))
        self.assertEqual(taken["holders"][0]["team"], "U12")
        reserved = body["tiles"][3]
        self.assertEqual((reserved["note"], reserved["reservation"]["can_release"]), ("Retired", True))
        self.assertEqual(len(body["seasons"]), 1)

    def test_previous_season_number_is_flagged(self):
        previous = type(self.season).objects.create(club=self.club, start_date=self.season.start_date - datetime.timedelta(days=400), end_date=self.season.start_date - datetime.timedelta(days=35))
        TeamMembership.objects.create(team=self.team, member=self.child, season=previous, jersey_number=3)
        token = self.admin()
        self.assertEqual(self.tiles(token)["tiles"][2]["state"], "previous")
        self.assertEqual(self.tiles(token, f"?season_id={previous.pk}")["tiles"][2]["state"], "taken")

    def test_no_pool_gives_an_empty_overview(self):
        self.pool.delete()
        body = self.tiles(self.admin())
        self.assertEqual((body["pool"], body["tiles"], body["pools"]), (None, [], []))

    def test_reserve_and_release(self):
        token = self.as_role(ClubRole.Roles.EDITOR)
        created = self.call("post", "/numbers/reservations", token, data={"pool_id": str(self.pool.pk), "number": 3, "note": "Retired"})
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual((body["number"], body["note"], body["can_release"]), (3, "Retired", True))
        reservation = NumberReservation.objects.get(pk=body["id"])
        self.assertEqual((reservation.club, reservation.reserved_by), (self.club, self.user))
        self.assertEqual(self.call("delete", f"/numbers/reservations/{body['id']}", token).status_code, 204)
        self.assertFalse(NumberReservation.objects.exists())

    def test_reserve_validation(self):
        NumberReservation.objects.create(club=self.club, pool=self.pool, number=2)
        token = self.admin()
        outside = self.call("post", "/numbers/reservations", token, data={"pool_id": str(self.pool.pk), "number": 9})
        self.assertError(outside, 422, "validation_error")
        self.assertIn("number", outside.json()["error"]["fields"])
        self.assertError(self.call("post", "/numbers/reservations", token, data={"pool_id": str(self.pool.pk), "number": 2}), 409, "already_reserved")
        self.assertError(self.call("post", "/numbers/reservations", token, data={"pool_id": str(self.pool.pk)}), 422, "validation_error")
        self.assertError(self.call("post", "/numbers/reservations", token, data={"pool_id": str(self.pool.pk), "number": 3, "note": "x" * 300}), 422, "validation_error")

    def test_reserve_is_idempotent(self):
        token = self.admin()
        data = {"pool_id": str(self.pool.pk), "number": 3}
        first = self.call("post", "/numbers/reservations", token, data=data, HTTP_IDEMPOTENCY_KEY="r-1")
        second = self.call("post", "/numbers/reservations", token, data=data, HTTP_IDEMPOTENCY_KEY="r-1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_release_of_someone_elses_reservation(self):
        reservation = NumberReservation.objects.create(club=self.club, pool=self.pool, number=3)
        token = self.as_role(ClubRole.Roles.EDITOR)
        self.assertError(self.call("delete", f"/numbers/reservations/{reservation.pk}", token), 403, "forbidden")
        self.assertTrue(NumberReservation.objects.filter(pk=reservation.pk).exists())
        ClubRole.objects.filter(club=self.club, member=self.member).update(role=ClubRole.Roles.MEMBER_ADMIN)
        self.assertEqual(self.call("delete", f"/numbers/reservations/{reservation.pk}", token).status_code, 204)

    def test_export_pdf(self):
        token = self.admin()
        with mock.patch("api.panel.routers.numbers.number_list_pdf", return_value=b"%PDF-1.7 test") as render:
            response = self.call("get", "/numbers/export", token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("numbers-youth-", response["Content-Disposition"])
        self.assertEqual(response.content, b"%PDF-1.7 test")
        self.assertEqual(render.call_args.args[0]["pool"], self.pool)

    def test_export_errors(self):
        token = self.admin()
        with mock.patch("api.panel.routers.numbers.number_list_pdf", side_effect=PDFExportError("no weasyprint")):
            self.assertError(self.call("get", "/numbers/export", token), 503, "pdf_unavailable")
        self.pool.delete()
        self.assertError(self.call("get", "/numbers/export", token), 409, "no_selection")
