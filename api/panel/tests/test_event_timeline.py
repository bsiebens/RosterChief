import datetime

from django.utils import timezone

from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import ClubMembership, ClubRole
from events.models import Attendance, Event, GameMoment, GameTimeline, Location, Opponent
from events.services.attendance import sync_event_attendances
from members.models import Member
from notifications.models import Notification
from rosterchief.test_support import make_club
from teams.models import Position, StaffAssignment, Team, TeamMembership

EVENTS = "/manage/events"

DOCUMENT = {
    "version": 1,
    "status": "live",
    "period": "2",
    "clock": "04:31",
    "score": {"home": 1, "away": 0},
    "periods": [{"label": "1", "home": 1, "away": 0}],
    "events": [
        {"id": "a", "type": "period_start", "period": "1", "seconds": 0, "time": "00:00"},
        {"id": "b", "type": "goal", "period": "1", "seconds": 473, "time": "07:53", "side": "home", "goal": {"scorer": {"name": "Julia Malejka", "number": 88}, "assists": [], "strength": "EQ", "score_after": {"home": 1, "away": 0}}},
    ],
}


class EventTimelineTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        self.other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        self.ground = Location.objects.create(club=self.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        self.opponent = Opponent.objects.create(club=self.club, name="Rivals")
        self.game = Event.objects.create(club=self.club, kind="game", title="Derby", start=timezone.now() + datetime.timedelta(hours=2), location=self.ground, opponent=self.opponent)
        self.game.teams.add(self.team)
        self.player = Member.objects.create(first_name="Sam", last_name="Smith")
        ClubMembership.objects.create(club=self.club, member=self.player, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        TeamMembership.objects.create(team=self.team, member=self.player, season=self.season, jersey_number=7)
        self.silent = Member.objects.create(first_name="Quinn", last_name="Quiet")
        ClubMembership.objects.create(club=self.club, member=self.silent, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        TeamMembership.objects.create(team=self.team, member=self.silent, season=self.season, jersey_number=9)
        sync_event_attendances(self.game)
        self.row = Attendance.objects.get(event=self.game, member=self.player)
        self.row.status = Attendance.AttendanceStatus.PRESENT
        self.row.save()
        self.quiet_row = Attendance.objects.get(event=self.game, member=self.silent)

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def as_admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def staff(self, *, manager, team=None):
        position = Position.objects.create(club=self.club, name="Head" if manager else "Assistant", short_name="P", staff_position=True, management_position=manager)
        StaffAssignment.objects.create(team=team or self.team, member=self.member, season=self.season, position=position)
        return self.login_with_totp() if manager else self.login()

    def call(self, method, path, token=None, data=None, host=CLUB_HOST, **headers):
        return self.api(method, path, token=token, host=host, data=data, **headers)

    def timeline(self, **kwargs):
        return GameTimeline.objects.create(event=self.game, source="RBIHF", status="live", data=DOCUMENT, **kwargs)

    # --- access ---------------------------------------------------------------

    def test_requires_token(self):
        for method, path in (("get", "timeline"), ("post", "check-ins"), ("post", "remind-silent")):
            self.assertError(self.call(method, f"{EVENTS}/{self.game.pk}/{path}"), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        for method, path in (("get", "timeline"), ("post", "check-ins"), ("post", "remind-silent")):
            self.assertError(self.call(method, f"{EVENTS}/{self.game.pk}/{path}", token, data={"items": []} if method == "post" else None), 403, "forbidden")

    def test_a_coach_cannot_check_in_or_remind(self):
        token = self.staff(manager=False)
        # The event page itself is scoped to the teams one manages (EventDetailView), so a plain coach doesn't see it.
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/timeline", token), 404, "not_found")
        self.assertError(self.call("post", f"{EVENTS}/{self.game.pk}/check-ins", token, data={"items": []}), 403, "forbidden")
        self.assertError(self.call("post", f"{EVENTS}/{self.game.pk}/remind-silent", token), 403, "forbidden")

    def test_a_manager_of_another_team_cannot_write_and_cannot_see(self):
        token = self.staff(manager=True, team=self.other_team)
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/timeline", token), 404, "not_found")
        self.assertError(self.call("post", f"{EVENTS}/{self.game.pk}/check-ins", token, data={"items": []}), 403, "forbidden")
        self.assertError(self.call("post", f"{EVENTS}/{self.game.pk}/remind-silent", token), 403, "forbidden")

    def test_other_clubs_event_is_404(self):
        other = make_club(name="Rival FC", slug="rival-fc")
        theirs = Event.objects.create(club=other, kind="game", title="Away", start=timezone.now())
        token = self.as_admin()
        for method, path in (("get", "timeline"), ("post", "check-ins"), ("post", "remind-silent")):
            self.assertError(self.call(method, f"{EVENTS}/{theirs.pk}/{path}", token, data={"items": []} if method == "post" else None), 404, "not_found")
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/timeline", token, host=OTHER_HOST), 403, "not_a_member")

    # --- timeline -------------------------------------------------------------

    def test_game_without_a_timeline(self):
        body = self.call("get", f"{EVENTS}/{self.game.pk}/timeline", self.as_admin()).json()
        self.assertEqual(body["event_id"], str(self.game.pk))
        self.assertIsNone(body["timeline"])
        self.assertEqual(body["moments"], [])

    def test_a_training_has_no_timeline(self):
        training = Event.objects.create(club=self.club, kind="training", title="Practice", start=timezone.now())
        training.teams.add(self.team)
        GameTimeline.objects.create(event=training, source="RBIHF", data=DOCUMENT)
        self.assertIsNone(self.call("get", f"{EVENTS}/{training.pk}/timeline", self.as_admin()).json()["timeline"])

    def test_timeline_with_names_and_moments(self):
        self.timeline(last_error="boom")
        GameMoment.objects.create(event=self.game, key="goal_for:b", kind="goal_for", payload={"scorer": {"name": "Julia Malejka", "number": 88}, "score": {"for": 1, "against": 0}}, game_seconds=473, notifiable=True)
        GameMoment.objects.create(event=self.game, key="goal_for:old", kind="goal_for", payload={}, notifiable=False)
        GameMoment.objects.create(event=self.game, key="game_started", kind="game_started", delivered_at=timezone.now())
        body = self.call("get", f"{EVENTS}/{self.game.pk}/timeline", self.as_admin()).json()
        self.assertEqual(body["source"], "RBIHF")
        self.assertEqual(body["last_error"], "boom")
        timeline = body["timeline"]
        self.assertEqual((timeline["home_score"], timeline["away_score"], timeline["our_side"]), (1, 0, "home"))
        goal = next(item for item in timeline["events"] if item["type"] == "goal")
        self.assertEqual(goal["goal"]["scorer"], {"name": "Julia Malejka", "number": 88})
        self.assertTrue(goal["ours"])
        self.assertEqual(sorted(moment["status"] for moment in body["moments"]), ["catch_up", "pending", "pushed"])
        self.assertIn("Julia Malejka (#88)", next(m["text"] for m in body["moments"] if m["game_seconds"] == 473))

    def test_a_manager_of_the_team_sees_it(self):
        self.timeline()
        token = self.staff(manager=True)
        self.assertIsNotNone(self.call("get", f"{EVENTS}/{self.game.pk}/timeline", token).json()["timeline"])

    # --- check-ins ------------------------------------------------------------

    def test_check_ins_record_and_clear(self):
        token = self.as_admin()
        response = self.call("post", f"{EVENTS}/{self.game.pk}/check-ins", token, data={"items": [{"attendance_id": str(self.row.pk), "showed_up": True}, {"attendance_id": str(self.quiet_row.pk), "showed_up": False}]})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["counts"]["checked_in"], 2)
        self.assertEqual(body["counts"]["total"], 2)
        self.assertEqual({item["attendance_id"]: item["showed_up"] for item in body["items"]}, {str(self.row.pk): True, str(self.quiet_row.pk): False})
        self.row.refresh_from_db()
        self.assertIs(self.row.showed_up, True)

        again = self.call("post", f"{EVENTS}/{self.game.pk}/check-ins", token, data={"items": [{"attendance_id": str(self.row.pk), "showed_up": None}]})
        self.assertEqual(again.json()["counts"]["checked_in"], 1)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.showed_up)

    def test_a_team_manager_may_check_in(self):
        token = self.staff(manager=True)
        response = self.call("post", f"{EVENTS}/{self.game.pk}/check-ins", token, data={"items": [{"attendance_id": str(self.row.pk), "showed_up": True}]})
        self.assertEqual(response.status_code, 200, response.content)

    def test_unknown_row_fails_the_whole_call(self):
        other = Event.objects.create(club=self.club, kind="training", title="P", start=timezone.now())
        stray = Attendance.objects.create(event=other, member=self.player)
        token = self.as_admin()
        response = self.call("post", f"{EVENTS}/{self.game.pk}/check-ins", token, data={"items": [{"attendance_id": str(self.row.pk), "showed_up": True}, {"attendance_id": str(stray.pk), "showed_up": True}]})
        self.assertError(response, 422, "unknown_attendance")
        self.assertEqual(response.json()["error"]["fields"]["items"], [str(stray.pk)])
        self.row.refresh_from_db()
        self.assertIsNone(self.row.showed_up)

    def test_check_in_validation(self):
        token = self.as_admin()
        self.assertEqual(self.call("post", f"{EVENTS}/{self.game.pk}/check-ins", token, data={}).status_code, 422)
        self.assertEqual(self.call("post", f"{EVENTS}/{self.game.pk}/check-ins", token, data={"items": [{"attendance_id": "x", "showed_up": True}]}).status_code, 422)

    # --- remind silent --------------------------------------------------------

    def test_remind_silent_and_idempotency(self):
        token = self.as_admin()
        with self.captureOnCommitCallbacks(execute=True):
            first = self.call("post", f"{EVENTS}/{self.game.pk}/remind-silent", token, HTTP_IDEMPOTENCY_KEY="remind-1")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json(), {"notified": 1})
        before = Notification.objects.count()
        with self.captureOnCommitCallbacks(execute=True):
            replay = self.call("post", f"{EVENTS}/{self.game.pk}/remind-silent", token, HTTP_IDEMPOTENCY_KEY="remind-1")
        self.assertEqual(replay.json(), {"notified": 1})
        self.assertEqual(Notification.objects.count(), before)

    def test_remind_silent_with_nobody_silent(self):
        self.quiet_row.status = Attendance.AttendanceStatus.PRESENT
        self.quiet_row.save()
        self.assertEqual(self.call("post", f"{EVENTS}/{self.game.pk}/remind-silent", self.as_admin()).json(), {"notified": 0})
