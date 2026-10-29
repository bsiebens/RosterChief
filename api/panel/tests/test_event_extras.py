import datetime
import json
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from waffle import get_waffle_flag_model

from api.models import Job
from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import ClubRole
from events.models import Event, EventAttachment, EventOfficial, EventReferee, EventTask, EventTaskClaim, Location
from events.services.competitions import CompetitionFetchError
from members.models import Member
from rosterchief.test_support import make_club
from teams.models import Position, StaffAssignment, Team

EVENTS = "/manage/events"

RBIHF_URL = "https://www.rbihf.be/league/team/4460"
RBIHF_HTML = """<html><body>
<div class="block"><div class="block-header"><h2>Sportoase Antwerp Phantoms</h2></div></div>
<div class="block"><div class="block-header"><h2 id="games-upcoming">Upcoming games</h2></div>
<div class="block-content"><table>
<tr><th class="game-nr">#</th><th class="date">Date</th><th class="hour">Hour</th><th>Location</th><th>Home</th><th>Visit</th></tr>
<tr>
<td class="game-nr"><a href="/game/5002" title="Game 5002">5002</a></td>
<td class="date">2026-09-12</td>
<td class="hour">12:15</td>
<td>Deurne</td>
<td><a href="/league/team/4460" title="Sportoase Antwerp Phantoms">Sportoase Antwerp Phantoms</a></td>
<td><a href="/league/team/4464" title="Amsterdam Tigers">Amsterdam Tigers</a></td>
</tr>
</table></div></div>
</body></html>"""


class EventExtrasBase(AppApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(self.remove_files)
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        self.head_pos = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        self.coach_pos = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        self.home = Location.objects.create(club=self.club, name="Ice Hall", address="1 Rink St", city="Antwerp", zip_code="2000", country="BE", is_home=True)
        self.start = (timezone.now() + datetime.timedelta(days=3)).replace(microsecond=0)

    def remove_files(self):
        for attachment in EventAttachment.objects.all():
            attachment.file.delete(save=False)

    # --- helpers ------------------------------------------------------------

    def as_admin(self):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": ClubRole.Roles.ADMIN})
        return self.login_with_totp()

    def login_with_totp(self):
        # One authenticator per user: log in once per test, then switch roles under the same token.
        if getattr(self, "_totp_token", None) is None:
            self._totp_token = super().login_with_totp()
        return self._totp_token

    def as_manager(self, team=None):
        ClubRole.objects.filter(club=self.club, member=self.member).delete()
        StaffAssignment.objects.create(team=team or self.team, member=self.member, season=self.season, position=self.head_pos)
        return self.login_with_totp()

    def as_coach(self):
        StaffAssignment.objects.create(team=self.team, member=self.member, season=self.season, position=self.coach_pos)
        return self.login()

    def call(self, method, path, token=None, data=None, host=CLUB_HOST, **headers):
        return self.api(method, path, token=token, host=host, data=data, **headers)

    def multipart(self, path, token, data, host=CLUB_HOST, **headers):
        return self.client.post(f"/api/v1{path}", data=data, HTTP_HOST=host, HTTP_AUTHORIZATION=f"Bearer {token}", **headers)

    def make_event(self, club=None, team=None, **kwargs):
        club = club or self.club
        event = Event.objects.create(club=club, kind=kwargs.pop("kind", "training"), title=kwargs.pop("title", "Practice"), start=kwargs.pop("start", self.start), **kwargs)
        event.teams.add(team or self.team)
        return event

    def make_game(self, **kwargs):
        return self.make_event(kind="game", title="Cup game", location=self.home, **kwargs)

    def foreign_event(self):
        rival = make_club(name="Rival FC", slug="rival-fc")
        rival_team = Team.objects.create(club=rival, name="R", short_name="R")
        return self.make_event(club=rival, team=rival_team)

    def enable(self, name):
        flag, _created = get_waffle_flag_model().objects.get_or_create(name=name)
        flag.clubs.add(self.club)
        cache.clear()


class TaskTests(EventExtrasBase):
    def test_requires_token(self):
        event = self.make_event()
        task = EventTask.objects.create(event=event, title="Fruit")
        for method, path in [("get", f"{EVENTS}/{event.pk}/tasks"), ("post", f"{EVENTS}/{event.pk}/tasks"), ("patch", f"{EVENTS}/{event.pk}/tasks/{task.pk}"), ("delete", f"{EVENTS}/{event.pk}/tasks/{task.pk}")]:
            self.assertError(self.call(method, path), 401, "unauthenticated")

    def test_non_manager_cannot_write(self):
        event = self.make_event()
        task = EventTask.objects.create(event=event, title="Fruit")
        token = self.as_coach()
        # A coach without the management position is not event staff: reading is the web's scope, writing is 403.
        self.assertError(self.call("post", f"{EVENTS}/{event.pk}/tasks", token, {"title": "X"}), 403, "forbidden")
        self.assertError(self.call("patch", f"{EVENTS}/{event.pk}/tasks/{task.pk}", token, {"title": "X"}), 403, "forbidden")
        self.assertError(self.call("delete", f"{EVENTS}/{event.pk}/tasks/{task.pk}", token), 403, "forbidden")
        self.assertEqual(EventTask.objects.count(), 1)

    def test_manager_of_another_teams_event_is_forbidden(self):
        other = Team.objects.create(club=self.club, name="U14", short_name="U14")
        event = self.make_event(team=other)
        token = self.as_manager()
        self.assertError(self.call("post", f"{EVENTS}/{event.pk}/tasks", token, {"title": "X"}), 403, "forbidden")

    def test_other_club_event_is_not_found(self):
        token = self.as_admin()
        foreign = self.foreign_event()
        task = EventTask.objects.create(event=foreign, title="Fruit")
        for method, path, data in [("get", "/tasks", None), ("post", "/tasks", {"title": "x"}), ("patch", f"/tasks/{task.pk}", {"title": "x"}), ("delete", f"/tasks/{task.pk}", None)]:
            self.assertError(self.call(method, f"{EVENTS}/{foreign.pk}{path}", token, data), 404, "not_found")

    def test_create_list_patch_delete(self):
        event = self.make_event()
        token = self.as_manager()

        created = self.call("post", f"{EVENTS}/{event.pk}/tasks", token, {"title": "Bring fruit", "description": "Oranges", "needed_quantity": 2})
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual((body["title"], body["needed_quantity"], body["claimed_count"]), ("Bring fruit", 2, 0))
        self.assertEqual(EventTask.objects.get().created_by, self.member)

        claimer = Member.objects.create(first_name="Cl", last_name="Aimer")
        EventTaskClaim.objects.create(task=EventTask.objects.get(), member=claimer)
        listing = self.call("get", f"{EVENTS}/{event.pk}/tasks", token).json()
        self.assertEqual([(t["title"], t["claimed_count"]) for t in listing["items"]], [("Bring fruit", 1)])

        patched = self.call("patch", f"{EVENTS}/{event.pk}/tasks/{body['id']}", token, {"needed_quantity": 3})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()["title"], patched.json()["needed_quantity"]), ("Bring fruit", 3))

        self.assertEqual(self.call("delete", f"{EVENTS}/{event.pk}/tasks/{body['id']}", token).status_code, 204)
        self.assertFalse(EventTask.objects.exists())

    def test_validation_errors(self):
        event = self.make_event()
        token = self.as_manager()
        blank = self.call("post", f"{EVENTS}/{event.pk}/tasks", token, {"title": ""})
        self.assertError(blank, 422, "validation_error")
        self.assertIn("title", blank.json()["error"]["fields"])
        self.assertError(self.call("post", f"{EVENTS}/{event.pk}/tasks", token, {"title": "x", "needed_quantity": 0}), 422, "validation_error")

    def test_unknown_task_is_not_found(self):
        event = self.make_event()
        token = self.as_manager()
        unknown = "6f1c3a52-0000-4000-8000-000000000000"
        self.assertError(self.call("patch", f"{EVENTS}/{event.pk}/tasks/{unknown}", token, {"title": "x"}), 404, "not_found")
        self.assertError(self.call("delete", f"{EVENTS}/{event.pk}/tasks/{unknown}", token), 404, "not_found")

    def test_create_is_idempotent(self):
        event = self.make_event()
        token = self.as_manager()
        first = self.call("post", f"{EVENTS}/{event.pk}/tasks", token, {"title": "Fruit"}, HTTP_IDEMPOTENCY_KEY="task-1")
        second = self.call("post", f"{EVENTS}/{event.pk}/tasks", token, {"title": "Fruit"}, HTTP_IDEMPOTENCY_KEY="task-1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(EventTask.objects.count(), 1)


class AttachmentTests(EventExtrasBase):
    def upload(self, event, token, *files, **kwargs):
        return self.multipart(f"{EVENTS}/{event.pk}/attachments", token, {"files": list(files)}, **kwargs)

    def pdf(self, name="plan.pdf", content=b"%PDF-1.4 plan"):
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def test_requires_token(self):
        event = self.make_event()
        attachment_id = "6f1c3a52-0000-4000-8000-000000000000"
        for method, path in [("get", "/attachments"), ("post", "/attachments"), ("get", f"/attachments/{attachment_id}"), ("delete", f"/attachments/{attachment_id}")]:
            self.assertError(self.call(method, f"{EVENTS}/{event.pk}{path}"), 401, "unauthenticated")

    def test_non_manager_is_forbidden(self):
        event = self.make_event()
        attachment = EventAttachment.objects.create(event=event, file=self.pdf(), name="plan.pdf", size=3)
        token = self.as_coach()
        self.assertError(self.call("get", f"{EVENTS}/{event.pk}/attachments", token), 403, "forbidden")
        self.assertError(self.upload(event, token, self.pdf()), 403, "forbidden")
        self.assertError(self.call("get", f"{EVENTS}/{event.pk}/attachments/{attachment.pk}", token), 403, "forbidden")
        self.assertError(self.call("delete", f"{EVENTS}/{event.pk}/attachments/{attachment.pk}", token), 403, "forbidden")

    def test_other_club_event_is_not_found(self):
        token = self.as_admin()
        foreign = self.foreign_event()
        attachment = EventAttachment.objects.create(event=foreign, file=self.pdf(), name="plan.pdf", size=3)
        self.assertError(self.call("get", f"{EVENTS}/{foreign.pk}/attachments", token), 404, "not_found")
        self.assertError(self.upload(foreign, token, self.pdf()), 404, "not_found")
        self.assertError(self.call("get", f"{EVENTS}/{foreign.pk}/attachments/{attachment.pk}", token), 404, "not_found")
        self.assertError(self.call("delete", f"{EVENTS}/{foreign.pk}/attachments/{attachment.pk}", token), 404, "not_found")

    def test_attachment_of_another_event_is_not_found(self):
        event, other = self.make_event(), self.make_event(title="Other")
        attachment = EventAttachment.objects.create(event=other, file=self.pdf(), name="plan.pdf", size=3)
        token = self.as_admin()
        self.assertError(self.call("get", f"{EVENTS}/{event.pk}/attachments/{attachment.pk}", token), 404, "not_found")

    def test_upload_list_download_delete(self):
        event = self.make_event()
        token = self.as_manager()

        response = self.upload(event, token, self.pdf("plan.pdf", b"%PDF-1.4 plan"), SimpleUploadedFile("map.png", b"\x89PNG map", content_type="image/png"))
        self.assertEqual(response.status_code, 201, response.content)
        items = response.json()["items"]
        self.assertEqual(sorted(i["name"] for i in items), ["map.png", "plan.pdf"])
        self.assertEqual(EventAttachment.objects.get(name="plan.pdf").uploaded_by, self.member)

        listing = self.call("get", f"{EVENTS}/{event.pk}/attachments", token).json()["items"]
        self.assertEqual(len(listing), 2)

        plan = next(i for i in items if i["name"] == "plan.pdf")
        download = self.call("get", f"{EVENTS}/{event.pk}/attachments/{plan['id']}", token)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), b"%PDF-1.4 plan")
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertIn("plan.pdf", download["Content-Disposition"])

        stored = EventAttachment.objects.get(pk=plan["id"]).file.name
        self.assertEqual(self.call("delete", f"{EVENTS}/{event.pk}/attachments/{plan['id']}", token).status_code, 204)
        self.assertFalse(EventAttachment.objects.filter(pk=plan["id"]).exists())
        self.assertFalse(EventAttachment._meta.get_field("file").storage.exists(stored))

    def test_disallowed_extension_is_415(self):
        event = self.make_event()
        token = self.as_manager()
        response = self.upload(event, token, SimpleUploadedFile("evil.html", b"<script>", content_type="text/html"))
        self.assertError(response, 415, "unsupported_media_type")
        self.assertFalse(EventAttachment.objects.exists())

    def test_too_large_is_413(self):
        event = self.make_event()
        token = self.as_manager()
        big = SimpleUploadedFile("big.pdf", b"0" * (10 * 1024 * 1024 + 1), content_type="application/pdf")
        self.assertError(self.upload(event, token, big), 413, "file_too_large")

    def test_empty_file_and_missing_part_are_422(self):
        event = self.make_event()
        token = self.as_manager()
        self.assertError(self.upload(event, token, SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")), 422, "validation_error")
        self.assertError(self.multipart(f"{EVENTS}/{event.pk}/attachments", token, {}), 422, "validation_error")

    def test_attachment_limit_is_422_on_files(self):
        event = self.make_event()
        for i in range(20):
            EventAttachment.objects.create(event=event, file=self.pdf(f"p{i}.pdf"), name=f"p{i}.pdf", size=3)
        token = self.as_manager()
        response = self.upload(event, token, self.pdf("one-more.pdf"))
        self.assertError(response, 422, "validation_error")
        self.assertIn("files", response.json()["error"]["fields"])

    def test_upload_is_idempotent(self):
        event = self.make_event()
        token = self.as_manager()
        first = self.upload(event, token, self.pdf(), HTTP_IDEMPOTENCY_KEY="up-1")
        second = self.upload(event, token, self.pdf(), HTTP_IDEMPOTENCY_KEY="up-1")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(EventAttachment.objects.count(), 1)


class FeeTests(EventExtrasBase):
    def setUp(self):
        super().setUp()
        self.game = self.make_game()
        self.referee = EventReferee.objects.create(event=self.game, external_name="Ref Eree")
        self.url = f"{EVENTS}/{self.game.pk}/fees"

    def row(self, assignment, kind="referee", **fields):
        return {"kind": kind, "id": str(assignment.pk), **fields}

    def test_requires_token(self):
        self.assertError(self.call("get", self.url), 401, "unauthenticated")
        self.assertError(self.call("put", self.url, data={"fees": []}), 401, "unauthenticated")

    def test_admin_only(self):
        for token in (self.as_manager(),):
            self.assertError(self.call("get", self.url, token), 403, "forbidden")
            self.assertError(self.call("put", self.url, token, {"fees": [self.row(self.referee, fee="25.00")]}), 403, "forbidden")

    def test_other_club_event_is_not_found(self):
        token = self.as_admin()
        foreign = self.foreign_event()
        self.assertError(self.call("get", f"{EVENTS}/{foreign.pk}/fees", token), 404, "not_found")
        self.assertError(self.call("put", f"{EVENTS}/{foreign.pk}/fees", token, {"fees": [self.row(self.referee, fee="1")]}), 404, "not_found")

    def test_list_and_save(self):
        token = self.as_admin()
        listing = self.call("get", self.url, token).json()["items"]
        self.assertEqual([(r["kind"], r["name"], Decimal(r["fee"])) for r in listing], [("referee", "Ref Eree", Decimal("0"))])

        response = self.call("put", self.url, token, {"fees": [self.row(self.referee, fee="25.00", km="40", km_rate="0.35")]})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["saved"], ["Ref Eree"])
        self.assertEqual(Decimal(body["items"][0]["total_payable"]), Decimal("39.00"))
        self.referee.refresh_from_db()
        self.assertEqual((self.referee.fee, self.referee.km, self.referee.km_rate), (Decimal("25.00"), Decimal("40.0"), Decimal("0.3500")))

        unpaid = self.call("put", self.url, token, {"fees": [self.row(self.referee, no_fee=True, fee="25.00", km="40", km_rate="0.35")]})
        self.assertEqual(unpaid.status_code, 200, unpaid.content)
        self.referee.refresh_from_db()
        self.assertEqual((self.referee.no_fee, self.referee.fee, self.referee.km), (True, Decimal("0.00"), None))

    def test_unchanged_row_is_not_reported(self):
        token = self.as_admin()
        self.call("put", self.url, token, {"fees": [self.row(self.referee, fee="25.00")]})
        again = self.call("put", self.url, token, {"fees": [self.row(self.referee, fee="25.00")]})
        self.assertEqual(again.json()["saved"], [])

    def test_invalid_row_saves_nothing(self):
        other = EventReferee.objects.create(event=self.game, external_name="Second Ref")
        token = self.as_admin()
        response = self.call("put", self.url, token, {"fees": [self.row(self.referee, fee="25.00"), self.row(other, fee="1", km="5", km_rate="12345678")]})
        self.assertError(response, 422, "validation_error")
        self.assertTrue(any(key.startswith(f"fees.{other.pk}.") for key in response.json()["error"]["fields"]))
        self.referee.refresh_from_db()
        self.assertEqual(self.referee.fee, Decimal("0.00"))

    def test_negative_fee_is_422(self):
        token = self.as_admin()
        self.assertError(self.call("put", self.url, token, {"fees": [self.row(self.referee, fee="-1")]}), 422, "validation_error")

    def test_unknown_assignment_is_422(self):
        token = self.as_admin()
        response = self.call("put", self.url, token, {"fees": [{"kind": "referee", "id": "6f1c3a52-0000-4000-8000-000000000000", "fee": "1"}]})
        self.assertError(response, 422, "validation_error")

    def test_officials_need_the_feature(self):
        official = EventOfficial.objects.create(event=self.game, external_name="Off Icial")
        token = self.as_admin()
        self.assertError(self.call("put", self.url, token, {"fees": [self.row(official, "official", fee="10")]}), 422, "validation_error")
        self.assertEqual([r["kind"] for r in self.call("get", self.url, token).json()["items"]], ["referee"])

        self.enable("officials")
        response = self.call("put", self.url, token, {"fees": [self.row(official, "official", fee="10")]})
        self.assertEqual(response.status_code, 200, response.content)
        official.refresh_from_db()
        self.assertEqual(official.fee, Decimal("10.00"))
        self.assertEqual({r["kind"] for r in response.json()["items"]}, {"referee", "official"})


class PdfTests(EventExtrasBase):
    def setUp(self):
        super().setUp()
        self.game = self.make_game()
        EventReferee.objects.create(event=self.game, external_name="Ref Eree")

    def test_requires_token(self):
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/referees/form-pdf"), 401, "unauthenticated")
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/officials/form-pdf"), 401, "unauthenticated")

    def test_referee_form_downloads_for_a_referee_manager(self):
        token = self.as_admin()
        with mock.patch("management.pdf.event_referee_form_pdf", return_value=b"%PDF-fake") as renderer:
            response = self.call("get", f"{EVENTS}/{self.game.pk}/referees/form-pdf", token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response.content, b"%PDF-fake")
        self.assertIn(f"referee-form-{self.game.pk}.pdf", response["Content-Disposition"])
        self.assertEqual([r.external_name for r in renderer.call_args.args[0]["referees"]], ["Ref Eree"])

    def test_referee_form_is_forbidden_for_a_team_manager(self):
        token = self.as_manager()
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/referees/form-pdf", token), 403, "forbidden")

    def test_pdf_library_missing_is_503(self):
        from management.pdf import PDFExportError

        token = self.as_admin()
        with mock.patch("management.pdf.event_referee_form_pdf", side_effect=PDFExportError("no pango")):
            self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/referees/form-pdf", token), 503, "pdf_unavailable")

    def test_other_club_event_is_not_found(self):
        token = self.as_admin()
        foreign = self.foreign_event()
        self.assertError(self.call("get", f"{EVENTS}/{foreign.pk}/referees/form-pdf", token), 404, "not_found")

    def test_official_form_needs_the_feature(self):
        token = self.as_admin()
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/officials/form-pdf", token), 404, "not_found")
        self.enable("officials")
        EventOfficial.objects.create(event=self.game, external_name="Off Icial")
        with mock.patch("management.pdf.event_official_form_pdf", return_value=b"%PDF-off"):
            response = self.call("get", f"{EVENTS}/{self.game.pk}/officials/form-pdf", token)
        self.assertEqual((response.status_code, response.content), (200, b"%PDF-off"))
        self.assertIn(f"official-form-{self.game.pk}.pdf", response["Content-Disposition"])
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/officials/form-pdf", self.as_manager()), 403, "forbidden")


class FetchGameInfoTests(EventExtrasBase):
    def url(self, event):
        return f"{EVENTS}/{event.pk}/fetch-game-info"

    def test_requires_token(self):
        self.assertError(self.call("post", self.url(self.make_game())), 401, "unauthenticated")

    def test_forbidden_and_other_club(self):
        game = self.make_game()
        self.assertError(self.call("post", self.url(game), self.as_coach()), 403, "forbidden")
        token = self.as_admin()
        self.assertError(self.call("post", self.url(self.foreign_event()), token), 404, "not_found")

    def test_fetch_and_nothing_to_fetch(self):
        game = self.make_game()
        token = self.as_manager()
        with mock.patch("api.panel.routers.event_extras.fetch_game_info", return_value=True) as fetch:
            response = self.call("post", self.url(game), token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["fetched"], True)
        self.assertEqual(response.json()["event"]["id"], str(game.pk))
        fetch.assert_called_once()
        with mock.patch("api.panel.routers.event_extras.fetch_game_info", return_value=False):
            self.assertEqual(self.call("post", self.url(game), token).json()["fetched"], False)

    def test_failing_source_is_502(self):
        game = self.make_game()
        token = self.as_manager()
        with mock.patch("api.panel.routers.event_extras.fetch_game_info", side_effect=CompetitionFetchError("boom")):
            self.assertError(self.call("post", self.url(game), token), 502, "competition_fetch_failed")


class FixtureImportTests(EventExtrasBase):
    def setUp(self):
        super().setUp()
        self.first = Team.objects.create(club=self.club, name="First Team", short_name="1st")
        self.body = {"url": RBIHF_URL, "team_id": str(self.first.pk), "season_id": str(self.season.pk)}

    def test_requires_token(self):
        for method, path in [("get", f"{EVENTS}/import/sources"), ("post", f"{EVENTS}/import/rbihf/preview"), ("post", f"{EVENTS}/import/rbihf/apply"), ("get", f"{EVENTS}/fixture-recheck"), ("post", f"{EVENTS}/fixture-recheck/apply")]:
            self.assertError(self.call(method, path), 401, "unauthenticated")

    def test_sources_lists_the_clubs_importers(self):
        token = self.as_admin()
        body = self.call("get", f"{EVENTS}/import/sources", token).json()
        self.assertEqual({s["source"]: s["enabled"] for s in body["sources"]}, {"rbihf": False, "dfel": False, "ijnl": False})
        self.assertEqual((body["json_import"], body["recheck"]), (True, False))
        self.enable("DFEL")
        body = self.call("get", f"{EVENTS}/import/sources", token).json()
        self.assertEqual(({s["source"]: s["enabled"] for s in body["sources"]}["dfel"], body["recheck"]), (True, True))
        self.assertError(self.call("get", f"{EVENTS}/import/sources", self.as_manager()), 403, "forbidden")

    def test_importer_is_404_while_the_feature_is_off(self):
        token = self.as_admin()
        for source in ("rbihf", "dfel", "ijnl"):
            self.assertError(self.call("post", f"{EVENTS}/import/{source}/preview", token, self.body), 404, "not_found")
            self.assertError(self.call("post", f"{EVENTS}/import/{source}/apply", token, self.body), 404, "not_found")

    def test_unknown_source_is_404(self):
        token = self.as_admin()
        self.assertError(self.call("post", f"{EVENTS}/import/nhl/preview", token, self.body), 404, "not_found")

    def test_non_admin_is_forbidden(self):
        self.enable("RBIHF")
        token = self.as_manager()
        self.assertError(self.call("post", f"{EVENTS}/import/rbihf/preview", token, self.body), 403, "forbidden")
        self.assertError(self.call("post", f"{EVENTS}/import/rbihf/apply", token, self.body), 403, "forbidden")

    def test_preview_shows_the_diff_and_saves_nothing(self):
        self.enable("RBIHF")
        token = self.as_admin()
        with mock.patch("events.services.rbihf_import.fetch_html", return_value=RBIHF_HTML) as fetch:
            response = self.call("post", f"{EVENTS}/import/rbihf/preview", token, self.body)
        self.assertEqual(response.status_code, 200, response.content)
        plan = response.json()["plan"]
        fetch.assert_called_once_with(RBIHF_URL)
        self.assertEqual(plan["source"], "RBIHF")
        self.assertEqual(plan["scraped_team_name"], "Sportoase Antwerp Phantoms")
        self.assertEqual([p["fixture"]["external_game_id"] for p in plan["to_create"]], ["5002"])
        self.assertEqual(plan["to_create"][0]["fixture"]["opponent_name"], "Amsterdam Tigers")
        self.assertFalse(Event.objects.filter(club=self.club).exists())

    def test_preview_marks_updates_and_deletions(self):
        self.enable("RBIHF")
        token = self.as_admin()
        stale = Event.objects.create(club=self.club, kind="game", title="Old", start=self.start, competition="RBIHF", external_game_id="5002", external_source_id="4460")
        stale.teams.add(self.first)
        gone = Event.objects.create(club=self.club, kind="game", title="Gone", start=self.start, competition="RBIHF", external_game_id="9999", external_source_id="4460")
        gone.teams.add(self.first)
        with mock.patch("events.services.rbihf_import.fetch_html", return_value=RBIHF_HTML):
            plan = self.call("post", f"{EVENTS}/import/rbihf/preview", token, self.body).json()["plan"]
        self.assertEqual([p["event_id"] for p in plan["to_update"]], [str(stale.pk)])
        self.assertIn("start", plan["to_update"][0]["changes"])
        self.assertEqual([d["event_id"] for d in plan["to_delete"]], [str(gone.pk)])

    def test_preview_validation(self):
        self.enable("RBIHF")
        token = self.as_admin()
        bad_url = self.call("post", f"{EVENTS}/import/rbihf/preview", token, {**self.body, "url": "https://example.com/nope"})
        self.assertError(bad_url, 422, "validation_error")
        self.assertIn("url", bad_url.json()["error"]["fields"])
        rival = make_club(name="Rival FC", slug="rival-fc")
        foreign_team = Team.objects.create(club=rival, name="R", short_name="R")
        bad_team = self.call("post", f"{EVENTS}/import/rbihf/preview", token, {**self.body, "team_id": str(foreign_team.pk)})
        self.assertError(bad_team, 422, "validation_error")
        self.assertIn("team_id", bad_team.json()["error"]["fields"])

    def test_preview_source_error_is_422_on_url(self):
        from events.services.rbihf_import import RBIHFImportError

        self.enable("RBIHF")
        token = self.as_admin()
        with mock.patch("events.services.rbihf_import.fetch_html", side_effect=RBIHFImportError("HTTP 500")):
            response = self.call("post", f"{EVENTS}/import/rbihf/preview", token, self.body)
        self.assertError(response, 422, "validation_error")
        self.assertEqual(response.json()["error"]["fields"]["url"], ["HTTP 500"])

    def test_apply_is_a_job_that_creates_the_events(self):
        self.enable("RBIHF")
        token = self.as_admin()
        location = Location.objects.create(club=self.club, name="Deurne Ice Hall", address="1 St", city="Deurne", zip_code="2100", country="BE")
        with mock.patch("events.services.rbihf_import.fetch_html", return_value=RBIHF_HTML), self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", f"{EVENTS}/import/rbihf/apply", token, {**self.body, "competition_label": "Division 1", "locations": {"5002": str(location.pk)}})
        self.assertEqual(response.status_code, 202, response.content)
        job = self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()
        self.assertEqual((job["status"], job["result"]), ("succeeded", {"created": 1, "updated": 0, "deleted": 0}))
        event = Event.objects.get(club=self.club, external_game_id="5002")
        self.assertEqual((event.location, event.competition_label, event.external_source_id), (location, "Division 1", "4460"))
        self.assertIn(self.first, event.teams.all())
        self.first.refresh_from_db()
        self.assertEqual(self.first.import_sources[0]["url"], RBIHF_URL)

    def test_apply_job_reports_a_source_error(self):
        from events.services.rbihf_import import RBIHFImportError

        self.enable("RBIHF")
        token = self.as_admin()
        with mock.patch("events.services.rbihf_import.fetch_html", side_effect=RBIHFImportError("HTTP 500")), self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", f"{EVENTS}/import/rbihf/apply", token, self.body)
        job = self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()
        self.assertEqual(job["status"], "failed")
        self.assertEqual((job["error"]["code"], job["error"]["message"]), ("import_failed", "HTTP 500"))

    def test_apply_validates_before_starting_a_job(self):
        self.enable("RBIHF")
        token = self.as_admin()
        self.assertError(self.call("post", f"{EVENTS}/import/rbihf/apply", token, {**self.body, "url": "nope"}), 422, "validation_error")
        self.assertFalse(Job.objects.exists())

    def test_apply_is_idempotent(self):
        self.enable("RBIHF")
        token = self.as_admin()
        with mock.patch("events.services.rbihf_import.fetch_html", return_value=RBIHF_HTML), self.captureOnCommitCallbacks(execute=True):
            first = self.call("post", f"{EVENTS}/import/rbihf/apply", token, self.body, HTTP_IDEMPOTENCY_KEY="imp-1")
            second = self.call("post", f"{EVENTS}/import/rbihf/apply", token, self.body, HTTP_IDEMPOTENCY_KEY="imp-1")
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Job.objects.count(), 1)

    def test_other_clubs_job_is_not_visible(self):
        make_club(name="Rival FC", slug="rival-fc")
        self.enable("RBIHF")
        token = self.as_admin()
        with mock.patch("events.services.rbihf_import.fetch_html", return_value=RBIHF_HTML), self.captureOnCommitCallbacks(execute=True):
            job_id = self.call("post", f"{EVENTS}/import/rbihf/apply", token, self.body).json()["id"]
        self.assertError(self.call("get", f"/manage/jobs/{job_id}", token, host=OTHER_HOST), 403, "not_a_member")


class RecheckTests(EventExtrasBase):
    def setUp(self):
        super().setUp()
        self.first = Team.objects.create(club=self.club, name="First Team", short_name="1st")
        self.first.import_sources = [{"source": "RBIHF", "url": RBIHF_URL, "season_id": str(self.season.pk), "season_name": self.season.name, "competition_label": "", "recorded_at": timezone.now().isoformat()}]
        self.first.save()

    def test_off_without_an_importer_and_admin_only(self):
        token = self.as_admin()
        self.assertError(self.call("get", f"{EVENTS}/fixture-recheck", token), 404, "not_found")
        self.assertError(self.call("post", f"{EVENTS}/fixture-recheck/apply", token, {}), 404, "not_found")
        self.enable("RBIHF")
        manager = self.as_manager()
        self.assertError(self.call("get", f"{EVENTS}/fixture-recheck", manager), 403, "forbidden")
        self.assertError(self.call("post", f"{EVENTS}/fixture-recheck/apply", manager, {}), 403, "forbidden")

    def test_preview(self):
        self.enable("RBIHF")
        token = self.as_admin()
        with mock.patch("events.services.rbihf_import.fetch_html", return_value=RBIHF_HTML):
            response = self.call("get", f"{EVENTS}/fixture-recheck", token)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["season_id"], body["has_changes"]), (str(self.season.pk), True))
        section = body["sections"][0]
        self.assertEqual((section["team_id"], section["source"], section["url"], section["error"]), (str(self.first.pk), "RBIHF", RBIHF_URL, ""))
        self.assertEqual([p["fixture"]["external_game_id"] for p in section["plan"]["to_create"]], ["5002"])
        self.assertFalse(Event.objects.filter(club=self.club).exists())

    def test_preview_reports_a_failing_list_per_section(self):
        from events.services.rbihf_import import RBIHFImportError

        self.enable("RBIHF")
        token = self.as_admin()
        with mock.patch("events.services.rbihf_import.fetch_html", side_effect=RBIHFImportError("HTTP 500")):
            body = self.call("get", f"{EVENTS}/fixture-recheck", token).json()
        self.assertEqual((body["sections"][0]["error"], body["sections"][0]["plan"], body["has_changes"]), ("HTTP 500", None, False))

    def test_preview_filters_and_unknown_ids(self):
        self.enable("RBIHF")
        token = self.as_admin()
        unknown = "6f1c3a52-0000-4000-8000-000000000000"
        self.assertError(self.call("get", f"{EVENTS}/fixture-recheck?team_id={unknown}", token), 404, "not_found")
        self.assertError(self.call("get", f"{EVENTS}/fixture-recheck?season_id={unknown}", token), 404, "not_found")
        rival = make_club(name="Rival FC", slug="rival-fc")
        foreign_team = Team.objects.create(club=rival, name="R", short_name="R")
        self.assertError(self.call("get", f"{EVENTS}/fixture-recheck?team_id={foreign_team.pk}", token), 404, "not_found")
        other = Team.objects.create(club=self.club, name="Zed", short_name="Z")
        with mock.patch("events.services.rbihf_import.fetch_html", return_value=RBIHF_HTML):
            body = self.call("get", f"{EVENTS}/fixture-recheck?team_id={other.pk}", token).json()
        self.assertEqual(body["sections"], [])

    def test_apply_is_a_job_that_applies_every_stored_list(self):
        self.enable("RBIHF")
        token = self.as_admin()
        location = Location.objects.create(club=self.club, name="Deurne Ice Hall", address="1 St", city="Deurne", zip_code="2100", country="BE")
        choices = [{"team_id": str(self.first.pk), "source": "RBIHF", "competition_label": "", "locations": {"5002": str(location.pk)}}]
        with mock.patch("events.services.rbihf_import.fetch_html", return_value=RBIHF_HTML), self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", f"{EVENTS}/fixture-recheck/apply", token, {"choices": choices})
        self.assertEqual(response.status_code, 202, response.content)
        job = self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()
        self.assertEqual((job["status"], job["result"]), ("succeeded", {"created": 1, "updated": 0, "deleted": 0, "failed": 0}))
        self.assertEqual(Event.objects.get(club=self.club, external_game_id="5002").location, location)

    def test_apply_counts_a_list_that_cannot_be_read(self):
        from events.services.rbihf_import import RBIHFImportError

        self.enable("RBIHF")
        token = self.as_admin()
        with mock.patch("events.services.rbihf_import.fetch_html", side_effect=RBIHFImportError("HTTP 500")), self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", f"{EVENTS}/fixture-recheck/apply", token, {})
        job = self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()
        self.assertEqual(job["result"], {"created": 0, "updated": 0, "deleted": 0, "failed": 1})

    def test_apply_validation(self):
        self.enable("RBIHF")
        token = self.as_admin()
        self.assertError(self.call("post", f"{EVENTS}/fixture-recheck/apply", token, {"season_id": "not-a-uuid"}), 422, "validation_error")
        unknown = "6f1c3a52-0000-4000-8000-000000000000"
        self.assertError(self.call("post", f"{EVENTS}/fixture-recheck/apply", token, {"team_id": unknown}), 404, "not_found")


class JSONImportTests(EventExtrasBase):
    def setUp(self):
        super().setUp()
        self.u8 = Team.objects.create(club=self.club, name="Sharks U8", short_name="U8")
        self.u10 = Team.objects.create(club=self.club, name="Sharks U10", short_name="U10")
        self.away = Location.objects.create(club=self.club, name="Hasselt ice", address="2 Rink", city="Hasselt", zip_code="3500", country="BE")

    def payload(self, **overrides):
        games = [
            {"id": "a", "date": "2027-10-18", "start": "11:45", "end": "13:45", "city": "Antwerp", "teams": ["U10"], "hosted_by_us": True, "participants": ["COP", "IHC"]},
            {"id": "b", "date": "2027-10-17", "start": "17:30", "city": "Hasselt", "teams": ["U8", "U10"], "hosted_by_us": False},
        ]
        return {"source": "Calendar", "our_code": "COP", "games": games, **overrides}

    def file(self, payload=None, name="games.json", raw=None):
        return SimpleUploadedFile(name, raw if raw is not None else json.dumps(payload or self.payload()).encode(), content_type="application/json")

    def test_requires_token_and_admin(self):
        for path in ("preview", "apply"):
            self.assertError(self.client.post(f"/api/v1{EVENTS}/import/json/{path}", {"file": self.file()}, HTTP_HOST=CLUB_HOST), 401, "unauthenticated")
        token = self.as_manager()
        self.assertError(self.multipart(f"{EVENTS}/import/json/preview", token, {"file": self.file()}), 403, "forbidden")
        self.assertError(self.multipart(f"{EVENTS}/import/json/apply", token, {"file": self.file()}), 403, "forbidden")

    def test_preview_suggests_locations_and_warns(self):
        token = self.as_admin()
        response = self.multipart(f"{EVENTS}/import/json/preview", token, {"file": self.file()})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        games = {g["external_game_id"]: g for g in body["games"]}
        self.assertEqual(set(games), {"a", "b"})
        self.assertEqual(games["b"]["suggested_location_id"], str(self.away.pk))
        self.assertEqual(sorted(games["b"]["team_ids"]), sorted([str(self.u8.pk), str(self.u10.pk)]))
        self.assertEqual((games["a"]["suggested_location_id"], games["a"]["warning"]), (str(self.home.pk), None))
        self.assertIsNone(games["b"]["warning"])
        self.assertFalse(Event.objects.filter(club=self.club).exists())

    def test_apply_creates_games_with_the_chosen_locations(self):
        token = self.as_admin()
        home = self.home
        chosen = json.dumps({"a": str(home.pk), "b": str(self.away.pk)})
        response = self.multipart(f"{EVENTS}/import/json/apply", token, {"file": self.file(), "locations": chosen})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"created": 2, "updated": 0})
        self.assertEqual(Event.objects.get(club=self.club, external_game_id="a").location, home)
        self.assertEqual({t.pk for t in Event.objects.get(club=self.club, external_game_id="b").teams.all()}, {self.u8.pk, self.u10.pk})
        again = self.multipart(f"{EVENTS}/import/json/apply", token, {"file": self.file(), "locations": chosen})
        self.assertEqual(again.json(), {"created": 0, "updated": 0})

    def test_contradicting_location_needs_acknowledgement(self):
        token = self.as_admin()
        chosen = json.dumps({"a": str(self.away.pk), "b": str(self.away.pk)})
        blocked = self.multipart(f"{EVENTS}/import/json/apply", token, {"file": self.file(), "locations": chosen})
        self.assertError(blocked, 422, "validation_error")
        self.assertIn("locations.a", blocked.json()["error"]["fields"])
        self.assertIn("acknowledge_warnings", blocked.json()["error"]["fields"])
        self.assertFalse(Event.objects.filter(club=self.club).exists())

        allowed = self.multipart(f"{EVENTS}/import/json/apply", token, {"file": self.file(), "locations": chosen, "acknowledge_warnings": "true"})
        self.assertEqual(allowed.status_code, 200, allowed.content)
        self.assertEqual(Event.objects.filter(club=self.club).count(), 2)

    def test_file_problems(self):
        token = self.as_admin()
        for upload, status, code in [
            (self.file(raw=b"{not json", name="games.json"), 422, "validation_error"),
            (self.file(raw=b"\xff\xfe\x00", name="games.json"), 422, "validation_error"),
            (self.file(self.payload(games=[{"id": "x", "date": "2027-10-18", "start": "11:45", "city": "Antwerp", "teams": ["Nope"]}])), 422, "validation_error"),
            (self.file(name="games.txt"), 415, "unsupported_media_type"),
            (self.file(raw=b"", name="games.json"), 422, "validation_error"),
        ]:
            response = self.multipart(f"{EVENTS}/import/json/preview", token, {"file": upload})
            self.assertError(response, status, code)
        self.assertError(self.multipart(f"{EVENTS}/import/json/preview", token, {}), 422, "validation_error")

    def test_bad_locations_value_is_422(self):
        token = self.as_admin()
        self.assertError(self.multipart(f"{EVENTS}/import/json/apply", token, {"file": self.file(), "locations": "[1]"}), 422, "validation_error")
        self.assertError(self.multipart(f"{EVENTS}/import/json/apply", token, {"file": self.file(), "locations": "nope"}), 422, "validation_error")

    def test_a_location_of_another_club_is_ignored(self):
        token = self.as_admin()
        rival = make_club(name="Rival FC", slug="rival-fc")
        foreign = Location.objects.create(club=rival, name="Elsewhere", address="1", city="X", zip_code="1", country="BE")
        chosen = json.dumps({"a": str(foreign.pk), "b": str(foreign.pk)})
        response = self.multipart(f"{EVENTS}/import/json/apply", token, {"file": self.file(), "locations": chosen, "acknowledge_warnings": "true"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Event.objects.filter(club=self.club, location=foreign).exists())
