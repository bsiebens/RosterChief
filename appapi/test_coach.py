"""Phase 4 of the app API: coach endpoints under /teams/{team_id}/. Pat (the parent from
AppApiTestCase) is staff of "U16": a plain coach by default, a manager with ``manage()``."""

import datetime
import uuid

from django.utils import timezone

from events.models import Attendance, Event, Lineup
from members.models import Member
from notifications.models import Notification
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
