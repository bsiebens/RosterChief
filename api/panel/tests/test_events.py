import datetime
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import ClubMembership, ClubRole
from events.models import Attendance, Event, EventSeries, Location, Opponent
from members.models import Member
from rosterchief.test_support import make_club
from teams.models import Position, StaffAssignment, Team, TeamMembership

EVENTS = "/manage/events"
NOTIFY = "api.panel.routers.events.dispatch_notify_new_event"


class PanelEventsTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        self.other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        self.forward = Position.objects.create(club=self.club, name="Forward", short_name="F")
        self.coach_pos = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        self.head_pos = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        self.player = Member.objects.create(first_name="Sam", last_name="Smith")
        ClubMembership.objects.create(club=self.club, member=self.player, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        TeamMembership.objects.create(team=self.team, member=self.player, season=self.season, position=self.forward, jersey_number=7)
        self.location = Location.objects.create(club=self.club, name="Ice Hall", address="1 Rink St", city="Antwerp", zip_code="2000", country="BE", is_home=True)
        self.opponent = Opponent.objects.create(club=self.club, name="Rival HC")
        self.start = (timezone.now() + datetime.timedelta(days=3)).replace(microsecond=0)

    # --- helpers ------------------------------------------------------------

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def as_admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def as_manager(self, team=None):
        StaffAssignment.objects.create(team=team or self.team, member=self.member, season=self.season, position=self.head_pos)
        return self.login_with_totp()

    def as_coach(self, team=None):
        StaffAssignment.objects.create(team=team or self.team, member=self.member, season=self.season, position=self.coach_pos)
        return self.login()

    def call(self, method, path, token=None, data=None, host=CLUB_HOST, **headers):
        return self.api(method, path, token=token, host=host, data=data, **headers)

    def make_event(self, club=None, team=None, **kwargs):
        club = club or self.club
        event = Event.objects.create(club=club, kind=kwargs.pop("kind", "training"), title=kwargs.pop("title", "Practice"), start=kwargs.pop("start", self.start), **kwargs)
        event.teams.add(team or self.team)
        return event

    def payload(self, **kwargs):
        return {"kind": "meeting", "title": "Parents evening", "team_ids": [str(self.team.pk)], "start": self.start.isoformat(), **kwargs}

    def series_payload(self, **kwargs):
        return {"title": "Tuesday training", "kind": "training", "team_ids": [str(self.team.pk)], "dtstart": self.start.isoformat(), "frequency": "weekly", "weekdays": ["TU"], "duration_minutes": 90, **kwargs}

    # --- auth ---------------------------------------------------------------

    def test_requires_token(self):
        event = self.make_event()
        for method, path in [
            ("get", EVENTS),
            ("post", EVENTS),
            ("get", f"{EVENTS}/{event.pk}"),
            ("patch", f"{EVENTS}/{event.pk}"),
            ("delete", f"{EVENTS}/{event.pk}"),
            ("get", f"{EVENTS}/{event.pk}/attendance"),
            ("get", f"{EVENTS}/series"),
        ]:
            self.assertError(self.call(method, path), 401, "unauthenticated")

    def test_plain_member_is_forbidden(self):
        token = self.login()
        event = self.make_event()
        self.assertError(self.call("get", EVENTS, token), 403, "forbidden")
        self.assertError(self.call("get", f"{EVENTS}/{event.pk}", token), 403, "forbidden")
        self.assertError(self.call("post", EVENTS, token, self.payload()), 403, "forbidden")

    def test_coach_without_management_position_cannot_write(self):
        token = self.as_coach()
        event = self.make_event()
        self.assertEqual(self.call("get", EVENTS, token).status_code, 200)
        self.assertError(self.call("post", EVENTS, token, self.payload()), 403, "forbidden")
        self.assertError(self.call("patch", f"{EVENTS}/{event.pk}", token, {"title": "X"}), 403, "forbidden")
        self.assertError(self.call("delete", f"{EVENTS}/{event.pk}", token), 403, "forbidden")

    def test_manager_of_another_team_cannot_change_event(self):
        token = self.as_manager(self.other_team)
        event = self.make_event()
        # Not visible to a non-admin who manages neither team: the web detail's 404; writes are a 403.
        self.assertError(self.call("get", f"{EVENTS}/{event.pk}", token), 404, "not_found")
        self.assertError(self.call("get", f"{EVENTS}/{event.pk}/attendance", token), 404, "not_found")
        self.assertError(self.call("patch", f"{EVENTS}/{event.pk}", token, {"title": "X"}), 403, "forbidden")

    def test_other_club_event_is_not_found(self):
        token = self.as_admin()
        rival = make_club(name="Rival FC", slug="rival-fc")
        rival_team = Team.objects.create(club=rival, name="R", short_name="R")
        foreign = self.make_event(club=rival, team=rival_team)
        for method, path in [("get", ""), ("patch", ""), ("delete", ""), ("post", "/detach"), ("put", "/result"), ("get", "/attendance")]:
            data = {"title": "x"} if method == "patch" else ({"score_for": 1, "score_against": 0} if method == "put" else None)
            self.assertError(self.call(method, f"{EVENTS}/{foreign.pk}{path}", token, data), 404, "not_found")
        self.assertError(self.call("get", f"{EVENTS}/{foreign.pk}", token, host=OTHER_HOST), 403, "not_a_member")

    def test_other_club_team_filter_and_audience(self):
        token = self.as_admin()
        rival = make_club(name="Rival FC", slug="rival-fc")
        rival_team = Team.objects.create(club=rival, name="R", short_name="R")
        self.assertError(self.call("get", f"{EVENTS}?team={rival_team.pk}", token), 404, "not_found")
        response = self.call("post", EVENTS, token, self.payload(team_ids=[str(rival_team.pk)]))
        self.assertError(response, 422, "validation_error")
        self.assertIn("teams", response.json()["error"]["fields"])

    # --- list / detail ------------------------------------------------------

    def test_list_filters_and_pagination(self):
        token = self.as_admin()
        soon = self.make_event(title="Soon")
        later = self.make_event(title="Later", start=self.start + datetime.timedelta(days=10), team=self.other_team, kind="meeting")
        self.make_event(title="Cancelled", start=self.start + datetime.timedelta(days=1), cancelled=True)
        rival = make_club(name="Rival FC", slug="rival-fc")
        self.make_event(club=rival, team=Team.objects.create(club=rival, name="R", short_name="R"), title="Foreign")

        titles = [item["title"] for item in self.call("get", EVENTS, token).json()["items"]]
        self.assertEqual(titles, ["Soon", "Cancelled", "Later"])

        body = self.call("get", f"{EVENTS}?team={self.other_team.pk}", token).json()
        self.assertEqual([item["id"] for item in body["items"]], [str(later.pk)])
        self.assertEqual([item["title"] for item in self.call("get", f"{EVENTS}?kind=meeting", token).json()["items"]], ["Later"])
        self.assertEqual([item["title"] for item in self.call("get", f"{EVENTS}?cancelled=true", token).json()["items"]], ["Cancelled"])

        cutoff = (self.start + datetime.timedelta(days=5)).isoformat().replace("+", "%2B")
        self.assertEqual([item["title"] for item in self.call("get", f"{EVENTS}?from={cutoff}", token).json()["items"]], ["Later"])
        self.assertEqual([item["title"] for item in self.call("get", f"{EVENTS}?to={cutoff}&order=desc", token).json()["items"]], ["Cancelled", "Soon"])
        self.assertEqual(len(self.call("get", f"{EVENTS}?season={self.season.pk}", token).json()["items"]), 3)
        self.assertEqual(self.call("get", f"{EVENTS}?season={self.season.pk}", token, host=CLUB_HOST).status_code, 200)

        first = self.call("get", f"{EVENTS}?limit=2", token).json()
        self.assertEqual(len(first["items"]), 2)
        second = self.call("get", f"{EVENTS}?limit=2&cursor={first['next_cursor']}", token).json()
        self.assertEqual([item["id"] for item in second["items"]], [str(later.pk)])
        self.assertIsNone(second["next_cursor"])
        self.assertTrue(soon.pk)

    def test_manager_list_is_scoped_to_their_teams(self):
        token = self.as_manager()
        mine = self.make_event(title="Mine")
        self.make_event(title="Theirs", team=self.other_team)
        items = self.call("get", EVENTS, token).json()["items"]
        self.assertEqual([item["id"] for item in items], [str(mine.pk)])
        self.assertTrue(items[0]["can_manage"])

    def test_detail_has_counts_and_flags(self):
        token = self.as_admin()
        event = self.make_event(location=self.location, opponent=self.opponent)
        self.attendance_for(event, "present")
        body = self.call("get", f"{EVENTS}/{event.pk}", token).json()
        self.assertEqual(body["id"], str(event.pk))
        self.assertEqual(body["location_id"], str(self.location.pk))
        self.assertEqual(body["attendance_counts"]["present"], 1)
        self.assertEqual(body["attendance_counts"]["no_response"], 0)
        self.assertIsNone(body["series"])
        self.assertTrue(body["can_manage"])

    def test_pickers(self):
        token = self.as_coach()
        self.assertEqual([item["name"] for item in self.call("get", f"{EVENTS}/locations?q=ice", token).json()["items"]], ["Ice Hall"])
        self.assertEqual(self.call("get", f"{EVENTS}/locations?q=zzz", token).json()["items"], [])
        self.assertEqual([item["name"] for item in self.call("get", f"{EVENTS}/opponents", token).json()["items"]], ["Rival HC"])

    # --- create -------------------------------------------------------------

    def test_create_event(self):
        token = self.as_admin()
        with mock.patch(NOTIFY) as notify:
            response = self.call("post", EVENTS, token, self.payload(location_id=str(self.location.pk)))
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        event = Event.objects.get(pk=body["id"])
        self.assertEqual(event.club, self.club)
        self.assertEqual(event.title, "Parents evening")
        self.assertEqual(event.created_by, self.member)
        self.assertEqual(list(event.teams.all()), [self.team])
        self.assertEqual(body["location_id"], str(self.location.pk))
        notify.assert_called_once_with(str(event.pk))

    def test_create_without_notify(self):
        token = self.as_admin()
        with mock.patch(NOTIFY) as notify:
            response = self.call("post", EVENTS, token, self.payload(notify=False))
        self.assertEqual(response.status_code, 201, response.content)
        notify.assert_not_called()

    def test_create_game_gets_matchup_title(self):
        token = self.as_admin()
        with mock.patch(NOTIFY):
            response = self.call("post", EVENTS, token, self.payload(kind="game", title="", opponent_id=str(self.opponent.pk)))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["title"], "U12 vs Rival HC")

    def test_create_is_idempotent(self):
        token = self.as_admin()
        with mock.patch(NOTIFY):
            first = self.call("post", EVENTS, token, self.payload(), HTTP_IDEMPOTENCY_KEY="abc")
            second = self.call("post", EVENTS, token, self.payload(), HTTP_IDEMPOTENCY_KEY="abc")
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Event.objects.filter(title="Parents evening").count(), 1)

    def test_manager_can_create_for_their_team_only(self):
        token = self.as_manager()
        with mock.patch(NOTIFY):
            self.assertEqual(self.call("post", EVENTS, token, self.payload()).status_code, 201)
        response = self.call("post", EVENTS, token, self.payload(team_ids=[str(self.other_team.pk)]))
        self.assertError(response, 422, "validation_error")
        self.assertIn("teams", response.json()["error"]["fields"])

    def test_create_validation(self):
        token = self.as_admin()
        response = self.call("post", EVENTS, token, self.payload(title=""))
        self.assertError(response, 422, "validation_error")
        self.assertIn("title", response.json()["error"]["fields"])
        response = self.call("post", EVENTS, token, self.payload(kind="game", team_ids=[]))
        self.assertIn("teams", response.json()["error"]["fields"])
        response = self.call("post", EVENTS, token, self.payload(kind="bogus"))
        self.assertIn("kind", response.json()["error"]["fields"])
        response = self.call("post", EVENTS, token, {"kind": "meeting"})
        self.assertError(response, 422, "validation_error")
        self.assertEqual(Event.objects.count(), 0)

    # --- update / delete ----------------------------------------------------

    def test_update_event(self):
        token = self.as_manager()
        event = self.make_event()
        response = self.call("patch", f"{EVENTS}/{event.pk}", token, {"title": "Goalie training", "location_id": str(self.location.pk)})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["occurrences_updated"], 0)
        event.refresh_from_db()
        self.assertEqual((event.title, event.location), ("Goalie training", self.location))
        self.assertEqual(list(event.teams.all()), [self.team])
        # null clears an optional field
        self.call("patch", f"{EVENTS}/{event.pk}", token, {"location_id": None})
        event.refresh_from_db()
        self.assertIsNone(event.location)

    def test_update_validation_and_scope_on_one_off(self):
        token = self.as_admin()
        event = self.make_event()
        response = self.call("patch", f"{EVENTS}/{event.pk}", token, {"title": ""})
        self.assertError(response, 422, "validation_error")
        self.assertError(self.call("patch", f"{EVENTS}/{event.pk}", token, {"title": "X", "scope": "all"}), 422, "not_in_series")
        event.refresh_from_db()
        self.assertEqual(event.title, "Practice")

    def test_update_series_occurrence_scopes(self):
        token = self.as_admin()
        with mock.patch(NOTIFY):
            series = self.call("post", f"{EVENTS}/series", token, self.series_payload()).json()
        occurrences = list(Event.objects.filter(series_id=series["id"]).order_by("start"))
        self.assertGreater(len(occurrences), 3)
        first, second = occurrences[0], occurrences[1]

        # this: only that one, and it stops following the series
        response = self.call("patch", f"{EVENTS}/{first.pk}", token, {"title": "Special"})
        self.assertEqual(response.status_code, 200, response.content)
        first.refresh_from_db()
        self.assertTrue(first.detached)
        second.refresh_from_db()
        self.assertEqual(second.title, "Tuesday training")

        # all: every upcoming occurrence still following
        response = self.call("patch", f"{EVENTS}/{second.pk}", token, {"title": "Renamed", "scope": "all"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertGreater(response.json()["occurrences_updated"], 0)
        self.assertEqual(Event.objects.filter(series_id=series["id"], title="Renamed").count(), len(occurrences) - 1)
        first.refresh_from_db()
        self.assertEqual(first.title, "Special")

        # this_and_following: the series splits in two
        third = occurrences[2]
        response = self.call("patch", f"{EVENTS}/{third.pk}", token, {"title": "From here", "scope": "this_and_following"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(EventSeries.objects.filter(club=self.club).count(), 2)
        third.refresh_from_db()
        self.assertNotEqual(str(third.series_id), series["id"])

    def test_delete_one_off(self):
        token = self.as_manager()
        event = self.make_event()
        self.assertEqual(self.call("delete", f"{EVENTS}/{event.pk}", token).status_code, 204)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())
        self.assertError(self.call("delete", f"{EVENTS}/{event.pk}", token), 404, "not_found")

    def test_delete_occurrence_cancels_or_removes(self):
        token = self.as_admin()
        with mock.patch(NOTIFY):
            series = self.call("post", f"{EVENTS}/series", token, self.series_payload()).json()
        first, second = list(Event.objects.filter(series_id=series["id"]).order_by("start")[:2])
        self.assertEqual(self.call("delete", f"{EVENTS}/{first.pk}?keep_record=true", token).status_code, 204)
        first.refresh_from_db()
        self.assertTrue(first.cancelled)
        self.assertEqual(self.call("delete", f"{EVENTS}/{second.pk}", token).status_code, 204)
        self.assertFalse(Event.objects.filter(pk=second.pk).exists())
        self.assertEqual(len(EventSeries.objects.get(pk=series["id"]).excluded_dates), 2)

    def test_detach(self):
        token = self.as_admin()
        with mock.patch(NOTIFY):
            series = self.call("post", f"{EVENTS}/series", token, self.series_payload()).json()
        occurrence = Event.objects.filter(series_id=series["id"]).order_by("start").first()
        response = self.call("post", f"{EVENTS}/{occurrence.pk}/detach", token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["detached"])
        one_off = self.make_event()
        self.assertError(self.call("post", f"{EVENTS}/{one_off.pk}/detach", token), 422, "not_in_series")

    # --- game result --------------------------------------------------------

    def test_set_result(self):
        token = self.as_manager()
        game = self.make_event(kind="game", title="U12 vs Rival HC", opponent=self.opponent, start=timezone.now() - datetime.timedelta(hours=3))
        response = self.call("put", f"{EVENTS}/{game.pk}/result", token, {"score_for": 4, "score_against": 2})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["score_for"], body["score_against"], body["is_live"]), (4, 2, False))
        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against), (4, 2))
        self.assertEqual(game.title, "U12 vs Rival HC")
        # live, then cleared
        self.call("put", f"{EVENTS}/{game.pk}/result", token, {"score_for": 4, "score_against": 3, "is_live": True})
        game.refresh_from_db()
        self.assertTrue(game.is_live)
        self.call("put", f"{EVENTS}/{game.pk}/result", token, {})
        game.refresh_from_db()
        self.assertEqual((game.score_for, game.score_against, game.is_live), (None, None, False))

    def test_set_result_validation_and_permissions(self):
        game = self.make_event(kind="game", title="G")
        training = self.make_event()
        token = self.as_manager()
        self.assertError(self.call("put", f"{EVENTS}/{training.pk}/result", token, {"score_for": 1, "score_against": 0}), 422, "not_a_game")
        response = self.call("put", f"{EVENTS}/{game.pk}/result", token, {"score_for": 1})
        self.assertError(response, 422, "validation_error")
        self.assertIn("score_against", response.json()["error"]["fields"])
        self.assertError(self.call("put", f"{EVENTS}/{game.pk}/result", token, {"score_for": -1, "score_against": 0}), 422, "validation_error")

    def test_coach_cannot_set_result(self):
        token = self.as_coach()
        game = self.make_event(kind="game", title="G")
        self.assertError(self.call("put", f"{EVENTS}/{game.pk}/result", token, {"score_for": 1, "score_against": 0}), 403, "forbidden")

    # --- attendance ---------------------------------------------------------

    def attendance_for(self, event, status="no_response"):
        row, _created = Attendance.objects.get_or_create(event=event, member=self.player)
        row.status = status
        row.coach_note = "knee"
        row.save()
        return row

    def test_attendance_list(self):
        token = self.as_manager()
        event = self.make_event()
        row = self.attendance_for(event, "maybe")
        body = self.call("get", f"{EVENTS}/{event.pk}/attendance", token).json()
        self.assertEqual(body["counts"]["maybe"], 1)
        self.assertEqual(body["counts"]["total"], sum(v for k, v in body["counts"].items() if k != "total"))
        self.assertEqual([item["attendance_id"] for item in body["items"]], [str(row.pk)])
        self.assertEqual(body["items"][0]["coach_note"], "knee")
        self.assertEqual(self.call("get", f"{EVENTS}/{event.pk}/attendance?status=present", token).json()["items"], [])

    def test_attendance_coach_note_hidden_from_non_managers(self):
        token = self.as_coach()
        # No team or group: visible to any staff, manageable by none of them.
        event = Event.objects.create(club=self.club, kind="meeting", title="Club social", start=self.start)
        Attendance.objects.create(event=event, member=self.player, coach_note="private")
        items = self.call("get", f"{EVENTS}/{event.pk}/attendance", token).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["coach_note"])

    def test_set_attendance_status(self):
        token = self.as_manager()
        event = self.make_event()
        row = self.attendance_for(event)
        url = f"{EVENTS}/{event.pk}/attendance/{row.pk}/status"
        response = self.call("post", url, token, {"status": "present", "coach_note": "confirmed by phone"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "present")
        row.refresh_from_db()
        self.assertEqual((row.status, row.coach_note, row.answered_by_coach, row.coach_set_by), ("present", "confirmed by phone", True, self.member))
        self.call("post", url, token, {"status": "no_response"})
        row.refresh_from_db()
        self.assertFalse(row.answered_by_coach)

    def test_set_attendance_status_errors(self):
        event = self.make_event()
        row = self.attendance_for(event)
        url = f"{EVENTS}/{event.pk}/attendance/{row.pk}/status"
        coach_token = self.as_coach()
        self.assertError(self.call("post", url, coach_token, {"status": "present"}), 403, "forbidden")
        StaffAssignment.objects.all().delete()
        token = self.as_manager()
        self.assertError(self.call("post", url, token, {"status": "excused"}), 422, "invalid_status")
        other = self.make_event(title="Other")
        self.assertError(self.call("post", f"{EVENTS}/{other.pk}/attendance/{row.pk}/status", token, {"status": "present"}), 404, "not_found")
        event.cancelled = True
        event.save()
        self.assertError(self.call("post", url, token, {"status": "present"}), 409, "event_cancelled")

    # --- series -------------------------------------------------------------

    def test_create_series_generates_occurrences(self):
        token = self.as_admin()
        response = self.call("post", f"{EVENTS}/series", token, self.series_payload())
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertGreater(body["occurrences_created"], 0)
        self.assertEqual((body["frequency"], body["interval"], body["weekdays"], body["duration_minutes"]), ("weekly", 1, ["TU"], 90))
        self.assertEqual(body["team_ids"], [str(self.team.pk)])
        self.assertTrue(body["can_manage"])
        series = EventSeries.objects.get(pk=body["id"])
        self.assertEqual(series.club, self.club)
        self.assertEqual(series.occurrences.count(), body["occurrences_created"])
        listed = self.call("get", f"{EVENTS}?series={series.pk}", token).json()["items"]
        self.assertEqual(len(listed), body["occurrences_created"])
        self.assertEqual(listed[0]["series"]["id"], str(series.pk))

    def test_series_list_query_count_is_constant(self):
        token = self.as_admin()
        extra = Member.objects.create(first_name="Eve", last_name="Extra")

        def make_series(n):
            for i in range(n):
                series = EventSeries.objects.create(club=self.club, kind="training", title=f"S{i}", dtstart=self.start, rrule="FREQ=WEEKLY;BYDAY=TU")
                series.teams.add(self.team)
                series.invited_members.add(self.player)
                series.excluded_members.add(extra)

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.call("get", f"{EVENTS}/series", token)
            self.assertEqual(response.status_code, 200, response.content)
            return len(ctx), response.json()["items"]

        make_series(1)
        few, _items = count_queries()
        make_series(4)
        many, items = count_queries()
        self.assertEqual(len(items), 5)
        self.assertEqual(items[0]["invited_member_ids"], [str(self.player.pk)])
        self.assertEqual(few, many)

    def test_create_series_validation(self):
        token = self.as_admin()
        response = self.call("post", f"{EVENTS}/series", token, self.series_payload(weekdays=[]))
        self.assertError(response, 422, "validation_error")
        self.assertIn("weekdays", response.json()["error"]["fields"])
        self.assertError(self.call("post", f"{EVENTS}/series", token, self.series_payload(frequency="daily")), 422, "validation_error")
        self.assertEqual(EventSeries.objects.count(), 0)

    def test_manager_series_needs_an_audience(self):
        token = self.as_manager()
        response = self.call("post", f"{EVENTS}/series", token, self.series_payload(team_ids=[]))
        self.assertError(response, 422, "validation_error")
        self.assertIn("non_field_errors", response.json()["error"]["fields"])

    def test_create_series_monthly_and_advanced(self):
        token = self.as_admin()
        response = self.call("post", f"{EVENTS}/series", token, self.series_payload(frequency="monthly", weekdays=[], interval=2))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["frequency"], response.json()["interval"]), ("monthly", 2))
        response = self.call("post", f"{EVENTS}/series", token, self.series_payload(advanced_rrule="FREQ=WEEKLY;BYDAY=MO,WE", weekdays=[]))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["rrule"], "FREQ=WEEKLY;BYDAY=MO,WE")

    def test_series_permissions_and_scope(self):
        token = self.as_coach()
        self.assertError(self.call("post", f"{EVENTS}/series", token, self.series_payload()), 403, "forbidden")
        rival = make_club(name="Rival FC", slug="rival-fc")
        foreign = EventSeries.objects.create(club=rival, title="Theirs", kind="training", dtstart=self.start, rrule="FREQ=WEEKLY;BYDAY=TU")
        admin = self.as_admin()
        for method, suffix in [("get", ""), ("patch", ""), ("delete", ""), ("post", "/stop"), ("post", "/generate")]:
            data = {"title": "x"} if method == "patch" else None
            self.assertError(self.call(method, f"{EVENTS}/series/{foreign.pk}{suffix}", admin, data), 404, "not_found")
        self.assertEqual(self.call("get", f"{EVENTS}/series", admin).json()["items"], [])

    def test_update_series_propagates(self):
        token = self.as_manager()
        created = self.call("post", f"{EVENTS}/series", token, self.series_payload()).json()
        url = f"{EVENTS}/series/{created['id']}"
        response = self.call("patch", url, token, {"title": "Evening training", "duration_minutes": 60, "location_id": str(self.location.pk)})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["title"], body["duration_minutes"], body["location_id"]), ("Evening training", 60, str(self.location.pk)))
        self.assertGreater(body["occurrences_updated"], 0)
        self.assertEqual(Event.objects.filter(series_id=created["id"], title="Evening training").count(), Event.objects.filter(series_id=created["id"]).count())
        self.assertEqual(self.call("get", url, token).json()["title"], "Evening training")
        self.assertEqual(len(self.call("get", f"{EVENTS}/series", token).json()["items"]), 1)

        self.assertError(self.call("patch", url, token, {"weekdays": []}), 422, "validation_error")
        response = self.call("patch", url, token, {"frequency": "weekly", "weekdays": ["MO", "TH"]})
        self.assertEqual(response.json()["weekdays"], ["MO", "TH"])

    def test_stop_generate_delete_series(self):
        token = self.as_admin()
        created = self.call("post", f"{EVENTS}/series", token, self.series_payload()).json()
        url = f"{EVENTS}/series/{created['id']}"
        count = Event.objects.filter(series_id=created["id"]).count()
        self.assertEqual(self.call("post", f"{url}/generate", token).json(), {"occurrences_created": 0})
        stopped = self.call("post", f"{url}/stop", token)
        self.assertEqual(stopped.status_code, 200, stopped.content)
        self.assertIsNotNone(stopped.json()["until"])
        self.assertEqual(Event.objects.filter(series_id=created["id"]).count(), count)
        self.assertEqual(self.call("delete", url, token).status_code, 204)
        self.assertEqual(Event.objects.filter(series_id=created["id"]).count(), 0)
        self.assertError(self.call("get", url, token), 404, "not_found")
