"""Phase 4 of the app API: coach endpoints under /teams/{team_id}/. Pat (the parent from
AppApiTestCase) is staff of "U16": a plain coach by default, a manager with ``manage()``."""

import datetime
import uuid

from django.utils import timezone

from club.models import Season
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


# --- slice 3: lineup -----------------------------------------------------------------------


class CoachLineupTestCase(CoachEventTestCase):
    def setUp(self):
        super().setUp()
        self.game = self.event(kind="game", title="Derby", start=timezone.now() + datetime.timedelta(days=3))
        self.ann = self.player("Ann", "Aal", jersey=7, status=Status.PRESENT, event=self.game)
        self.bo = self.player("Bo", "Bos", jersey=9, status=Status.MAYBE, event=self.game)
        self.cy = self.player("Cy", "Cox", status=Status.ABSENT, event=self.game)
        self.di = self.player("Di", "Dam", status=Status.NO_RESPONSE, event=self.game)
        self.path = self.url(f"/events/{self.game.pk}/lineup")

    def put(self, *members):
        return self.send("put", self.path, {"selected_member_ids": [str(member.pk) for member in members]})

    def publish(self, mode="now", **extra):
        return self.send("post", self.path + "/publish", {"mode": mode, **extra})

    def rows(self, body):
        return {row["first_name"]: row for row in body["members"]}


class LineupReadTests(CoachLineupTestCase):
    def test_reading_creates_nothing(self):
        body = self.get(self.path).json()

        self.assertEqual((body["status"], body["selected_count"], body["published_at"]), ("none", 0, None))
        self.assertFalse(Lineup.objects.exists())

    def test_every_invited_player_comes_with_their_answer(self):
        rows = self.rows(self.get(self.path).json())

        self.assertEqual(set(rows), {"Ann", "Bo", "Cy", "Di"})
        ann = rows["Ann"]
        self.assertEqual((ann["jersey_number"], ann["position"], ann["rsvp_status"], ann["available"], ann["selected"], ann["can_drop_out"]), (7, "Forward", "present", True, False, False))
        self.assertEqual([rows[name]["available"] for name in ("Bo", "Cy", "Di")], [True, False, False])

    def test_members_are_ordered_by_position_then_name(self):
        TeamMembership.objects.filter(member=self.ann).update(position=None)

        names = [row["first_name"] for row in self.get(self.path).json()["members"]]

        self.assertEqual(names[-1], "Ann")  # no position sorts last

    def test_a_coach_without_a_management_position_may_read(self):
        self.assignment.position = self.coach_position
        self.assignment.save()

        self.assertEqual(self.get(self.path).status_code, 200)

    def test_other_kinds_and_other_teams_are_404(self):
        practice = self.event(title="Practice")
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")
        foreign = Event.objects.create(club=self.club, title="Foreign", kind="game", start=self.soon)
        foreign.teams.add(other)

        for event in (practice, foreign):
            with self.subTest(event=event.title):
                self.assertError(self.get(self.url(f"/events/{event.pk}/lineup")), 404, "not_found")
                self.assertError(self.send("put", self.url(f"/events/{event.pk}/lineup"), {"selected_member_ids": []}), 404, "not_found")
                self.assertError(self.send("post", self.url(f"/events/{event.pk}/lineup/publish"), {"mode": "now"}), 404, "not_found")

    def test_a_tournament_has_a_lineup_too(self):
        tournament = self.event(kind="tournament", title="Cup")

        self.assertEqual(self.get(self.url(f"/events/{tournament.pk}/lineup")).status_code, 200)


class LineupSaveTests(CoachLineupTestCase):
    def test_put_replaces_the_set_and_returns_the_saved_state(self):
        first = self.put(self.ann, self.bo)
        second = self.put(self.bo)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.json()["selected_count"], 1)
        rows = self.rows(second.json())
        self.assertEqual((rows["Ann"]["selected"], rows["Bo"]["selected"]), (False, True))
        self.assertEqual(second.json()["status"], "draft")
        self.assertEqual(second.json()["pending"], {"selected_member_ids": [str(self.bo.pk)], "dropped_member_ids": []})

    def test_saving_notifies_nobody_and_changes_no_answers(self):
        before = Notification.objects.count()

        self.put(self.ann)

        self.assertEqual(Notification.objects.count(), before)
        self.assertEqual(Attendance.objects.get(event=self.game, member=self.ann).status, Status.PRESENT)

    def test_unavailable_or_uninvited_players_fail_the_whole_call(self):
        stranger = Member.objects.create(first_name="Sue", last_name="Stranger")

        response = self.put(self.ann, self.cy, self.di, stranger)

        self.assertError(response, 422, "invalid_selection")
        self.assertEqual(set(response.json()["error"]["fields"]["selected_member_ids"]), {str(self.cy.pk), str(self.di.pk), str(stranger.pk)})
        self.assertFalse(Lineup.objects.exists())

    def test_an_empty_list_clears_the_picks(self):
        self.put(self.ann)

        self.assertEqual(self.put().json()["selected_count"], 0)

    def test_a_coach_without_a_management_position_is_refused(self):
        self.assignment.position = self.coach_position
        self.assignment.save()

        self.assertError(self.put(self.ann), 403, "manager_required")
        self.assertError(self.publish(), 403, "manager_required")


class LineupPublishTests(CoachLineupTestCase):
    def test_publish_selects_and_deselects_and_says_who_was_told(self):
        self.put(self.ann)
        before = Notification.objects.count()

        response = self.publish()

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["notified"], {"selected_member_ids": [str(self.ann.pk)], "dropped_member_ids": []})
        self.assertEqual(body["lineup"]["status"], "published")
        self.assertEqual(body["lineup"]["pending"], {"selected_member_ids": [], "dropped_member_ids": []})
        rows = self.rows(body["lineup"])
        self.assertEqual((rows["Ann"]["rsvp_status"], rows["Bo"]["rsvp_status"], rows["Cy"]["rsvp_status"]), ("selected", "not_selected", "absent"))
        self.assertTrue(rows["Ann"]["can_drop_out"])
        self.assertFalse(rows["Bo"]["can_drop_out"])
        notification = Notification.objects.filter(member=self.ann).latest("created")
        self.assertEqual((Notification.objects.count(), notification.source), (before + 1, self.game))
        self.assertIn("line-up", notification.body)

    def test_a_republish_notifies_only_the_changes(self):
        self.put(self.ann, self.bo)
        self.publish()
        self.put(self.bo)
        preview = self.get(self.path).json()["pending"]
        before = Notification.objects.count()

        response = self.publish()

        self.assertEqual(preview, {"selected_member_ids": [], "dropped_member_ids": [str(self.ann.pk)]})
        self.assertEqual(response.json()["notified"], {"selected_member_ids": [], "dropped_member_ids": [str(self.ann.pk)]})
        self.assertEqual(Notification.objects.count(), before + 1)
        self.assertEqual(Notification.objects.latest("created").member, self.ann)

    def test_publishing_twice_in_a_row_notifies_nobody_the_second_time(self):
        self.put(self.ann)
        self.publish()
        before = Notification.objects.count()

        self.assertEqual(self.publish().json()["notified"], {"selected_member_ids": [], "dropped_member_ids": []})
        self.assertEqual(Notification.objects.count(), before)

    def test_an_idempotency_key_replays(self):
        self.put(self.ann)
        before = Notification.objects.count()

        first = self.send("post", self.path + "/publish", {"mode": "now"}, HTTP_IDEMPOTENCY_KEY="pub-1")
        second = self.send("post", self.path + "/publish", {"mode": "now"}, HTTP_IDEMPOTENCY_KEY="pub-1")

        self.assertEqual(first.json(), second.json())
        self.assertEqual(Notification.objects.count(), before + 1)

    def test_the_first_publish_of_an_empty_lineup_is_refused(self):
        self.assertError(self.publish(), 409, "lineup_empty")
        self.put()
        self.assertError(self.publish(), 409, "lineup_empty")
        self.assertEqual(Attendance.objects.get(event=self.game, member=self.bo).status, Status.MAYBE)

    def test_schedule_and_cancel(self):
        self.put(self.ann)
        at = timezone.now() + datetime.timedelta(hours=5)

        scheduled = self.publish("schedule", at=self.stamp(at))

        self.assertEqual(scheduled.status_code, 200, scheduled.content)
        self.assertEqual(scheduled.json()["lineup"]["status"], "scheduled")
        self.assertEqual(Attendance.objects.get(event=self.game, member=self.ann).status, Status.PRESENT)
        self.assertEqual(scheduled.json()["notified"], {"selected_member_ids": [], "dropped_member_ids": []})
        cancelled = self.publish("cancel")
        self.assertEqual((cancelled.json()["lineup"]["status"], cancelled.json()["lineup"]["scheduled_publish_at"]), ("draft", None))

    def test_schedule_needs_a_future_time(self):
        self.put(self.ann)

        for extra in ({}, {"at": self.stamp(timezone.now() - datetime.timedelta(hours=1))}):
            with self.subTest(extra=extra):
                response = self.publish("schedule", **extra)
                self.assertError(response, 422, "validation_error")
                self.assertIn("at", response.json()["error"]["fields"])

    def test_the_scheduled_sweep_publishes_it(self):
        from django.core.management import call_command

        self.put(self.ann)
        self.publish("schedule", at=self.stamp(timezone.now() + datetime.timedelta(hours=1)))
        Lineup.objects.update(scheduled_publish_at=timezone.now() - datetime.timedelta(minutes=1))

        call_command("publish_scheduled_lineups")

        self.assertEqual(self.rows(self.get(self.path).json())["Ann"]["rsvp_status"], "selected")

    def test_a_dropout_afterwards_is_visible_as_absent(self):
        self.put(self.ann)
        self.publish()
        from events.services.rsvp import submit_rsvp

        submit_rsvp(Attendance.objects.get(event=self.game, member=self.ann), "dropout", "Injured")

        rows = self.rows(self.get(self.path).json())
        self.assertEqual((rows["Ann"]["rsvp_status"], rows["Ann"]["note"], rows["Ann"]["available"], rows["Ann"]["can_drop_out"]), ("absent", "Injured", False, False))


# --- slice 4: roster -----------------------------------------------------------------------

from club.models import ClubMembership  # noqa: E402


class CoachRosterTestCase(CoachEventTestCase):
    def candidate(self, first, last, **kwargs):
        member = Member.objects.create(first_name=first, last_name=last)
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=kwargs.pop("status", ClubMembership.StatusChoices.ACTIVE), kind=kwargs.pop("kind", ClubMembership.Kind.MEMBER))
        return member

    def roster(self):
        return self.get(self.url("/roster")).json()


class RosterReadTests(CoachRosterTestCase):
    def test_roster_lists_players_staff_and_the_positions_to_choose_from(self):
        ann = self.player("Ann", "Aal", jersey=7)
        TeamMembership.objects.filter(member=ann).update(is_captain=True)
        bo = self.candidate("Bo", "Bos")
        TeamMembership.objects.create(team=self.team, member=bo, season=self.season)
        ClubMembership.objects.filter(member=bo).update(status=ClubMembership.StatusChoices.PENDING)

        body = self.roster()

        self.assertEqual(body["season_id"], str(self.season.pk))
        by_name = {row["first_name"]: row for row in body["players"]}
        self.assertEqual((by_name["Ann"]["jersey_number"], by_name["Ann"]["position"], by_name["Ann"]["is_captain"], by_name["Ann"]["sign_up_pending"]), (7, "Forward", True, False))
        self.assertEqual((by_name["Bo"]["position"], by_name["Bo"]["position_id"], by_name["Bo"]["sign_up_pending"]), ("", None, True))
        [me] = body["staff"]
        self.assertEqual((me["member_id"], me["position"], me["is_manager"], me["is_me"]), (str(self.member.pk), "Head coach", True, True))
        self.assertEqual([p["name"] for p in body["player_positions"]], ["Forward"])
        self.assertEqual({p["name"]: p["management_position"] for p in body["staff_positions"]}, {"Assistant": False, "Head coach": True})

    def test_no_contact_details_or_birth_dates_are_returned(self):
        self.player("Ann", "Aal")

        text = self.get(self.url("/roster")).content.decode()

        for word in ("phone", "date_of_birth", "email", "address"):
            self.assertNotIn(word, text)

    def test_any_staff_may_read_but_only_the_teams_own(self):
        self.assignment.position = self.coach_position
        self.assignment.save()
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")

        self.assertEqual(self.get(self.url("/roster")).status_code, 200)
        self.assertError(self.send("get", self.url("/roster", team=other)), 404, "not_found")
        self.assertError(self.send("get", self.url("/roster/candidates", team=other)), 404, "not_found")

    def test_players_of_a_past_season_are_not_listed(self):
        old = Season.objects.create(club=self.club, start_date=self.season.start_date - datetime.timedelta(days=400), end_date=self.season.start_date - datetime.timedelta(days=35))
        member = Member.objects.create(first_name="Old", last_name="Timer")
        TeamMembership.objects.create(team=self.team, member=member, season=old)

        self.assertEqual(self.roster()["players"], [])


class RosterCandidateTests(CoachRosterTestCase):
    def setUp(self):
        super().setUp()
        ClubMembership.objects.filter(member=self.child).update(status=ClubMembership.StatusChoices.CANCELLED)  # the fixture's child would be a candidate too

    def test_lists_active_members_not_yet_on_the_team(self):
        self.candidate("Cy", "Cox")
        self.candidate("Di", "Dam", status=ClubMembership.StatusChoices.PENDING)
        self.candidate("Ed", "Eek", kind=ClubMembership.Kind.GUARDIAN)
        self.player("Ann", "Aal")

        names = [item["first_name"] for item in self.get(self.url("/roster/candidates")).json()["items"]]

        self.assertEqual(names, ["Cy"])

    def test_search_filter_and_paging(self):
        for first, last in (("Cy", "Cox"), ("Di", "Dam"), ("Ed", "Eek")):
            self.candidate(first, last)

        self.assertEqual([i["first_name"] for i in self.get(self.url("/roster/candidates"), q="dam").json()["items"]], ["Di"])
        page = self.get(self.url("/roster/candidates"), limit=2).json()
        self.assertEqual([i["first_name"] for i in page["items"]], ["Cy", "Di"])
        rest = self.get(self.url("/roster/candidates"), limit=2, cursor=page["next_cursor"]).json()
        self.assertEqual([i["first_name"] for i in rest["items"]], ["Ed"])

    def test_no_team_leaves_out_people_on_another_team(self):
        free = self.candidate("Free", "Agent")
        busy = self.candidate("Busy", "Bee")
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")
        TeamMembership.objects.create(team=other, member=busy, season=self.season)

        self.assertEqual({i["first_name"] for i in self.get(self.url("/roster/candidates")).json()["items"]}, {"Free", "Busy"})
        self.assertEqual([i["member_id"] for i in self.get(self.url("/roster/candidates"), filter="no_team").json()["items"]], [str(free.pk)])

    def test_suggested_are_last_seasons_players(self):
        last = Season.objects.create(club=self.club, start_date=self.season.start_date - datetime.timedelta(days=400), end_date=self.season.start_date - datetime.timedelta(days=35))
        returning = self.candidate("Back", "Again")
        self.candidate("New", "Face")
        TeamMembership.objects.create(team=self.team, member=returning, season=last)

        items = self.get(self.url("/roster/candidates"), filter="suggested").json()["items"]

        self.assertEqual([i["member_id"] for i in items], [str(returning.pk)])

    def test_staff_candidates_are_those_not_on_the_staff(self):
        player = self.candidate("Pla", "Yer")
        TeamMembership.objects.create(team=self.team, member=player, season=self.season)

        names = [i["first_name"] for i in self.get(self.url("/roster/candidates"), role="staff").json()["items"]]

        self.assertEqual(names, ["Pla"])  # a player can also be staff


class RosterWriteTests(CoachRosterTestCase):
    def test_add_players_and_they_are_invited_to_upcoming_events(self):
        cy = self.candidate("Cy", "Cox")
        event = self.event()

        response = self.send("post", self.url("/roster/players"), {"member_ids": [str(cy.pk)]})

        self.assertEqual(response.status_code, 200, response.content)
        [row] = response.json()["players"]
        self.assertEqual((row["first_name"], row["position_id"], row["jersey_number"]), ("Cy", None, None))
        self.assertTrue(Attendance.objects.filter(event=event, member=cy).exists())

    def test_adding_twice_is_harmless_and_a_non_candidate_fails_the_call(self):
        cy = self.candidate("Cy", "Cox")
        guardian = self.candidate("Gu", "Ardian", kind=ClubMembership.Kind.GUARDIAN)
        self.send("post", self.url("/roster/players"), {"member_ids": [str(cy.pk)]})

        again = self.send("post", self.url("/roster/players"), {"member_ids": [str(cy.pk)]})
        bad = self.send("post", self.url("/roster/players"), {"member_ids": [str(cy.pk), str(guardian.pk)]})

        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(again.json()["players"]), 1)
        self.assertError(bad, 422, "not_a_candidate")
        self.assertEqual(bad.json()["error"]["fields"], {"member_ids": [str(guardian.pk)]})
        self.assertFalse(TeamMembership.objects.filter(member=guardian).exists())

    def test_add_staff_with_one_position(self):
        pla = self.candidate("Pla", "Yer")

        response = self.send("post", self.url("/roster/staff"), {"member_ids": [str(pla.pk)], "position_id": str(self.coach_position.pk)})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual({row["first_name"]: row["position"] for row in response.json()["staff"]}, {"Pat": "Head coach", "Pla": "Assistant"})
        assert StaffAssignment.objects.filter(member=pla, team=self.team).exists()

    def test_staff_needs_a_staff_position(self):
        pla = self.candidate("Pla", "Yer")

        for position in (self.player_position.pk, uuid.uuid4()):
            with self.subTest(position=position):
                response = self.send("post", self.url("/roster/staff"), {"member_ids": [str(pla.pk)], "position_id": str(position)})
                self.assertError(response, 422, "validation_error")
                self.assertIn("position_id", response.json()["error"]["fields"])

    def test_update_a_player(self):
        ann = self.player("Ann", "Aal", jersey=7)
        membership = TeamMembership.objects.get(member=ann)

        response = self.send("patch", self.url(f"/roster/{membership.pk}"), {"jersey_number": 10, "is_captain": True})

        self.assertEqual(response.status_code, 200, response.content)
        row = response.json()["players"][0]
        self.assertEqual((row["jersey_number"], row["is_captain"], row["position"]), (10, True, "Forward"))

    def test_jersey_clash_is_a_422_with_fields_never_a_500(self):
        self.player("Ann", "Aal", jersey=7)
        bo = self.player("Bo", "Bos", jersey=9)

        response = self.send("patch", self.url(f"/roster/{TeamMembership.objects.get(member=bo).pk}"), {"jersey_number": 7})

        self.assertError(response, 422, "validation_error")
        self.assertIn("jersey_number", response.json()["error"]["fields"])
        self.assertEqual(TeamMembership.objects.get(member=bo).jersey_number, 9)

    def test_a_number_pool_is_respected(self):
        from teams.models import NumberPool

        pool = NumberPool.objects.create(club=self.club, name="Juniors", min_number=1, max_number=30, min_age_gap_years=0)
        self.team.pool = pool
        self.team.save()
        other_team = Team.objects.create(club=self.club, name="U14", short_name="U14", pool=pool)
        taken = Member.objects.create(first_name="Tom", last_name="Taken")
        TeamMembership.objects.create(team=other_team, member=taken, season=self.season, jersey_number=12)
        ann = self.player("Ann", "Aal")

        response = self.send("patch", self.url(f"/roster/{TeamMembership.objects.get(member=ann).pk}"), {"jersey_number": 12})

        self.assertError(response, 422, "validation_error")
        self.assertIn("jersey_number", response.json()["error"]["fields"])

    def test_a_player_keeps_a_position(self):
        ann = self.player("Ann", "Aal")

        response = self.send("patch", self.url(f"/roster/{TeamMembership.objects.get(member=ann).pk}"), {"position_id": None})

        self.assertError(response, 422, "validation_error")
        self.assertIn("position_id", response.json()["error"]["fields"])

    def test_clearing_a_number(self):
        ann = self.player("Ann", "Aal", jersey=7)

        row = self.send("patch", self.url(f"/roster/{TeamMembership.objects.get(member=ann).pk}"), {"jersey_number": None}).json()["players"][0]

        self.assertIsNone(row["jersey_number"])

    def test_remove_a_player_and_a_staff_member(self):
        ann = self.player("Ann", "Aal")
        other = StaffAssignment.objects.create(team=self.team, member=Member.objects.create(first_name="Sam", last_name="Staff"), season=self.season, position=self.coach_position)

        self.assertEqual(self.send("delete", self.url(f"/roster/{TeamMembership.objects.get(member=ann).pk}")).status_code, 204)
        self.assertEqual(self.send("delete", self.url(f"/staff/{other.pk}")).status_code, 204)

        body = self.roster()
        self.assertEqual((body["players"], [row["first_name"] for row in body["staff"]]), ([], ["Pat"]))
        self.assertTrue(Member.objects.filter(pk=ann.pk).exists())

    def test_removing_yourself_as_staff_is_forbidden(self):
        self.assertError(self.send("delete", self.url(f"/staff/{self.assignment.pk}")), 403, "self_removal")
        self.assertTrue(StaffAssignment.objects.filter(pk=self.assignment.pk).exists())

    def test_a_coach_without_a_management_position_may_not_change_anything(self):
        ann = self.player("Ann", "Aal")
        membership = TeamMembership.objects.get(member=ann)
        cy = self.candidate("Cy", "Cox")
        self.assignment.position = self.coach_position
        self.assignment.save()

        for method, path, body in (
            ("post", "/roster/players", {"member_ids": [str(cy.pk)]}),
            ("post", "/roster/staff", {"member_ids": [str(cy.pk)], "position_id": str(self.coach_position.pk)}),
            ("patch", f"/roster/{membership.pk}", {"jersey_number": 3}),
            ("delete", f"/roster/{membership.pk}", None),
            ("delete", f"/staff/{self.assignment.pk}", None),
        ):
            with self.subTest(path=path):
                self.assertError(self.send(method, self.url(path), body), 403, "manager_required")

    def test_another_teams_membership_is_404(self):
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")
        member = Member.objects.create(first_name="Far", last_name="Away")
        foreign = TeamMembership.objects.create(team=other, member=member, season=self.season)
        staff = StaffAssignment.objects.create(team=other, member=member, season=self.season, position=self.coach_position)

        self.assertError(self.send("patch", self.url(f"/roster/{foreign.pk}"), {"jersey_number": 3}), 404, "not_found")
        self.assertError(self.send("delete", self.url(f"/roster/{foreign.pk}")), 404, "not_found")
        self.assertError(self.send("delete", self.url(f"/staff/{staff.pk}")), 404, "not_found")


# --- slice 5: news -------------------------------------------------------------------------

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402

from news.models import News  # noqa: E402

from .test_member import png_bytes  # noqa: E402


class CoachNewsTests(CoachEventTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch("appapi.routers.coach_news.dispatch_notify_editors_of_pending_review")  # a background thread would fight the test database
        self.dispatch = patcher.start()
        self.addCleanup(patcher.stop)

    def submit(self, title="Great win", body="We **won** 5-2.", photos=(), team=None, **headers):
        data = {"title": title, "body": body}
        if photos:
            data["photos"] = list(photos)
        return self.client.post(f"/api/app/v1{self.url('/news', team=team)}", data=data, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {self.token}", **headers)

    def image(self, name="a.png"):
        return SimpleUploadedFile(name, png_bytes(), content_type="image/png")

    def test_submits_for_review_with_photos_and_tells_the_editors(self):
        response = self.submit(photos=[self.image("a.png"), self.image("b.png")])

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["title"], body["status"], body["published_at"]), ("Great win", "pending_review", None))
        self.assertEqual([team["id"] for team in body["teams"]], [str(self.team.pk)])
        self.assertEqual([photo["is_main"] for photo in body["photos"]], [True, False])
        self.assertTrue(body["photos"][0]["url"].startswith("http"))
        item = News.objects.get(pk=body["id"])
        self.assertEqual((item.created_by, item.visibility, item.status), (self.member, News.Visibility.INTERNAL, News.Status.PENDING_REVIEW))
        self.dispatch.assert_called_once_with(item.pk)

    def test_no_photos_is_fine(self):
        self.assertEqual(self.submit().status_code, 201)

    def test_title_and_body_are_required(self):
        response = self.submit(title="", body="")

        self.assertError(response, 422, "validation_error")
        self.assertEqual(set(response.json()["error"]["fields"]), {"title", "body"})
        self.assertFalse(News.objects.exists())

    def test_a_file_that_is_not_an_image_is_refused_and_nothing_is_saved(self):
        response = self.submit(photos=[self.image(), SimpleUploadedFile("evil.png", b"not an image", content_type="image/png")])

        self.assertError(response, 422, "validation_error")
        self.assertIn("evil.png", response.json()["error"]["fields"]["photos"][0])
        self.assertFalse(News.objects.exists())

    def test_too_many_photos(self):
        response = self.submit(photos=[self.image(f"{n}.png") for n in range(11)])

        self.assertError(response, 422, "validation_error")
        self.assertIn("photos", response.json()["error"]["fields"])

    def test_an_idempotency_key_posts_once(self):
        first = self.submit(HTTP_IDEMPOTENCY_KEY="news-1")
        second = self.submit(HTTP_IDEMPOTENCY_KEY="news-1")

        self.assertEqual(first.json(), second.json())
        self.assertEqual(News.objects.count(), 1)

    def test_a_staff_member_who_may_not_post_is_forbidden(self):
        self.assignment.position = self.coach_position
        self.assignment.save()

        self.assertError(self.submit(), 403, "forbidden")
        self.assertFalse(News.objects.exists())

    def test_another_teams_news_is_404(self):
        other = Team.objects.create(club=self.club, name="U18", short_name="U18")

        self.assertError(self.submit(team=other), 404, "not_found")
        self.assertError(self.send("get", self.url("/news", team=other)), 404, "not_found")

    def test_lists_only_your_own_submissions_for_the_team_with_their_status(self):
        mine = self.submit(title="Mine").json()
        News.objects.filter(pk=mine["id"]).update(status=News.Status.PUBLISHED, published_at=timezone.now())
        waiting = self.submit(title="Waiting").json()
        returned = self.submit(title="Returned").json()
        News.objects.filter(pk=returned["id"]).update(status=News.Status.DRAFT)
        others = News.objects.create(club=self.club, title="Not mine", body="x", created_by=Member.objects.create(first_name="Ed", last_name="Editor"))
        others.teams.add(self.team)
        elsewhere = News.objects.create(club=self.club, title="Other team", body="x", created_by=self.member)

        items = self.get(self.url("/news")).json()["items"]

        self.assertEqual({item["title"]: item["status"] for item in items}, {"Mine": "published", "Waiting": "pending_review", "Returned": "draft"})
        self.assertEqual(items[0]["id"], returned["id"])  # newest first
        self.assertNotIn(str(others.pk), [item["id"] for item in items])
        self.assertNotIn(str(elsewhere.pk), [item["id"] for item in items])
        self.assertIsNotNone(next(item for item in items if item["title"] == "Mine")["published_at"])
        self.assertIn(waiting["id"], [item["id"] for item in items])

    def test_list_pages(self):
        for n in range(3):
            self.submit(title=f"Post {n}")

        page = self.get(self.url("/news"), limit=2).json()
        rest = self.get(self.url("/news"), limit=2, cursor=page["next_cursor"]).json()

        self.assertEqual((len(page["items"]), len(rest["items"]), rest["next_cursor"]), (2, 1, None))

    def test_any_staff_may_list(self):
        self.assignment.position = self.coach_position
        self.assignment.save()

        self.assertEqual(self.get(self.url("/news")).status_code, 200)


# --- slice 6: evaluations ------------------------------------------------------------------

from club.models import EvaluationManager  # noqa: E402
from evaluations.models import EvaluationChecklist, PlayerEvaluation  # noqa: E402
from formbuilder.models import Field as FormField  # noqa: E402
from formbuilder.models import Form  # noqa: E402


class CoachEvaluationTestCase(CoachApiTestCase):
    def setUp(self):
        super().setUp()
        from waffle import get_waffle_flag_model

        flag, _created = get_waffle_flag_model().objects.get_or_create(name="evaluations", defaults={"everyone": None})
        flag.clubs.add(self.club)
        # A club-wide grant for someone who staffs no team at all.
        self.assignment.delete()
        EvaluationManager.objects.create(club=self.club, member=self.member)
        self._token = self.login_with_totp()
        self.form = Form.objects.create(club=self.club, title="U16 rubric")
        FormField.objects.create(form=self.form, key="skating", label="Skating", field_type="choice", options=[{"value": "1", "label": "1"}, {"value": "5", "label": "5"}], order=1, section="Technical")
        FormField.objects.create(form=self.form, key="notes", label="Notes", field_type="textarea", required=False, order=2)
        self.checklist = EvaluationChecklist.objects.create(club=self.club, name="U16", form=self.form)
        self.child_player = self.child
        self.path = f"/players/{self.child.pk}/evaluations"

    def post(self, answers, slug="u16", **extra):
        return self.send("post", self.path, {"checklist_slug": slug, "answers": answers}, **extra)


class EvaluationChecklistTests(CoachEvaluationTestCase):
    def test_lists_the_fields_to_render(self):
        EvaluationChecklist.objects.create(club=self.club, name="Empty")  # no questions yet: not offered
        EvaluationChecklist.objects.create(club=self.club, name="Retired", form=self.form, is_active=False)

        body = self.get("/evaluations/checklists").json()

        [checklist] = body["items"]
        self.assertEqual((checklist["slug"], checklist["name"]), ("u16", "U16"))
        skating, notes = checklist["fields"]
        self.assertEqual((skating["key"], skating["type"], skating["required"], skating["section"], [o["value"] for o in skating["options"]]), ("skating", "choice", True, "Technical", ["1", "5"]))
        self.assertEqual((notes["type"], notes["required"]), ("textarea", False))
        self.assertIn("max_file_bytes", body)

    def test_a_coach_without_the_grant_is_forbidden(self):
        EvaluationManager.objects.all().delete()

        for path in ("/evaluations/checklists", self.path):
            with self.subTest(path=path):
                self.assertError(self.get(path), 403, "forbidden")
        self.assertError(self.post({"skating": "5"}), 403, "forbidden")

    def test_the_module_must_be_on(self):
        from waffle import get_waffle_flag_model

        get_waffle_flag_model().objects.get(name="evaluations").clubs.clear()

        self.assertError(self.get("/evaluations/checklists"), 404, "feature_disabled")
        self.assertError(self.post({"skating": "5"}), 404, "feature_disabled")

    def test_context_says_whether_the_account_may(self):
        permissions = self.get("/context").json()["permissions"]

        self.assertTrue(permissions["can_manage_evaluations"])


class EvaluationFilingTests(CoachEvaluationTestCase):
    def test_files_an_evaluation_and_returns_it(self):
        response = self.post({"skating": "5", "notes": "Quick feet"})

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["checklist"], body["season_id"], body["evaluator"]), ({"slug": "u16", "name": "U16"}, str(self.season.pk), {"first_name": "Pat", "last_name": "Parent"}))
        self.assertEqual([(a["key"], a["value"]) for a in body["answers"]], [("skating", "5"), ("notes", "Quick feet")])
        evaluation = PlayerEvaluation.objects.get(pk=body["id"])
        self.assertEqual((evaluation.player, evaluation.submission.member), (self.child, self.member))

    def test_an_optional_question_may_be_left_out(self):
        body = self.post({"skating": "1"}).json()

        self.assertEqual([(a["key"], a["value"]) for a in body["answers"]], [("skating", "1"), ("notes", None)])

    def test_validation_errors_are_per_question(self):
        response = self.post({"skating": "9"})

        self.assertError(response, 422, "validation_error")
        self.assertIn("skating", response.json()["error"]["fields"])
        self.assertFalse(PlayerEvaluation.objects.exists())

    def test_a_required_answer_is_needed(self):
        response = self.post({"notes": "Only notes"})

        self.assertError(response, 422, "validation_error")
        self.assertIn("skating", response.json()["error"]["fields"])

    def test_the_checklist_is_always_explicit_even_with_only_one(self):
        self.assertError(self.send("post", self.path, {"answers": {"skating": "5"}}), 404, "not_found")
        self.assertError(self.post({"skating": "5"}, slug="nope"), 404, "not_found")

    def test_an_inactive_checklist_cannot_be_used(self):
        EvaluationChecklist.objects.filter(pk=self.checklist.pk).update(is_active=False)

        self.assertError(self.post({"skating": "5"}), 404, "not_found")

    def test_answers_must_be_an_object(self):
        self.assertError(self.send("post", self.path, {"checklist_slug": "u16", "answers": "5"}), 422, "validation_error")

    def test_without_a_season_it_is_a_409(self):
        Season.objects.update(start_date=timezone.localdate() - datetime.timedelta(days=400), end_date=timezone.localdate() - datetime.timedelta(days=40))

        self.assertError(self.post({"skating": "5"}), 409, "no_current_season")

    def test_an_idempotency_key_files_once(self):
        first = self.post({"skating": "5"}, HTTP_IDEMPOTENCY_KEY="eval-1")
        second = self.post({"skating": "5"}, HTTP_IDEMPOTENCY_KEY="eval-1")

        self.assertEqual(first.json(), second.json())
        self.assertEqual(PlayerEvaluation.objects.count(), 1)

    def test_an_unknown_player_is_404(self):
        self.assertError(self.send("post", f"/players/{uuid.uuid4()}/evaluations", {"checklist_slug": "u16", "answers": {"skating": "5"}}), 404, "not_found")
        self.assertError(self.get(f"/players/{uuid.uuid4()}/evaluations"), 404, "not_found")

    def test_a_person_outside_the_club_is_404(self):
        stranger = Member.objects.create(first_name="Out", last_name="Sider")

        self.assertError(self.get(f"/players/{stranger.pk}/evaluations"), 404, "not_found")


class EvaluationHistoryTests(CoachEvaluationTestCase):
    def test_lists_everyones_evaluations_newest_first_and_opens_one(self):
        first = self.post({"skating": "1"}).json()
        second = self.post({"skating": "5", "notes": "Better"}).json()
        PlayerEvaluation.objects.filter(pk=first["id"]).update(created=timezone.now() - datetime.timedelta(days=30))

        listing = self.get(self.path).json()
        detail = self.get(f"{self.path}/{second['id']}").json()

        self.assertEqual([item["id"] for item in listing["items"]], [second["id"], first["id"]])
        self.assertNotIn("answers", listing["items"][0])
        self.assertEqual([(a["key"], a["value"]) for a in detail["answers"]], [("skating", "5"), ("notes", "Better")])

    def test_an_old_evaluation_keeps_the_questions_it_was_scored_against(self):
        from evaluations.services import start_new_rubric_version

        old = self.post({"skating": "5"}).json()
        new_form = start_new_rubric_version(self.checklist)
        new_form.fields.filter(key="notes").update(is_active=False)
        new_form.fields.filter(key="skating").update(label="Skating v2")

        detail = self.get(f"{self.path}/{old['id']}").json()
        checklist_fields = self.get("/evaluations/checklists").json()["items"][0]["fields"]

        self.assertEqual([a["label"] for a in detail["answers"]], ["Skating", "Notes"])
        self.assertEqual([f["label"] for f in checklist_fields], ["Skating v2"])

    def test_pages(self):
        for _n in range(3):
            self.post({"skating": "5"})

        page = self.get(self.path, limit=2).json()
        rest = self.get(self.path, limit=2, cursor=page["next_cursor"]).json()

        self.assertEqual((len(page["items"]), len(rest["items"])), (2, 1))

    def test_another_players_evaluation_is_404(self):
        other = self.player("Ann", "Aal")
        mine = self.post({"skating": "5"}).json()

        self.assertError(self.get(f"/players/{other.pk}/evaluations/{mine['id']}"), 404, "not_found")

    def test_evaluations_never_appear_in_member_endpoints(self):
        self.post({"skating": "5", "notes": "Secret assessment"})
        EvaluationManager.objects.all().delete()  # read the member side as a plain parent

        for path in ("/home", f"/members/{self.child.pk}", "/forms", "/notifications"):
            with self.subTest(path=path):
                self.assertNotIn("Secret assessment", self.get(path).content.decode())
