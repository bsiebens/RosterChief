"""Tests of the leftovers slice: the analytics PDF download, the registration payment code, per-club duplicate names
(now enforced by the web forms the routers call) and the spreadsheet upload handling."""

import datetime
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.utils import timezone

from api.panel.tests.test_team_extras import TeamExtrasTestCase, enable
from club.models import Club, ClubMembership
from club.services.invoice_rows import registration_invoice_status, registration_payment_code
from events.models import Event, GameTimeline
from events.services.game_stats import sync_game_stats
from management.pdf import PDFExportError
from members.models import Group
from rosterchief.test_support import make_club
from teams.models import NumberPool, Position, Team

TEAMS = "/manage/teams"
DOCUMENT = {
    "events": [
        {"id": "g1", "type": "goal", "period": "1", "seconds": 60, "side": "away", "goal": {"scorer": {"name": "Sam Scorer", "number": 78}, "assists": [], "strength": "PP1"}},
        {"id": "g2", "type": "goal", "period": "2", "seconds": 90, "side": "home", "goal": {"scorer": {"name": "Them Scorer", "number": 5}, "assists": []}},
    ]
}


class AnalyticsPdfTests(TeamExtrasTestCase):
    def setUp(self):
        super().setUp()
        Club.objects.filter(pk=self.club.pk).update(sport_type=Club.SportType.ICE_HOCKEY)
        self.club.refresh_from_db()
        enable("analytics", self.club)
        self.game = Event.objects.create(club=self.club, title="Game", kind=Event.EventKind.GAME, season=self.season, start=timezone.now() - datetime.timedelta(days=2), score_for=3, score_against=1)
        self.game.teams.add(self.team)
        GameTimeline.objects.create(event=self.game, source="test", data=DOCUMENT, status=GameTimeline.Status.FINAL)
        sync_game_stats(self.game, DOCUMENT)

    def url(self, team=None, query=""):
        return f"{TEAMS}/{(team or self.team).pk}/analytics/pdf{query}"

    def test_auth(self):
        self.assertError(self.call("get", self.url()), 401, "unauthenticated")
        self.assertError(self.call("get", self.url(), self.login()), 403, "forbidden")

    def test_download(self):
        with mock.patch("api.panel.routers.team_analytics_pdf.team_analytics_pdf", return_value=b"%PDF-fake") as render:
            response = self.call("get", self.url(), self.as_admin())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("analytics-u12-", response["Content-Disposition"])
        context = render.call_args.args[0]
        self.assertEqual((context["team"], context["scope"], context["analytics"]["games"]), (self.team, "all", 1))
        self.assertEqual(context["scope_label"], "All games")
        self.assertEqual(len(context["leaderboards"]), 4)

    def test_coach_of_the_team_may_download_and_filters_apply(self):
        with mock.patch("api.panel.routers.team_analytics_pdf.team_analytics_pdf", return_value=b"%PDF-fake") as render:
            response = self.call("get", self.url(query="?games=home"), self.as_coach())
        self.assertEqual(response.status_code, 409, response.content)  # no home game: nothing to export
        self.assertFalse(render.called)

    def test_no_games_is_409(self):
        self.game.delete()
        self.assertError(self.call("get", self.url(), self.as_admin()), 409, "no_games")

    def test_pdf_unavailable_is_503(self):
        with mock.patch("api.panel.routers.team_analytics_pdf.team_analytics_pdf", side_effect=PDFExportError("no weasyprint")):
            self.assertError(self.call("get", self.url(), self.as_admin()), 503, "pdf_unavailable")

    def test_flag_off_other_sport_and_other_clubs_are_404(self):
        token = self.as_admin()
        foreign = Team.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Foreign", short_name="F")
        self.assertError(self.call("get", self.url(foreign), token), 404, "not_found")
        self.assertError(self.call("get", self.url(query="?season_id=" + str(foreign.pk)), token), 404, "not_found")
        Club.objects.filter(pk=self.club.pk).update(sport_type="other")
        self.assertError(self.call("get", self.url(), token), 404, "not_found")


class RegistrationPaymentCodeTests(SimpleTestCase):
    def memberships(self, *statuses):
        return [SimpleNamespace(fee_status=status) for status in statuses]

    def test_codes_match_the_labels(self):
        Fee = ClubMembership.FeeStatus
        cases = [
            ((Fee.PAID, Fee.WAIVED), "paid", "Paid", "badge-success"),
            ((Fee.PARTIALLY_PAID,), "partial", "Partially paid", "badge-warning"),
            ((Fee.PAID, Fee.UNPAID), "partial", "Partially paid", "badge-warning"),
            ((Fee.UNPAID,), "unpaid", "Unpaid", "badge-error"),
        ]
        for statuses, code, label, css in cases:
            rows = self.memberships(*statuses)
            self.assertEqual(registration_payment_code(rows), code)
            self.assertEqual(tuple(map(str, registration_invoice_status(rows))), (label, css))


class DuplicateNameTests(TeamExtrasTestCase):
    """The forms now reject a duplicate name themselves; the routers keep answering 422 on `name`."""

    def admin(self):
        if not hasattr(self, "_admin_token"):
            self._admin_token = self.as_admin()
        return self._admin_token

    def assertNameTaken(self, response):
        self.assertEqual(response.status_code, 422, response.content)
        self.assertIn("name", response.json()["error"]["fields"])

    def test_position(self):
        token = self.admin()
        payload = {"name": "Forward", "short_name": "FW"}
        self.assertNameTaken(self.call("post", "/manage/positions", token, data=payload))
        other = Position.objects.create(club=self.club, name="Winger", short_name="W")
        self.assertNameTaken(self.call("patch", f"/manage/positions/{other.pk}", token, data={"name": "Forward"}))
        self.assertEqual(self.call("patch", f"/manage/positions/{other.pk}", token, data={"name": "Winger", "short_name": "WW"}).status_code, 200)

    def test_number_pool(self):
        token = self.admin()
        NumberPool.objects.create(club=self.club, name="Juniors", min_number=1, max_number=20)
        self.assertNameTaken(self.call("post", "/manage/number-pools", token, data={"name": "Juniors", "min_number": 1, "max_number": 9}))

    def test_group(self):
        token = self.admin()
        Group.objects.create(club=self.club, name="Coaches")
        self.assertNameTaken(self.call("post", "/manage/groups", token, data={"name": "Coaches"}))
        self.assertEqual(self.call("post", "/manage/groups", token, data={"name": "Refs"}).status_code, 201)

    def test_referee_level(self):
        token = self.admin()
        self.assertEqual(self.call("post", "/manage/referee-levels", token, data={"name": "L1", "team_ids": []}).status_code, 201)
        self.assertNameTaken(self.call("post", "/manage/referee-levels", token, data={"name": "L1", "team_ids": []}))

    def test_onboarding_requirement(self):
        token = self.admin()
        body = {"name": "Photo", "description": "", "requires_document": False, "blocked_event_kinds": [], "is_active": True}
        for path in ("/manage/onboarding-requirements", "/manage/settings/onboarding-requirements"):
            with self.subTest(path=path):
                body = {**body, "name": f"Photo {len(path)}"}
                self.assertEqual(self.call("post", path, token, data=body).status_code, 201)
                self.assertNameTaken(self.call("post", path, token, data=body))

    def test_other_clubs_name_is_fine(self):
        Group.objects.create(club=make_club(name="Rival FC", slug="rival-fc"), name="Coaches")
        self.assertEqual(self.call("post", "/manage/groups", self.admin(), data={"name": "Coaches"}).status_code, 201)


class SpreadsheetUploadTests(TeamExtrasTestCase):
    def test_octet_stream_xlsx_is_accepted_but_a_wrong_extension_is_not(self):
        token = self.as_admin()
        url = "/manage/members/import?dry_run=true"
        garbage = SimpleUploadedFile("members.xlsx", b"not a workbook", content_type="application/octet-stream")
        response = self.upload(url, token, {"file": garbage})
        self.assertEqual(response.status_code, 422, response.content)  # got past the type check, then unreadable
        self.assertIn("file", response.json()["error"]["fields"])
        csv = SimpleUploadedFile("members.csv", b"a,b", content_type="application/octet-stream")
        self.assertEqual(self.upload(url, token, {"file": csv}).status_code, 415)
        text = SimpleUploadedFile("members.xlsx", b"a,b", content_type="text/csv")
        self.assertEqual(self.upload(url, token, {"file": text}).status_code, 415)
