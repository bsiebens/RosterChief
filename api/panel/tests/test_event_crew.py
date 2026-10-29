import datetime

from django.utils import timezone
from waffle import get_waffle_flag_model

from api.protected.tests import CLUB_HOST, OTHER_HOST, AppApiTestCase
from club.models import ClubRole, RefereeManager
from events.models import Event, EventOfficial, EventReferee, Location, OfficialSignup, RefereeSignup
from members.models import Member
from rosterchief.test_support import make_club
from teams.models import OfficialLevel, OfficialProfile, Position, RefereeLevel, RefereeProfile, StaffAssignment, Team

EVENTS = "/manage/events"


class EventCrewTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        self.ground = Location.objects.create(club=self.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        self.start = timezone.now() + datetime.timedelta(days=5)
        self.game = Event.objects.create(club=self.club, kind="game", title="Derby", start=self.start, location=self.ground)
        self.game.teams.add(self.team)
        valid = timezone.localdate() + datetime.timedelta(days=60)
        self.ref_level = RefereeLevel.objects.create(club=self.club, name="Regional")
        self.ref_level.teams.add(self.team)
        self.referee = Member.objects.create(first_name="Ref", last_name="Eree")
        RefereeProfile.objects.create(club=self.club, member=self.referee, level=self.ref_level, valid_until=valid)
        self.off_level = OfficialLevel.objects.create(club=self.club, name="Table")
        self.off_level.teams.add(self.team)
        self.official = Member.objects.create(first_name="Olly", last_name="Official")
        OfficialProfile.objects.create(club=self.club, member=self.official, level=self.off_level, valid_until=valid)

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def as_admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def call(self, method, path, token=None, data=None, host=CLUB_HOST, **headers):
        return self.api(method, path, token=token, host=host, data=data, **headers)

    def officials_on(self):
        get_waffle_flag_model().objects.get_or_create(name="officials")[0].clubs.add(self.club)

    def as_team_manager(self):
        head = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=self.team, member=self.member, season=self.season, position=head)
        return self.login_with_totp()

    # --- access ---------------------------------------------------------------

    def test_requires_token(self):
        for kind in ("referees", "officials"):
            for method, path in (("get", f"{EVENTS}/{self.game.pk}/{kind}"), ("post", f"{EVENTS}/{self.game.pk}/{kind}"), ("post", f"{EVENTS}/{self.game.pk}/{kind}/external"), ("delete", f"{EVENTS}/{self.game.pk}/{kind}/{self.game.pk}")):
                self.assertError(self.call(method, path), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        for kind in ("referees", "officials"):
            self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/{kind}", token), 403, "forbidden")

    def test_team_manager_reads_the_panel_but_cannot_write(self):
        self.officials_on()
        token = self.as_team_manager()
        for kind in ("referees", "officials"):
            body = self.call("get", f"{EVENTS}/{self.game.pk}/{kind}", token).json()
            self.assertTrue(body["needed"])
            self.assertFalse(body["can_manage"])
            self.assertEqual(body["candidates"], [])
            self.assertIsNone(body["signups"])
            self.assertError(self.call("post", f"{EVENTS}/{self.game.pk}/{kind}", token, data={"member_id": str(self.referee.pk)}), 403, "forbidden")
            self.assertError(self.call("post", f"{EVENTS}/{self.game.pk}/{kind}/external", token, data={"name": "X"}), 403, "forbidden")
            self.assertError(self.call("delete", f"{EVENTS}/{self.game.pk}/{kind}/{self.game.pk}", token), 403, "forbidden")

    def test_a_coach_outside_the_event_gets_404(self):
        other = Team.objects.create(club=self.club, name="U14", short_name="U14")
        pos = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        StaffAssignment.objects.create(team=other, member=self.member, season=self.season, position=pos)
        token = self.login()
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/referees", token), 404, "not_found")

    def test_referee_manager_grant_may_assign(self):
        RefereeManager.objects.create(club=self.club, member=self.member)
        pos = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        StaffAssignment.objects.create(team=self.team, member=self.member, season=self.season, position=pos)
        token = self.login_with_totp()
        response = self.call("post", f"{EVENTS}/{self.game.pk}/referees", token, data={"member_id": str(self.referee.pk)})
        self.assertEqual(response.status_code, 201, response.content)

    def test_other_clubs_event_and_ids_are_404(self):
        other = make_club(name="Rival FC", slug="rival-fc")
        theirs = Event.objects.create(club=other, kind="game", title="Away", start=self.start)
        token = self.as_admin()
        for kind in ("referees", "officials"):
            self.officials_on()
            self.assertError(self.call("get", f"{EVENTS}/{theirs.pk}/{kind}", token), 404, "not_found")
            self.assertError(self.call("post", f"{EVENTS}/{theirs.pk}/{kind}", token, data={"member_id": str(self.referee.pk)}), 404, "not_found")
            self.assertError(self.call("delete", f"{EVENTS}/{self.game.pk}/{kind}/{theirs.pk}", token), 404, "not_found")
        self.assertError(self.call("get", f"{EVENTS}/{self.game.pk}/referees", token, host=OTHER_HOST), 403, "not_a_member")

    def test_officials_need_the_feature(self):
        token = self.as_admin()
        for method, path in (("get", ""), ("post", ""), ("post", "/external")):
            self.assertError(self.call(method, f"{EVENTS}/{self.game.pk}/officials{path}", token, data={"member_id": str(self.official.pk), "name": "X"} if method == "post" else None), 404, "feature_disabled")

    # --- referees ---------------------------------------------------------------

    def test_panel_lists_candidates_and_signups(self):
        token = self.as_admin()
        RefereeSignup.objects.create(event=self.game, member=self.referee, status=RefereeSignup.Status.ACCEPTED)
        body = self.call("get", f"{EVENTS}/{self.game.pk}/referees", token).json()
        self.assertTrue(body["can_manage"])
        self.assertEqual(body["max"], 2)
        self.assertFalse(body["full"])
        self.assertEqual([c["member_id"] for c in body["candidates"]], [str(self.referee.pk)])
        self.assertFalse(body["candidates"][0]["has_conflict"])
        self.assertEqual([s["member_id"] for s in body["signups"]["interested"]], [str(self.referee.pk)])
        self.assertEqual(body["assigned"], [])

    def test_a_game_away_from_home_is_not_needed(self):
        away = Location.objects.create(club=self.club, name="Away", address="2 St", city="Town", zip_code="1000", country="BE")
        self.game.location = away
        self.game.save()
        body = self.call("get", f"{EVENTS}/{self.game.pk}/referees", self.as_admin()).json()
        self.assertFalse(body["needed"])
        self.assertEqual(body["candidates"], [])

    def test_assign_and_unassign_a_referee(self):
        token = self.as_admin()
        response = self.call("post", f"{EVENTS}/{self.game.pk}/referees", token, data={"member_id": str(self.referee.pk)})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual([row["member_id"] for row in body["assigned"]], [str(self.referee.pk)])
        self.assertEqual(body["candidates"], [])
        self.assertEqual(body["assigned"][0]["assigned_by"], str(self.member))
        self.assertTrue(RefereeSignup.objects.filter(event=self.game, member=self.referee, status="confirmed").exists())
        assignment = EventReferee.objects.get(event=self.game)

        removed = self.call("delete", f"{EVENTS}/{self.game.pk}/referees/{assignment.pk}", token)
        self.assertEqual(removed.status_code, 200, removed.content)
        self.assertEqual(removed.json()["assigned"], [])
        self.assertEqual([c["member_id"] for c in removed.json()["candidates"]], [str(self.referee.pk)])
        self.assertTrue(RefereeSignup.objects.filter(event=self.game, member=self.referee, status="accepted").exists())

    def test_assigning_a_non_candidate_is_404(self):
        stranger = Member.objects.create(first_name="No", last_name="Level")
        token = self.as_admin()
        self.assertError(self.call("post", f"{EVENTS}/{self.game.pk}/referees", token, data={"member_id": str(stranger.pk)}), 404, "not_found")

    def test_capacity_and_duplicates_are_409(self):
        self.game.max_referees = 1
        self.game.save()
        token = self.as_admin()
        self.assertEqual(self.call("post", f"{EVENTS}/{self.game.pk}/referees", token, data={"member_id": str(self.referee.pk)}).status_code, 201)
        self.assertError(self.call("post", f"{EVENTS}/{self.game.pk}/referees/external", token, data={"name": "Fed Ref"}), 409, "cannot_assign")
        body = self.call("get", f"{EVENTS}/{self.game.pk}/referees", token).json()
        self.assertTrue(body["full"])
        self.assertEqual(body["candidates"], [])

    def test_external_referee(self):
        token = self.as_admin()
        response = self.call("post", f"{EVENTS}/{self.game.pk}/referees/external", token, data={"name": "  Fed Ref "})
        self.assertEqual(response.status_code, 201, response.content)
        row = response.json()["assigned"][0]
        self.assertTrue(row["external"])
        self.assertEqual(row["name"], "Fed Ref")
        self.assertIsNone(row["member_id"])
        removed = self.call("delete", f"{EVENTS}/{self.game.pk}/referees/{row['id']}", token)
        self.assertEqual(removed.json()["assigned"], [])

    def test_validation(self):
        token = self.as_admin()
        self.assertEqual(self.call("post", f"{EVENTS}/{self.game.pk}/referees/external", token, data={"name": "   "}).status_code, 422)
        self.assertEqual(self.call("post", f"{EVENTS}/{self.game.pk}/referees/external", token, data={}).status_code, 422)
        self.assertEqual(self.call("post", f"{EVENTS}/{self.game.pk}/referees", token, data={"member_id": "nope"}).status_code, 422)

    def test_assign_is_idempotent(self):
        token = self.as_admin()
        headers = {"HTTP_IDEMPOTENCY_KEY": "assign-1"}
        first = self.call("post", f"{EVENTS}/{self.game.pk}/referees", token, data={"member_id": str(self.referee.pk)}, **headers)
        again = self.call("post", f"{EVENTS}/{self.game.pk}/referees", token, data={"member_id": str(self.referee.pk)}, **headers)
        self.assertEqual((first.status_code, again.status_code), (201, 201))
        self.assertEqual(again.json(), first.json())
        self.assertEqual(EventReferee.objects.filter(event=self.game).count(), 1)

    # --- officials ----------------------------------------------------------------

    def test_officials_flow(self):
        self.officials_on()
        token = self.as_admin()
        OfficialSignup.objects.create(event=self.game, member=self.official, status=OfficialSignup.Status.INVITED)
        panel = self.call("get", f"{EVENTS}/{self.game.pk}/officials", token).json()
        self.assertEqual(panel["max"], 1)
        self.assertEqual([c["member_id"] for c in panel["candidates"]], [str(self.official.pk)])
        self.assertEqual([s["member_id"] for s in panel["signups"]["waiting"]], [str(self.official.pk)])

        response = self.call("post", f"{EVENTS}/{self.game.pk}/officials", token, data={"member_id": str(self.official.pk)})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()["full"])
        self.assertError(self.call("post", f"{EVENTS}/{self.game.pk}/officials/external", token, data={"name": "Fed"}), 409, "cannot_assign")

        assignment = EventOfficial.objects.get(event=self.game)
        removed = self.call("delete", f"{EVENTS}/{self.game.pk}/officials/{assignment.pk}", token)
        self.assertEqual(removed.status_code, 200, removed.content)
        self.assertFalse(EventOfficial.objects.exists())

        external = self.call("post", f"{EVENTS}/{self.game.pk}/officials/external", token, data={"name": "Fed"})
        self.assertEqual(external.status_code, 201, external.content)
        self.assertTrue(external.json()["assigned"][0]["external"])
