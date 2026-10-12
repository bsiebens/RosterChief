"""Phase 4 of the app API: coach endpoints under /teams/{team_id}/. Pat (the parent from
AppApiTestCase) is staff of "U16": a plain coach by default, a manager with ``manage()``."""

import datetime
import uuid

from django.utils import timezone

from events.models import Attendance, Event, Lineup
from events.services import detach_occurrence, generate_occurrences, propagate_series
from members.models import Member
from notifications.models import Notification
from rosterchief.test_support import make_club
from teams.models import Position, StaffAssignment, Team, TeamMembership

from .tests import CLUB_HOST, AppApiTestCase

Status = Attendance.AttendanceStatus


class CoachApiTestCase(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        self.coach_position = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        self.manager_position = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        self.player_position = Position.objects.create(club=self.club, name="Forward", short_name="F")
        self.assignment = StaffAssignment.objects.create(team=self.team, member=self.member, season=self.season, position=self.coach_position)
        self.soon = timezone.now() + datetime.timedelta(days=2)
        self._token = None

    def manage(self):
        """Make Pat a manager of the team (which also puts the account under the 2FA rule)."""
        self.assignment.position = self.manager_position
        self.assignment.save()
        self._token = self.login_with_totp()

    @property
    def token(self):
        if self._token is None:
            self._token = self.login()
        return self._token

    def get(self, path, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return self.api("get", f"{path}?{query}" if query else path, token=self.token, host=CLUB_HOST)

    def send(self, method, path, data=None, **headers):
        return self.api(method, path, token=self.token, host=CLUB_HOST, data=data, **headers)

    def player(self, first, last, *, jersey=None, status=Status.NO_RESPONSE, event=None):
        member = Member.objects.create(first_name=first, last_name=last)
        TeamMembership.objects.create(team=self.team, member=member, season=self.season, position=self.player_position, jersey_number=jersey)
        if event is not None:
            Attendance.objects.update_or_create(event=event, member=member, defaults={"status": status})
        return member

    def event(self, **kwargs):
        kwargs.setdefault("kind", Event.EventKind.TRAINING)
        event = Event.objects.create(club=self.club, title=kwargs.pop("title", "Training"), start=kwargs.pop("start", self.soon), **kwargs)
        event.teams.add(self.team)
        return event

    def url(self, path="", team=None):
        return f"/teams/{(team or self.team).pk}{path}"


class ContextTests(CoachApiTestCase):
    def test_staffed_team_carries_role_and_flags(self):
        item = self.get("/context").json()["staffed_teams"][0]

        self.assertEqual(item, {"id": str(self.team.pk), "name": "U16", "short_name": "U16", "role": "coach", "position": "Assistant", "can_manage_attendance": False, "can_edit_events": False, "can_publish_lineup": False, "can_edit_roster": False})

    def test_manager_gets_every_flag(self):
        self.manage()

        item = self.get("/context").json()["staffed_teams"][0]

        self.assertEqual(item["role"], "manager")
        self.assertEqual(item["position"], "Head coach")
        self.assertTrue(all(item[flag] for flag in ("can_manage_attendance", "can_edit_events", "can_publish_lineup", "can_edit_roster")))


class TwoFactorTests(CoachApiTestCase):
    def test_a_manager_without_two_factor_is_told_to_enrol(self):
        self.assignment.position = self.manager_position
        self.assignment.save()
        token = self.login()

        for path in ("/context", self.url("/today")):
            with self.subTest(path=path):
                self.assertError(self.api("get", path, token=token, host=CLUB_HOST), 403, "mfa_enrollment_required")

    def test_a_coach_without_a_management_position_needs_no_second_factor(self):
        self.assertEqual(self.get(self.url("/today")).status_code, 200)


class TeamScopeTests(CoachApiTestCase):
    def test_unstaffed_team_is_404_everywhere(self):
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")
        event = self.event()
        event.teams.add(other)

        for method, path in (
            ("get", "/today"),
            ("get", f"/events/{event.pk}/attendance"),
            ("post", f"/events/{event.pk}/check-ins"),
            ("post", f"/events/{event.pk}/remind-silent"),
        ):
            with self.subTest(path=path):
                body = {"items": []} if path.endswith("check-ins") else None
                self.assertError(self.send(method, self.url(path, team=other), body), 404, "not_found")

    def test_unknown_team_is_404(self):
        self.assertError(self.send("get", f"/teams/{uuid.uuid4()}/today"), 404, "not_found")

    def test_staff_of_a_past_season_is_not_staff(self):
        self.assignment.delete()

        self.assertError(self.send("get", self.url("/today")), 404, "not_found")

    def test_event_of_another_team_is_404(self):
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")
        elsewhere = Event.objects.create(club=self.club, title="Elsewhere", start=self.soon)
        elsewhere.teams.add(other)

        self.assertError(self.get(self.url(f"/events/{elsewhere.pk}/attendance")), 404, "not_found")

    def test_requires_a_club_tie(self):
        stranger_token = self.api("get", "/context", token=self.token, host="rival-fc.rosterchief.app")
        self.assertEqual(stranger_token.status_code, 404)  # no such club host in this fixture


class TodayTests(CoachApiTestCase):
    def test_nothing_coming_up(self):
        self.player("Ann", "Aal")

        body = self.get(self.url("/today")).json()

        self.assertEqual(body["team"]["name"], "U16")
        self.assertEqual(body["squad_count"], 1)
        self.assertIsNone(body["event"])
        self.assertIsNone(body["closes_at"])
        self.assertEqual((body["in_count"], body["out_count"], body["silent_count"]), (0, 0, 0))
        self.assertEqual(body["needs_you"], [])

    def test_counts_deadline_and_silent_players(self):
        deadline = self.soon - datetime.timedelta(hours=6)
        event = self.event(deadline=deadline)
        self.player("Ann", "Aal", status=Status.PRESENT, event=event)
        self.player("Bo", "Bos", status=Status.MAYBE, event=event)
        self.player("Cy", "Cox", status=Status.ABSENT, event=event)
        silent = self.player("Di", "Dam", event=event)

        body = self.get(self.url("/today")).json()

        self.assertEqual(body["event"]["id"], str(event.pk))
        self.assertEqual(body["squad_count"], 4)
        self.assertEqual((body["in_count"], body["out_count"], body["silent_count"]), (2, 1, 1))
        self.assertLess(abs(datetime.datetime.fromisoformat(body["closes_at"]) - event.deadline), datetime.timedelta(milliseconds=1))
        self.assertEqual(body["needs_you"], [{"type": "silent_players", "event_id": str(event.pk), "title": "Training", "start": body["needs_you"][0]["start"], "count": 1}])
        self.assertTrue(Attendance.objects.filter(member=silent, status=Status.NO_RESPONSE).exists())

    def test_todays_session_beats_an_earlier_listed_future_one(self):
        later = self.event(title="Later", start=timezone.now() + datetime.timedelta(days=5))
        today_event = self.event(title="Tonight", start=timezone.now() + datetime.timedelta(minutes=30))

        body = self.get(self.url("/today")).json()

        self.assertEqual(body["event"]["id"], str(today_event.pk))
        self.assertTrue(body["is_today"] or timezone.localdate() != timezone.localtime(today_event.start).date())
        self.assertNotEqual(body["event"]["id"], str(later.pk))

    def test_unpublished_lineups_are_listed_for_games_only(self):
        game = self.event(title="Derby", kind=Event.EventKind.GAME)
        published = self.event(title="Cup", kind=Event.EventKind.GAME, start=self.soon + datetime.timedelta(days=1))
        Lineup.objects.create(event=published, team=self.team, published_at=timezone.now())
        self.event(title="Practice", start=self.soon + datetime.timedelta(days=2))

        needs = self.get(self.url("/today")).json()["needs_you"]

        self.assertEqual([(item["type"], item["event_id"], item["count"]) for item in needs], [("unpublished_lineup", str(game.pk), None)])

    def test_cancelled_events_are_skipped(self):
        self.event(cancelled=True)

        self.assertIsNone(self.get(self.url("/today")).json()["event"])


class AttendanceTests(CoachApiTestCase):
    def setUp(self):
        super().setUp()
        self.game = self.event(kind=Event.EventKind.GAME)
        self.ann = self.player("Ann", "Aal", jersey=7, status=Status.PRESENT, event=self.game)
        self.bo = self.player("Bo", "Bos", jersey=9, status=Status.ABSENT, event=self.game)
        self.cy = self.player("Cy", "Cox", status=Status.NO_RESPONSE, event=self.game)
        Attendance.objects.filter(member=self.bo).update(note="Flu")

    def rows(self, **params):
        return self.get(self.url(f"/events/{self.game.pk}/attendance"), **params).json()

    def test_default_lists_who_responded_with_everything_the_app_needs(self):
        body = self.rows()

        self.assertEqual(body["filter"], "responded")
        self.assertEqual(body["counts"], {"total": 3, "responded": 1, "silent": 1, "declined": 1, "checked_in": 0})
        [row] = body["items"]
        self.assertEqual(
            row,
            {
                "attendance_id": str(Attendance.objects.get(member=self.ann).pk),
                "member_id": str(self.ann.pk),
                "first_name": "Ann",
                "last_name": "Aal",
                "jersey_number": 7,
                "position": "Forward",
                "status": "present",
                "note": "",
                "showed_up": None,
                "photo_url": None,
            },
        )

    def test_filters(self):
        self.assertEqual([r["first_name"] for r in self.rows(filter="silent")["items"]], ["Cy"])
        declined = self.rows(filter="declined")["items"]
        self.assertEqual([(r["first_name"], r["note"]) for r in declined], [("Bo", "Flu")])
        self.assertEqual([r["first_name"] for r in self.rows(filter="all")["items"]], ["Ann", "Bo", "Cy"])

    def test_unknown_filter_is_a_validation_error(self):
        self.assertError(self.get(self.url(f"/events/{self.game.pk}/attendance"), filter="nope"), 422, "validation_error")

    def test_unplaced_player_has_no_jersey_or_position(self):
        TeamMembership.objects.filter(member=self.ann).update(jersey_number=None, position=None)

        row = self.rows()["items"][0]

        self.assertIsNone(row["jersey_number"])
        self.assertEqual(row["position"], "")

    def test_photo_url_only_when_the_account_may_see_it(self):
        Member.objects.filter(pk=self.ann.pk).update(photo="members/ann.jpg", photo_public_consent=True)
        Member.objects.filter(pk=self.cy.pk).update(photo="members/cy.jpg")

        silent = self.rows(filter="silent")["items"][0]
        responded = self.rows()["items"][0]

        self.assertIn(f"/api/app/v1/members/{self.ann.pk}/photo?v=", responded["photo_url"])
        self.assertIn(f"/api/app/v1/members/{self.cy.pk}/photo?v=", silent["photo_url"])  # team staff may see their players

    def test_photo_url_is_null_for_someone_the_account_may_not_see(self):
        guest = Member.objects.create(first_name="Gus", last_name="Guest", photo="members/gus.jpg")
        Attendance.objects.create(event=self.game, member=guest, status=Status.PRESENT)

        row = next(row for row in self.rows()["items"] if row["first_name"] == "Gus")

        self.assertIsNone(row["photo_url"])

    def test_a_coach_without_a_management_position_can_read(self):
        self.assertEqual(self.get(self.url(f"/events/{self.game.pk}/attendance")).status_code, 200)


class CheckInTests(CoachApiTestCase):
    def setUp(self):
        super().setUp()
        self.manage()
        self.game = self.event()
        self.ann = self.player("Ann", "Aal", status=Status.PRESENT, event=self.game)
        self.bo = self.player("Bo", "Bos", status=Status.PRESENT, event=self.game)
        self.rows = {a.member_id: a for a in Attendance.objects.filter(event=self.game)}

    def check_in(self, *items, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        path = self.url(f"/events/{self.game.pk}/check-ins") + (f"?{query}" if query else "")
        return self.send("post", path, {"items": [{"attendance_id": str(self.rows[member.pk].pk), "showed_up": value} for member, value in items]})

    def test_partial_list_records_only_those_rows_and_returns_the_sheet(self):
        response = self.check_in((self.ann, True))

        self.assertEqual(response.status_code, 200, response.content)
        by_name = {row["first_name"]: row["showed_up"] for row in response.json()["items"]}
        self.assertEqual(by_name, {"Ann": True, "Bo": None})
        self.assertEqual(response.json()["counts"]["checked_in"], 1)

    def test_is_idempotent_and_null_clears(self):
        self.check_in((self.ann, True), (self.bo, False))
        self.check_in((self.ann, True), (self.bo, False))
        self.assertEqual({a.member_id: a.showed_up for a in Attendance.objects.filter(event=self.game)}, {self.ann.pk: True, self.bo.pk: False})

        self.check_in((self.bo, None))

        self.assertIsNone(Attendance.objects.get(event=self.game, member=self.bo).showed_up)

    def test_a_row_of_another_event_fails_the_whole_call(self):
        other = self.event(title="Other")
        foreign = Attendance.objects.get(event=other, member=self.ann)
        response = self.send("post", self.url(f"/events/{self.game.pk}/check-ins"), {"items": [{"attendance_id": str(self.rows[self.ann.pk].pk), "showed_up": True}, {"attendance_id": str(foreign.pk), "showed_up": True}]})

        self.assertError(response, 422, "unknown_attendance")
        self.assertEqual(response.json()["error"]["fields"], {"items": [str(foreign.pk)]})
        self.assertIsNone(Attendance.objects.get(pk=self.rows[self.ann.pk].pk).showed_up)

    def test_a_coach_without_a_management_position_is_refused(self):
        self.assignment.position = self.coach_position
        self.assignment.save()

        self.assertError(self.check_in((self.ann, True)), 403, "manager_required")
        self.assertIsNone(Attendance.objects.get(pk=self.rows[self.ann.pk].pk).showed_up)


class RemindSilentTests(CoachApiTestCase):
    def setUp(self):
        super().setUp()
        self.manage()
        self.game = self.event(title="Derby")
        self.player("Ann", "Aal", status=Status.PRESENT, event=self.game)
        self.silent = self.player("Cy", "Cox", event=self.game)

    def test_notifies_the_silent_players_with_the_event_as_target(self):
        before = Notification.objects.count()
        response = self.send("post", self.url(f"/events/{self.game.pk}/remind-silent"))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"notified": 1})
        [notification] = Notification.objects.filter(member=self.silent).order_by("-created")[:1]
        self.assertEqual(Notification.objects.count(), before + 1)
        self.assertEqual(notification.title, "Derby")
        self.assertEqual(notification.source, self.game)
        self.assertIn("still needs your answer", notification.body)

    def test_nobody_silent(self):
        Attendance.objects.update(status=Status.PRESENT)

        self.assertEqual(self.send("post", self.url(f"/events/{self.game.pk}/remind-silent")).json(), {"notified": 0})

    def test_idempotency_key_stops_a_double_reminder(self):
        path = self.url(f"/events/{self.game.pk}/remind-silent")
        before = Notification.objects.count()

        first = self.send("post", path, HTTP_IDEMPOTENCY_KEY="remind-1")
        second = self.send("post", path, HTTP_IDEMPOTENCY_KEY="remind-1")

        self.assertEqual(first.json(), second.json())
        self.assertEqual(Notification.objects.count(), before + 1)

    def test_a_coach_without_a_management_position_is_refused(self):
        self.assignment.position = self.coach_position
        self.assignment.save()

        before = Notification.objects.count()

        self.assertError(self.send("post", self.url(f"/events/{self.game.pk}/remind-silent")), 403, "manager_required")
        self.assertEqual(Notification.objects.count(), before)


# --- slice 2: events and series ------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

from events.models import EventSeries, Location, Opponent  # noqa: E402


class CoachEventTestCase(CoachApiTestCase):
    def setUp(self):
        super().setUp()
        self.manage()
        self.when = (timezone.now() + datetime.timedelta(days=7)).replace(microsecond=0)
        self.rink = Location.objects.create(club=self.club, name="Home rink", address="1 Ice Rd", city="Antwerp", zip_code="2000", country="BE", is_home=True)
        self.away = Location.objects.create(club=self.club, name="Their rink", address="2 Cold St", city="Ghent", zip_code="9000", country="BE")
        self.ajax = Opponent.objects.create(club=self.club, name="Ajax")

    def stamp(self, value):
        return value.isoformat().replace("+00:00", "Z")

    def create(self, **body):
        body.setdefault("kind", "training")
        body.setdefault("start", self.stamp(self.when))
        body.setdefault("notify", False)
        return self.send("post", self.url("/events"), body)


class CoachEventCreateTests(CoachEventTestCase):
    def test_creates_a_training_for_the_team_and_invites_the_roster(self):
        ann = self.player("Ann", "Aal")

        response = self.create(end=self.stamp(self.when + datetime.timedelta(hours=1)), location_id=str(self.rink.pk))

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["title"], body["kind"], body["location_id"]), ("Training", "training", str(self.rink.pk)))
        self.assertEqual([team["id"] for team in body["teams"]], [str(self.team.pk)])
        self.assertIsNone(body["series"])
        event = Event.objects.get(pk=body["id"])
        self.assertEqual(event.created_by, self.member)
        self.assertEqual(Attendance.objects.get(event=event, member=ann).status, Status.NO_RESPONSE)

    def test_a_game_is_named_after_the_match_up_and_flips_for_an_away_location(self):
        home = self.create(kind="game", opponent_id=str(self.ajax.pk), location_id=str(self.rink.pk)).json()
        away = self.create(kind="game", opponent_id=str(self.ajax.pk), location_id=str(self.away.pk)).json()

        self.assertEqual(home["title"], "U16 vs Ajax")
        self.assertEqual(away["title"], "Ajax vs U16")
        self.assertEqual(home["opponent"], "Ajax")

    def test_a_typed_title_is_kept_for_a_training(self):
        self.assertEqual(self.create(title="Goalie session").json()["title"], "Goalie session")

    def test_notify_tells_the_roster_in_the_background(self):
        with patch("appapi.routers.coach_events.dispatch_notify_new_event") as dispatch:
            event_id = self.create(notify=True).json()["id"]

        dispatch.assert_called_once_with(event_id)

    def test_validation_errors_use_the_json_names(self):
        stranger = Member.objects.create(first_name="Sue", last_name="Stranger")

        response = self.create(competition="No such cup", invited_member_ids=[str(stranger.pk)], location_id=str(uuid.uuid4()))

        self.assertError(response, 422, "validation_error")
        self.assertEqual(set(response.json()["error"]["fields"]), {"competition", "invited_member_ids", "location_id"})

    def test_a_kind_the_coach_screens_do_not_offer_is_refused(self):
        self.assertError(self.create(kind="social"), 422, "validation_error")

    def test_an_idempotency_key_creates_one_event(self):
        first = self.send("post", self.url("/events"), {"kind": "training", "start": self.stamp(self.when), "notify": False}, HTTP_IDEMPOTENCY_KEY="create-1")
        second = self.send("post", self.url("/events"), {"kind": "training", "start": self.stamp(self.when), "notify": False}, HTTP_IDEMPOTENCY_KEY="create-1")

        self.assertEqual(first.json(), second.json())
        self.assertEqual(Event.objects.count(), 1)

    def test_a_coach_without_a_management_position_may_read_but_not_write(self):
        self.assignment.position = self.coach_position
        self.assignment.save()
        event = self.event()

        self.assertError(self.create(), 403, "manager_required")
        self.assertError(self.send("patch", self.url(f"/events/{event.pk}"), {"title": "X"}), 403, "manager_required")
        self.assertError(self.send("delete", self.url(f"/events/{event.pk}")), 403, "manager_required")
        self.assertError(self.send("post", self.url("/locations"), {"name": "R", "address": "a", "city": "c", "zip_code": "1"}), 403, "manager_required")
        self.assertEqual(self.get(self.url(f"/events/{event.pk}")).status_code, 200)
        self.assertEqual(self.get(self.url("/events")).status_code, 200)


class CoachEventReadTests(CoachEventTestCase):
    def test_list_pages_by_start_and_includes_cancelled(self):
        first = self.event(title="One", start=self.when)
        second = self.event(title="Two", start=self.when + datetime.timedelta(days=1), cancelled=True)
        self.event(title="Three", start=self.when + datetime.timedelta(days=2))
        elsewhere = Event.objects.create(club=self.club, title="Elsewhere", start=self.when)

        page = self.get(self.url("/events"), limit=2).json()

        self.assertEqual([item["id"] for item in page["items"]], [str(first.pk), str(second.pk)])
        self.assertTrue(page["items"][1]["cancelled"])
        rest = self.get(self.url("/events"), limit=2, cursor=page["next_cursor"]).json()
        self.assertEqual([item["title"] for item in rest["items"]], ["Three"])
        self.assertIsNone(rest["next_cursor"])
        self.assertNotIn(str(elsewhere.pk), [item["id"] for item in page["items"] + rest["items"]])

    def test_list_filters_by_kind_and_looks_back_with_from(self):
        past = self.event(title="Old game", kind="game", start=timezone.now() - datetime.timedelta(days=3))
        self.event(title="Practice", start=self.when)

        items = self.get(self.url("/events"), kind="game", **{"from": self.stamp(timezone.now() - datetime.timedelta(days=10))}).json()["items"]

        self.assertEqual([item["id"] for item in items], [str(past.pk)])

    def test_detail_carries_what_editing_needs(self):
        guest = self.player("Gus", "Guest")
        event = self.event(title="Derby", kind="game", location=self.rink, opponent=self.ajax, deadline=self.when - datetime.timedelta(hours=5), competition="", is_friendly=True)
        event.excluded_members.add(guest)

        body = self.get(self.url(f"/events/{event.pk}")).json()

        self.assertEqual((body["location_id"], body["opponent_id"], body["is_friendly"], body["excluded_member_ids"]), (str(self.rink.pk), str(self.ajax.pk), True, [str(guest.pk)]))
        self.assertEqual((body["max_referees"], body["max_officials"]), (2, None))
        self.assertIsNotNone(body["deadline"])

    def test_event_of_another_team_is_404(self):
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")
        foreign = Event.objects.create(club=self.club, title="Foreign", start=self.when)
        foreign.teams.add(other)

        for method in ("get", "patch", "delete"):
            with self.subTest(method=method):
                self.assertError(self.send(method, self.url(f"/events/{foreign.pk}"), {} if method == "patch" else None), 404, "not_found")
        self.assertError(self.send("post", self.url(f"/events/{foreign.pk}/detach")), 404, "not_found")


class CoachEventUpdateTests(CoachEventTestCase):
    def series(self, occurrences=3):
        series = EventSeries.objects.create(
            club=self.club, title="Practice", kind="training", rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=" + ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][self.when.weekday()], dtstart=self.when, duration=datetime.timedelta(hours=1), location=self.rink
        )
        series.teams.add(self.team)
        events = generate_occurrences(series, until=self.when + datetime.timedelta(days=7 * (occurrences - 1), hours=1))
        return series, sorted(events, key=lambda event: event.start)

    def patch(self, event, **body):
        return self.send("patch", self.url(f"/events/{event.pk}"), body)

    def test_changes_only_what_is_sent(self):
        event = self.event(title="Practice", location=self.rink)

        response = self.patch(event, title="Special practice", location_id=None, deadline=self.stamp(self.when - datetime.timedelta(hours=2)))

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()["event"]
        self.assertEqual((body["title"], body["location_id"], body["kind"]), ("Special practice", None, "training"))
        self.assertIsNotNone(body["deadline"])
        self.assertEqual(response.json()["occurrences_updated"], 0)

    def test_scores_and_live_flag_are_editable(self):
        game = self.event(kind="game", title="Derby")

        body = self.patch(game, score_for=3, score_against=1, is_live=True).json()["event"]

        self.assertEqual((body["score_for"], body["score_against"], body["is_live"]), (3, 1, True))

    def test_validation_error_names_the_field(self):
        event = self.event()

        response = self.patch(event, competition="No such cup")

        self.assertError(response, 422, "validation_error")
        self.assertIn("competition", response.json()["error"]["fields"])

    def test_a_multi_team_event_keeps_the_teams_the_coach_does_not_manage(self):
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")
        event = self.event()
        event.teams.add(other)

        self.patch(event, title="Joint practice")

        self.assertEqual(set(event.teams.values_list("pk", flat=True)), {self.team.pk, other.pk})

    def test_scope_this_detaches_the_occurrence_when_a_series_field_changes(self):
        series, [_first, second, _third] = self.series()

        body = self.patch(second, title="One-off name").json()["event"]

        self.assertTrue(body["series"]["detached"])
        propagate_series(series)
        second.refresh_from_db()
        self.assertEqual(second.title, "One-off name")

    def test_scope_this_leaves_it_attached_when_only_a_result_changes(self):
        _series, [first, *_rest] = self.series()

        self.assertFalse(self.patch(first, score_for=2).json()["event"]["series"]["detached"])

    def test_scope_all_changes_every_attached_upcoming_occurrence(self):
        series, [first, second, third] = self.series()
        detached = second
        detach_occurrence(detached)

        response = self.patch(first, scope="all", title="New name", location_id=str(self.away.pk))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["occurrences_updated"], 1)  # the third; the second is detached
        for event, expected in ((first, "New name"), (second, "Practice"), (third, "New name")):
            event.refresh_from_db()
            self.assertEqual(event.title, expected)
        series.refresh_from_db()
        self.assertEqual((series.title, series.location_id), ("New name", self.away.pk))

    def test_scope_all_derives_the_durations_from_the_edited_occurrence(self):
        series, [first, second, _third] = self.series()

        self.patch(first, scope="all", end=self.stamp(first.start + datetime.timedelta(minutes=45)), deadline=self.stamp(first.start - datetime.timedelta(hours=3)))

        series.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(series.duration, datetime.timedelta(minutes=45))
        self.assertEqual(series.deadline_offset, datetime.timedelta(hours=3))
        self.assertEqual(second.end - second.start, datetime.timedelta(minutes=45))

    def test_scope_this_and_following_splits_the_series(self):
        series, [first, second, third] = self.series()

        response = self.patch(second, scope="this_and_following", title="Evening practice")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["occurrences_updated"], 1)
        for event in (first, second, third):
            event.refresh_from_db()
        self.assertEqual((first.title, second.title, third.title), ("Practice", "Evening practice", "Evening practice"))
        self.assertEqual(first.series_id, series.pk)
        self.assertEqual(second.series_id, third.series_id)
        self.assertNotEqual(second.series_id, series.pk)
        series.refresh_from_db()
        self.assertLess(series.until, second.start)
        new = EventSeries.objects.get(pk=second.series_id)
        self.assertEqual((new.dtstart, new.title), (second.start, "Evening practice"))
        self.assertEqual(set(new.teams.all()), {self.team})
        # the old series no longer generates past its cut
        self.assertEqual(generate_occurrences(series, until=self.when + datetime.timedelta(days=60)), [])

    def test_a_scope_other_than_this_needs_a_series(self):
        event = self.event()

        for scope in ("all", "this_and_following"):
            with self.subTest(scope=scope):
                self.assertError(self.patch(event, scope=scope, title="X"), 422, "not_in_series")

    def test_notify_tells_the_invited(self):
        event = self.event(title="Practice")
        ann = self.player("Ann", "Aal", status=Status.PRESENT, event=event)
        before = Notification.objects.count()

        self.patch(event, start=self.stamp(self.when + datetime.timedelta(hours=1)), notify=True)

        [notification] = Notification.objects.filter(member=ann).order_by("-created")[:1]
        self.assertEqual(Notification.objects.count(), before + 1)
        self.assertEqual(notification.source, event)
        self.assertIn("changed", notification.body)

    def test_detach_endpoint(self):
        _series, [first, *_rest] = self.series()

        body = self.send("post", self.url(f"/events/{first.pk}/detach")).json()

        self.assertTrue(body["series"]["detached"])

    def test_detach_needs_a_series(self):
        self.assertError(self.send("post", self.url(f"/events/{self.event().pk}/detach")), 422, "not_in_series")


class CoachEventDeleteTests(CoachEventTestCase):
    def test_a_one_off_event_is_deleted(self):
        event = self.event()

        self.assertEqual(self.send("delete", self.url(f"/events/{event.pk}")).status_code, 204)

        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_an_occurrence_is_removed_and_not_regenerated(self):
        series = EventSeries.objects.create(club=self.club, title="P", kind="training", rrule="FREQ=DAILY", dtstart=self.when)
        series.teams.add(self.team)
        [first, _second] = sorted(generate_occurrences(series, until=self.when + datetime.timedelta(days=1)), key=lambda e: e.start)

        self.send("delete", self.url(f"/events/{first.pk}"))

        self.assertFalse(Event.objects.filter(pk=first.pk).exists())
        series.refresh_from_db()
        self.assertIn(first.start.isoformat(), series.excluded_dates)
        self.assertEqual(generate_occurrences(series, until=self.when + datetime.timedelta(days=1)), [])

    def test_keep_record_marks_it_cancelled_and_notify_links_the_event(self):
        series = EventSeries.objects.create(club=self.club, title="P", kind="training", rrule="FREQ=DAILY", dtstart=self.when)
        series.teams.add(self.team)
        [occurrence] = generate_occurrences(series, until=self.when)
        ann = self.player("Ann", "Aal", status=Status.PRESENT, event=occurrence)

        self.send("delete", self.url(f"/events/{occurrence.pk}?keep_record=true&notify=true"))

        occurrence.refresh_from_db()
        self.assertTrue(occurrence.cancelled)
        notification = Notification.objects.filter(member=ann).latest("created")
        self.assertEqual(notification.source, occurrence)
        self.assertIn("cancelled", notification.body)

    def test_deleting_with_notify_points_the_notification_at_the_calendar(self):
        event = self.event()
        ann = self.player("Ann", "Aal", status=Status.PRESENT, event=event)

        self.send("delete", self.url(f"/events/{event.pk}?notify=true"))

        notification = Notification.objects.filter(member=ann).latest("created")
        self.assertIsNone(notification.source)
        self.assertEqual(notification.target_type, "calendar")


class CoachSeriesTests(CoachEventTestCase):
    def body(self, **overrides):
        body = {
            "title": "Tuesday practice",
            "kind": "training",
            "dtstart": self.stamp(self.when),
            "frequency": "weekly",
            "weekdays": [["MO", "TU", "WE", "TH", "FR", "SA", "SU"][self.when.weekday()]],
            "duration_minutes": 90,
            "deadline_minutes_before": 120,
            "location_id": str(self.rink.pk),
        }
        return {**body, **overrides}

    def test_create_generates_the_next_months(self):
        ann = self.player("Ann", "Aal")

        response = self.send("post", self.url("/series"), self.body())

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["frequency"], body["interval"], body["duration_minutes"], body["deadline_minutes_before"]), ("weekly", 1, 90, 120))
        self.assertGreaterEqual(body["occurrences_created"], 12)
        series = EventSeries.objects.get(pk=body["id"])
        first = series.occurrences.order_by("start").first()
        self.assertEqual((first.end - first.start, first.start - first.deadline), (datetime.timedelta(minutes=90), datetime.timedelta(hours=2)))
        self.assertTrue(Attendance.objects.filter(event=first, member=ann).exists())
        self.assertEqual(set(series.teams.all()), {self.team})

    def test_weekly_needs_a_weekday(self):
        response = self.send("post", self.url("/series"), self.body(weekdays=[]))

        self.assertError(response, 422, "validation_error")
        self.assertIn("weekdays", response.json()["error"]["fields"])

    def test_get_patch_and_delete(self):
        series_id = self.send("post", self.url("/series"), self.body()).json()["id"]
        before = EventSeries.objects.get(pk=series_id).occurrences.count()

        read = self.get(self.url(f"/series/{series_id}")).json()
        patched = self.send("patch", self.url(f"/series/{series_id}"), {"title": "Renamed", "duration_minutes": 60})

        self.assertEqual(read["title"], "Tuesday practice")
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json()["title"], "Renamed")
        self.assertGreaterEqual(patched.json()["occurrences_updated"], 11)
        upcoming = EventSeries.objects.get(pk=series_id).occurrences.order_by("start").last()
        self.assertEqual((upcoming.title, upcoming.end - upcoming.start), ("Renamed", datetime.timedelta(minutes=60)))
        self.assertEqual(self.send("delete", self.url(f"/series/{series_id}")).status_code, 204)
        self.assertEqual(Event.objects.filter(series_id=series_id).count(), 0)
        self.assertGreater(before, 0)

    def test_stopping_a_series_sets_until(self):
        series_id = self.send("post", self.url("/series"), self.body()).json()["id"]

        response = self.send("patch", self.url(f"/series/{series_id}"), {"until": self.stamp(self.when + datetime.timedelta(days=20))})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.json()["until"])

    def test_series_of_another_team_is_404(self):
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")
        series = EventSeries.objects.create(club=self.club, title="Other", kind="training", rrule="FREQ=DAILY", dtstart=self.when)
        series.teams.add(other)

        for method in ("get", "patch", "delete"):
            with self.subTest(method=method):
                self.assertError(self.send(method, self.url(f"/series/{series.pk}"), {} if method == "patch" else None), 404, "not_found")


class CoachPickerTests(CoachEventTestCase):
    def test_lists_are_searchable(self):
        self.assertEqual([item["name"] for item in self.get(self.url("/locations"), q="Ghent").json()["items"]], ["Their rink"])
        self.assertEqual([item["name"] for item in self.get(self.url("/opponents")).json()["items"]], ["Ajax"])
        self.assertEqual(self.get(self.url("/opponents"), q="zzz").json()["items"], [])

    def test_quick_create_a_location(self):
        response = self.send("post", self.url("/locations"), {"name": "New rink", "address": "3 Frost Ln", "city": "Bruges", "zip_code": "8000"})

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["country"], response.json()["is_home"]), ("BE", False))
        self.assertTrue(Location.objects.filter(club=self.club, name="New rink").exists())

    def test_a_bad_country_is_a_validation_error(self):
        response = self.send("post", self.url("/locations"), {"name": "X", "address": "a", "city": "c", "zip_code": "1", "country": "ZZ"})

        self.assertError(response, 422, "validation_error")
        self.assertIn("country", response.json()["error"]["fields"])

    def test_quick_create_an_opponent_once_per_key(self):
        first = self.send("post", self.url("/opponents"), {"name": "Rivals"}, HTTP_IDEMPOTENCY_KEY="opp-1")
        second = self.send("post", self.url("/opponents"), {"name": "Rivals"}, HTTP_IDEMPOTENCY_KEY="opp-1")

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(Opponent.objects.filter(name="Rivals").count(), 1)

    def test_other_clubs_data_is_not_listed(self):
        rival = make_club(name="Rival FC", slug="rival-fc")
        Opponent.objects.create(club=rival, name="Secret")

        self.assertNotIn("Secret", [item["name"] for item in self.get(self.url("/opponents")).json()["items"]])


class CoachNotificationTranslationTests(CoachEventTestCase):
    def test_change_and_cancel_notices_come_in_dutch_too(self):
        event = self.event(title="Practice")
        ann = self.player("Ann", "Aal", status=Status.PRESENT, event=event)

        self.send("patch", self.url(f"/events/{event.pk}"), {"title": "Practice", "notify": True})
        self.send("delete", self.url(f"/events/{event.pk}?notify=true"))

        changed, cancelled = Notification.objects.filter(member=ann, title="Practice").order_by("created")
        self.assertIn("gewijzigd", changed.translations["nl"]["body"])
        self.assertIn("changed", changed.translations["en"]["body"])
        self.assertIn("geannuleerd", cancelled.translations["nl"]["body"])
