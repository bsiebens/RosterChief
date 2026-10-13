"""Phase 2 of the app API: member endpoints, news, notifications, profiles and native
push delivery. Fixtures come from appapi.tests.AppApiTestCase: a parent (Pat) with a
child (Kim) in Ajax United, current season, signed in through allauth headless."""

import datetime
import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from django.utils.translation import get_language
from PIL import Image
from waffle import get_waffle_flag_model

from appapi.presenters import game_status
from appapi.scope import ClubScope
from club.models import Club, ClubMembership, OnboardingRequirement
from events.models import Attendance, Event, EventTask, EventTaskClaim, Lineup, LineupSelection, OfficialSignup, RefereeSignup
from events.services.agenda import member_agenda
from events.services.attendance import notify_newly_invited
from events.services.game_timeline import TimelineFetch
from events.services.game_timeline import store as store_game_timeline
from members.models import Family, FamilyMembership, Member
from mobile.models import PushSubscription
from mobile.services.push import send_push_for_notifications, send_push_to_user
from news.models import News
from notifications import outbox
from notifications.models import Notification, OutboxEntry
from notifications.services import notify_members
from shop.models import Order
from teams.models import Position, Team, TeamMembership

from .delivery import NativePushChannel, deliver, payload_data
from .models import LiveActivity, PushDevice
from .push import PushGateway
from .tests import CLUB_HOST, AppApiTestCase, make_club


class ClubApiTestCase(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.login()
        self.soon = timezone.now() + datetime.timedelta(days=3)

    def get(self, path, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return self.api("get", f"{path}?{query}" if query else path, token=self.token, host=CLUB_HOST)

    def send(self, method, path, data=None):
        return self.api(method, path, token=self.token, host=CLUB_HOST, data=data)

    def event(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("title", "Training")
        kwargs.setdefault("start", self.soon)
        return Event.objects.create(**kwargs)

    def invite(self, event, member=None, **kwargs):
        # update_or_create: adding a team to an event already invites its roster.
        return Attendance.objects.update_or_create(event=event, member=member or self.member, defaults=kwargs)[0]

    def stranger(self):
        """Someone in the same club but another household."""
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger")
        ClubMembership.objects.create(club=self.club, member=stranger, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        return stranger


class HomeTests(ClubApiTestCase):
    def test_home_cards(self):
        hero = self.event(title="Game day", kind=Event.EventKind.GAME)
        self.invite(hero, member=self.child)
        going = self.event(title="Party", start=self.soon + datetime.timedelta(days=1))
        self.invite(going, status=Attendance.AttendanceStatus.PRESENT)
        maybe = self.event(title="Tournament", start=self.soon + datetime.timedelta(days=2))
        self.invite(maybe, status=Attendance.AttendanceStatus.MAYBE)
        EventTask.objects.create(event=going, title="Bring fruit", needed_quantity=2)
        News.objects.create(club=self.club, title="Welcome", slug="welcome", body="Hello **all**", status=News.Status.PUBLISHED, published_at=timezone.now())

        body = self.get("/home").json()

        self.assertEqual(body["rsvp_needed"]["event"]["title"], "Game day")
        self.assertEqual(body["rsvp_needed"]["attendance"]["member_id"], str(self.child.pk))
        self.assertFalse(body["rsvp_needed"]["rsvp_closed"])
        self.assertEqual(body["next_going"]["event"]["title"], "Party")
        self.assertEqual([(row["type"], row["event"]["title"], row["status"]) for row in body["needs_answer"]], [("rsvp", "Tournament", "maybe")])
        self.assertEqual(body["open_tasks"][0]["title"], "Bring fruit")
        self.assertEqual(body["open_tasks"][0]["open_count"], 2)
        self.assertEqual(body["news"][0]["excerpt"], "Hello all")

    def cards(self, **params):
        return [(card["kind"], card["event"]["title"], sorted(row["member_id"] for row in card["members"])) for card in self.get("/home", **params).json()["member_cards"]]

    def test_every_person_gets_their_own_rsvp_and_next_up_cards(self):
        day = datetime.timedelta(days=1)
        self.invite(self.event(title="Kid's training", start=self.soon), member=self.child)
        self.invite(self.event(title="Parent's meeting", start=self.soon + day))
        self.invite(self.event(title="Kid's party", start=self.soon + 2 * day), member=self.child, status=Attendance.AttendanceStatus.PRESENT)
        self.invite(self.event(title="Parent's dinner", start=self.soon + 3 * day), status=Attendance.AttendanceStatus.SELECTED)

        self.assertEqual(
            self.cards(),
            [
                ("rsvp_needed", "Kid's training", [str(self.child.pk)]),
                ("rsvp_needed", "Parent's meeting", [str(self.member.pk)]),
                ("next_going", "Kid's party", [str(self.child.pk)]),
                ("next_going", "Parent's dinner", [str(self.member.pk)]),
            ],
        )

    def test_people_with_the_same_next_event_share_a_card(self):
        game = self.event(title="Family day")
        self.invite(game)
        self.invite(game, member=self.child)

        self.assertEqual(self.cards(), [("rsvp_needed", "Family day", sorted([str(self.member.pk), str(self.child.pk)]))])

    def test_a_card_is_the_soonest_only_and_the_rest_stay_in_needs_answer(self):
        first = self.event(title="First", start=self.soon)
        later = self.event(title="Later", start=self.soon + datetime.timedelta(days=1))
        self.invite(first)
        self.invite(later)

        body = self.get("/home").json()

        self.assertEqual([(kind, title) for kind, title, _members in self.cards()], [("rsvp_needed", "First")])
        # The card's own invitation is not repeated; the other one is still an ask.
        self.assertEqual([row["event"]["title"] for row in body["needs_answer"]], ["Later"])

    def test_a_closed_invitation_is_skipped_for_the_next_open_one(self):
        closed = self.event(title="Closed", start=self.soon, deadline=timezone.now() - datetime.timedelta(hours=1))
        self.invite(closed)
        self.invite(self.event(title="Open", start=self.soon + datetime.timedelta(days=1)))

        self.assertEqual([(kind, title) for kind, title, _members in self.cards()], [("rsvp_needed", "Open")])

    def test_cards_follow_member_id_and_can_be_empty(self):
        self.invite(self.event(title="Kid's game"), member=self.child)

        self.assertEqual(self.cards(member_id=self.member.pk), [])
        self.assertEqual(self.cards(member_id=self.child.pk), [("rsvp_needed", "Kid's game", [str(self.child.pk)])])

    def test_member_id_narrows_to_one_person(self):
        self.invite(self.event(title="Kid's game"), member=self.child)
        self.invite(self.event(title="Parents' meeting"))

        body = self.get("/home", member_id=self.member.pk).json()

        self.assertEqual(body["rsvp_needed"]["event"]["title"], "Parents' meeting")

    def test_unmanaged_member_id_is_404(self):
        self.assertError(self.get("/home", member_id=self.stranger().pk), 404, "not_found")

    def test_dues_are_money(self):
        membership = ClubMembership.objects.get(member=self.child)
        ClubMembership.objects.filter(pk=membership.pk).update(fee_amount="120.00", status=ClubMembership.StatusChoices.ACTIVE)

        dues = self.get("/home").json()["dues"]

        self.assertEqual(dues[0]["member_id"], str(self.child.pk))
        self.assertEqual(dues[0]["balance"], {"amount": "120.00", "currency": "EUR"})


class CalendarTests(ClubApiTestCase):
    def test_agenda_merges_invites_signups_and_blocked_events(self):
        training = self.event(title="Training", kind=Event.EventKind.TRAINING)
        self.invite(training, member=self.child, status=Attendance.AttendanceStatus.PRESENT)
        game = self.event(title="Home game", kind=Event.EventKind.GAME, start=self.soon + datetime.timedelta(days=1))
        signup = RefereeSignup.objects.create(event=game, member=self.member)
        self.invite(self.event(title="Cancelled", cancelled=True))

        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        TeamMembership.objects.create(team=team, member=self.child, season=self.season, position=Position.objects.create(club=self.club, name="Forward", short_name="F"))
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        cup = self.event(title="Cup game", kind=Event.EventKind.GAME, start=self.soon + datetime.timedelta(days=2))
        cup.teams.add(team)

        items = self.get("/calendar").json()["items"]

        self.assertEqual([(item["type"], item["event"]["title"]) for item in items], [("rsvp", "Training"), ("referee", "Home game"), ("blocked", "Cup game")])
        self.assertEqual(items[0]["status"], "present")
        self.assertEqual(items[1]["signup_id"], str(signup.pk))
        self.assertEqual(items[2]["blocked_requirements"], ["Medical certificate"])
        self.assertEqual(items[2]["event"]["teams"], [{"id": str(team.pk), "name": "U12", "short_name": "U12"}])

    def test_window_and_kind(self):
        self.invite(self.event(title="Soon training", kind=Event.EventKind.TRAINING))
        self.invite(self.event(title="Soon game", kind=Event.EventKind.GAME, start=self.soon + datetime.timedelta(hours=1)))
        self.invite(self.event(title="Far away", start=timezone.now() + datetime.timedelta(days=120)))

        self.assertEqual([item["event"]["title"] for item in self.get("/calendar").json()["items"]], ["Soon training", "Soon game"])
        self.assertEqual([item["event"]["title"] for item in self.get("/calendar", kind="game").json()["items"]], ["Soon game"])
        later = (timezone.now() + datetime.timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual([item["event"]["title"] for item in self.get("/calendar", **{"from": later}).json()["items"]], ["Far away"])

    def test_looking_back_leaves_out_games_only_refereed(self):
        now = timezone.now()
        played = self.event(title="Played", kind=Event.EventKind.GAME, start=now - datetime.timedelta(days=2))
        refereed = self.event(title="Refereed", kind=Event.EventKind.GAME, start=now - datetime.timedelta(days=2))
        ahead = self.event(title="Refereeing ahead", kind=Event.EventKind.GAME, start=now + datetime.timedelta(days=2))
        self.invite(played, member=self.child)
        for event in (refereed, ahead):
            RefereeSignup.objects.create(event=event, member=self.member, status=RefereeSignup.Status.ACCEPTED)

        items = self.get("/calendar", **{"from": (now - datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}).json()["items"]

        self.assertEqual([(item["type"], item["event"]["title"]) for item in items], [("rsvp", "Played"), ("referee", "Refereeing ahead")])

    def test_looking_back_covers_invited_and_official_signups_too(self):
        # "Scheduled but never joined" is an INVITED sign-up; officials go through the same filter.
        now = timezone.now()
        past = self.event(title="Past", kind=Event.EventKind.GAME, start=now - datetime.timedelta(days=3))
        no_end = self.event(title="Past, no end", kind=Event.EventKind.GAME, start=now - datetime.timedelta(days=4))
        Event.objects.filter(pk=no_end.pk).update(end=None)
        ahead = self.event(title="Ahead", kind=Event.EventKind.GAME, start=now + datetime.timedelta(days=1))
        RefereeSignup.objects.create(event=past, member=self.member)
        OfficialSignup.objects.create(event=past, member=self.member)
        OfficialSignup.objects.create(event=no_end, member=self.member)
        OfficialSignup.objects.create(event=ahead, member=self.member)

        rows = member_agenda(self.club, [self.member], officials_enabled=True, start=now - datetime.timedelta(days=10))

        self.assertEqual([(row["type"], row["event"].title) for row in rows], [("official", "Ahead")])

    def test_looking_back_keeps_past_trainings_and_other_events_you_are_invited_to(self):
        now = timezone.now()
        past = now - datetime.timedelta(days=3)
        kinds = [Event.EventKind.TRAINING, Event.EventKind.GAME, Event.EventKind.TOURNAMENT, Event.EventKind.MEETING, Event.EventKind.SOCIAL, Event.EventKind.OTHER]
        for kind in kinds:
            self.invite(self.event(title=f"Past {kind}", kind=kind, start=past))
        # Even a past game you also refereed stays, as the player invitation.
        refereed = self.event(title="Played and refereed", kind=Event.EventKind.GAME, start=past)
        self.invite(refereed)
        RefereeSignup.objects.create(event=refereed, member=self.member, status=RefereeSignup.Status.ACCEPTED)

        from_ = (now - datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        items = self.get("/calendar", **{"from": from_}).json()["items"]

        self.assertEqual(sorted(item["event"]["title"] for item in items), sorted([*(f"Past {kind}" for kind in kinds), "Played and refereed"]))
        self.assertEqual({item["type"] for item in items}, {"rsvp"})
        self.assertEqual([item["event"]["title"] for item in self.get("/calendar", kind="training", **{"from": from_}).json()["items"]], ["Past training"])

    def test_a_declined_sign_up_stays_until_the_game_starts(self):
        now = timezone.now()
        hour = datetime.timedelta(hours=1)
        upcoming = self.event(title="Upcoming", kind=Event.EventKind.GAME, start=now + 2 * hour)
        started = self.event(title="Started", kind=Event.EventKind.GAME, start=now - hour)
        Event.objects.filter(pk=started.pk).update(end=now + hour)  # still being played
        for event in (upcoming, started):
            RefereeSignup.objects.create(event=event, member=self.member, status=RefereeSignup.Status.DECLINED)
            OfficialSignup.objects.create(event=event, member=self.member, status=OfficialSignup.Status.DECLINED)
        from_ = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        with patch.object(ClubScope, "feature", return_value=True):
            items = self.get("/calendar", **{"from": from_}).json()["items"]

        # Kept (as "declined") while the game is still ahead; dropped once it has started.
        self.assertEqual([(item["type"], item["event"]["title"], item["status"]) for item in items], [("referee", "Upcoming", "declined"), ("official", "Upcoming", "declined")])

    def test_the_web_agenda_still_leaves_declined_sign_ups_out(self):
        game = self.event(title="Game", kind=Event.EventKind.GAME)
        RefereeSignup.objects.create(event=game, member=self.member, status=RefereeSignup.Status.DECLINED)

        self.assertEqual(member_agenda(self.club, [self.member]), [])
        self.assertEqual([row["type"] for row in member_agenda(self.club, [self.member], include_declined=True)], ["referee"])

    def test_game_status(self):
        now = timezone.now()
        hour = datetime.timedelta(hours=1)
        games = {
            "Upcoming": self.event(title="Upcoming", kind=Event.EventKind.GAME, start=now + 2 * hour),
            "Live": self.event(title="Live", kind=Event.EventKind.GAME, start=now - hour, is_live=True),
            "Played": self.event(title="Played", kind=Event.EventKind.GAME, start=now - datetime.timedelta(days=1), score_for=3, score_against=1),
        }
        training = self.event(title="Practice", kind=Event.EventKind.TRAINING, start=now + hour)
        for event in [*games.values(), training]:
            self.invite(event)

        statuses = {item["event"]["title"]: item["event"]["game_status"] for item in self.get("/calendar", **{"from": (now - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}).json()["items"]}

        self.assertEqual(statuses, {"Upcoming": "scheduled", "Live": "live", "Played": "finished", "Practice": None})
        # The calendar never lists cancelled events, so check that branch on the helper.
        self.assertEqual(game_status(Event(kind=Event.EventKind.GAME, start=now, cancelled=True)), "cancelled")

    def test_window_too_long_is_422(self):
        start = timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (timezone.now() + datetime.timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        response = self.get("/calendar", **{"from": start, "to": end})
        self.assertError(response, 422, "validation_error")
        self.assertIn("to", response.json()["error"]["fields"])


class EventDetailTests(ClubApiTestCase):
    def test_family_view_of_an_event(self):
        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        TeamMembership.objects.create(team=team, member=self.child, season=self.season, jersey_number=7)
        event = self.event(title="Game", kind=Event.EventKind.GAME)
        event.teams.add(team)
        self.invite(event, member=self.child, status=Attendance.AttendanceStatus.ABSENT, note="Sick")
        self.invite(event, member=self.stranger(), status=Attendance.AttendanceStatus.PRESENT, note="secret")
        task = EventTask.objects.create(event=event, title="Drive", needed_quantity=1)
        EventTaskClaim.objects.create(task=task, member=self.member)

        body = self.get(f"/events/{event.pk}").json()

        self.assertEqual(body["answers"], [{"member_id": str(self.child.pk), "status": "absent", "note": "Sick", "can_drop_out": False, "team": {"team_id": str(team.pk), "position": None, "jersey_number": 7}}])
        self.assertEqual(body["squad_summary"], {"total": 2, "responded": 2, "in_count": 1, "out_count": 1, "no_reply_count": 0})
        self.assertNotIn("secret", str(body))
        self.assertEqual(body["tasks"][0]["claimed_by"], ["Parent family"])
        self.assertTrue(body["tasks"][0]["claimed_by_me"])
        self.assertTrue(body["tasks"][0]["is_full"])
        self.assertIsNone(body["lineup"])

    def test_published_lineup_and_dropout_flag(self):
        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        event = self.event(title="Game", kind=Event.EventKind.GAME)
        event.teams.add(team)
        self.invite(event, member=self.child, status=Attendance.AttendanceStatus.SELECTED)
        lineup = Lineup.objects.create(event=event, team=team, published_at=timezone.now())
        LineupSelection.objects.create(lineup=lineup, member=self.child)

        body = self.get(f"/events/{event.pk}").json()

        self.assertEqual(body["lineup"]["categories"][0]["members"], [{"id": str(self.child.pk), "first_name": "Kim", "last_name": "Parent"}])
        self.assertTrue(body["answers"][0]["can_drop_out"])

    def test_a_games_timeline_always_carries_names_for_members(self):
        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        game = self.event(title="Game", kind=Event.EventKind.GAME)
        game.teams.add(team)
        goal = {"id": "g1", "type": "goal", "side": "away", "period": "1", "seconds": 61, "time": "01:01", "goal": {"scorer": {"name": "Sam Scorer", "number": 9}, "assists": []}}
        store_game_timeline(game, TimelineFetch(document={"events": [goal]}))

        self.assertIsNone(self.get(f"/events/{game.pk}").json()["game_timeline"])  # feature off

        get_waffle_flag_model().objects.get_or_create(name="game_timeline")[0].clubs.add(self.club)
        cache.clear()
        timeline = self.get(f"/events/{game.pk}").json()["game_timeline"]

        self.assertEqual(timeline["events"][0]["goal"]["scorer"], {"name": "Sam Scorer", "number": 9})

    def test_choosing_teams_to_follow_for_live_game_updates(self):
        seniors = Team.objects.create(club=self.club, name="Seniors", short_name="SEN")
        Team.objects.create(club=self.club, name="U12", short_name="U12")

        self.assertError(self.get("/game-follows"), 404, "feature_disabled")

        get_waffle_flag_model().objects.get_or_create(name="game_timeline")[0].clubs.add(self.club)
        cache.clear()
        self.assertEqual([team["following"] for team in self.get("/game-follows").json()["teams"]], [False, False])

        body = self.send("put", "/game-follows", {"team_ids": [str(seniors.pk)]}).json()

        self.assertEqual([(team["name"], team["following"]) for team in body["teams"]], [("Seniors", True), ("U12", False)])
        self.assertEqual([team["following"] for team in self.send("put", "/game-follows", {"team_ids": []}).json()["teams"]], [False, False])

    def test_live_activity_tokens_are_registered_per_install_and_game(self):
        get_waffle_flag_model().objects.get_or_create(name="game_timeline")[0].clubs.add(self.club)
        cache.clear()
        game = self.event(title="Game", kind=Event.EventKind.GAME)
        device = PushDevice.objects.create(user=self.user, installation_id="parent-phone", platform="ios", push_token="t")
        body = {"installation_id": "parent-phone", "activity_token": "aa11"}

        first = self.send("post", f"/events/{game.pk}/live-activity", body)
        self.send("post", f"/events/{game.pk}/live-activity", {**body, "activity_token": "bb22"})

        self.assertEqual(first.json(), {"event_id": str(game.pk), "installation_id": "parent-phone"})
        self.assertEqual(list(LiveActivity.objects.values_list("token", "device", "club")), [("bb22", device.pk, self.club.pk)])
        self.assertEqual(self.send("delete", f"/events/{game.pk}/live-activity/parent-phone").status_code, 204)
        self.assertFalse(LiveActivity.objects.exists())

    def test_live_activity_rules(self):
        game = self.event(title="Game", kind=Event.EventKind.GAME)
        training = self.event(title="Training")
        PushDevice.objects.create(user=self.user, installation_id="parent-phone", platform="ios", push_token="t")
        other = get_user_model().objects.create_user(email="other@example.com", password="x")
        PushDevice.objects.create(user=other, installation_id="someone-elses", platform="ios", push_token="t")
        body = {"installation_id": "parent-phone", "activity_token": "aa11"}

        self.assertError(self.send("post", f"/events/{game.pk}/live-activity", body), 404, "feature_disabled")
        get_waffle_flag_model().objects.get_or_create(name="game_timeline")[0].clubs.add(self.club)
        cache.clear()
        self.assertError(self.send("post", f"/events/{training.pk}/live-activity", body), 422, "not_a_game")
        self.assertError(self.send("post", f"/events/{game.pk}/live-activity", {**body, "installation_id": "someone-elses"}), 404, "not_found")
        self.assertEqual(self.send("delete", f"/events/{game.pk}/live-activity/nothing").status_code, 204)

    def test_another_clubs_event_is_404(self):
        rival = make_club(name="Rival FC", slug="rival-fc")
        event = Event.objects.create(club=rival, title="Theirs", start=self.soon)
        self.assertError(self.get(f"/events/{event.pk}"), 404, "not_found")


class RsvpTests(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        self.game = self.event(title="Game", kind=Event.EventKind.GAME)
        self.attendance = self.invite(self.game, member=self.child)

    def rsvp(self, **data):
        return self.send("post", f"/events/{self.game.pk}/rsvp", {"member_id": str(self.child.pk), **data})

    def test_answer_and_repeat(self):
        for _attempt in range(2):
            response = self.rsvp(status="present")
            self.assertEqual(response.json(), {"member_id": str(self.child.pk), "status": "present", "note": ""})
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, "present")

    def test_absent_needs_a_reason(self):
        response = self.rsvp(status="absent", note="...")
        self.assertError(response, 422, "note_required")
        self.assertIn("note", response.json()["error"]["fields"])
        self.assertEqual(self.rsvp(status="absent", note="Sick").json()["note"], "Sick")

    def test_deadline_and_start(self):
        Event.objects.filter(pk=self.game.pk).update(deadline=timezone.now() - datetime.timedelta(hours=1))
        self.assertError(self.rsvp(status="present"), 409, "rsvp_closed")
        Event.objects.filter(pk=self.game.pk).update(start=timezone.now() - datetime.timedelta(minutes=1))
        self.assertError(self.rsvp(status="present"), 409, "event_started")

    def test_dropout(self):
        self.assertError(self.rsvp(status="dropout", note="Injured"), 409, "not_in_lineup")
        Attendance.objects.filter(pk=self.attendance.pk).update(status=Attendance.AttendanceStatus.SELECTED)
        Event.objects.filter(pk=self.game.pk).update(deadline=timezone.now() - datetime.timedelta(hours=1))
        self.assertEqual(self.rsvp(status="dropout", note="Injured").json()["status"], "absent")

    def test_uninvited_and_unmanaged_are_404(self):
        self.assertError(self.send("post", f"/events/{self.game.pk}/rsvp", {"member_id": str(self.member.pk), "status": "present"}), 404, "not_found")
        stranger = self.stranger()
        self.invite(self.game, member=stranger)
        self.assertError(self.send("post", f"/events/{self.game.pk}/rsvp", {"member_id": str(stranger.pk), "status": "present"}), 404, "not_found")

    def test_unknown_status_is_422(self):
        self.assertError(self.rsvp(status="yes"), 422, "validation_error")


class SignupAndTaskTests(ClubApiTestCase):
    def test_decline_a_referee_invite(self):
        game = self.event(title="Game", kind=Event.EventKind.GAME)
        signup = RefereeSignup.objects.create(event=game, member=self.child)

        response = self.send("post", f"/events/{game.pk}/referee-signups/{signup.pk}/respond", {"response": "decline"})

        self.assertEqual(response.json()["status"], "declined")

    def test_accept_refused_is_409(self):
        game = self.event(title="Away game", kind=Event.EventKind.GAME)  # not a home game: nobody from the club referees it
        signup = RefereeSignup.objects.create(event=game, member=self.child)

        self.assertError(self.send("post", f"/events/{game.pk}/referee-signups/{signup.pk}/respond", {"response": "accept"}), 409, "signup_unavailable")
        signup.refresh_from_db()
        self.assertEqual(signup.status, RefereeSignup.Status.INVITED)

    def test_someone_elses_signup_is_404(self):
        game = self.event(title="Game", kind=Event.EventKind.GAME)
        signup = RefereeSignup.objects.create(event=game, member=self.stranger())
        self.assertError(self.send("post", f"/events/{game.pk}/referee-signups/{signup.pk}/respond", {"response": "decline"}), 404, "not_found")

    def test_officials_need_the_feature(self):
        game = self.event(title="Game", kind=Event.EventKind.GAME)
        self.assertError(self.send("post", f"/events/{game.pk}/official-signups/{game.pk}/respond", {"response": "decline"}), 404, "feature_disabled")

    def test_claim_and_unclaim(self):
        event = self.event()
        task = EventTask.objects.create(event=event, title="Drive", needed_quantity=1)

        claimed = self.send("post", f"/events/{event.pk}/tasks/{task.pk}/claim")
        self.assertTrue(claimed.json()["claimed_by_me"])
        self.assertError(self.send("post", f"/events/{event.pk}/tasks/{task.pk}/claim"), 409, "task_unavailable")

        released = self.send("delete", f"/events/{event.pk}/tasks/{task.pk}/claim")
        self.assertEqual(released.json()["claimed_by"], [])
        self.assertEqual(self.send("delete", f"/events/{event.pk}/tasks/{task.pk}/claim").status_code, 200)


class NewsTests(ClubApiTestCase):
    def news(self, slug, **kwargs):
        kwargs.setdefault("status", News.Status.PUBLISHED)
        kwargs.setdefault("published_at", timezone.now() - datetime.timedelta(hours=1))
        return News.objects.create(club=self.club, title=kwargs.pop("title", slug), slug=slug, body=kwargs.pop("body", "Body"), **kwargs)

    def test_feed_pages_newest_first_and_hides_non_member_items(self):
        for index in range(3):
            self.news(f"item-{index}", published_at=timezone.now() - datetime.timedelta(hours=index + 1))
        self.news("public-only", visibility=News.Visibility.EXTERNAL)
        self.news("draft", status=News.Status.DRAFT)
        self.news("scheduled", published_at=timezone.now() + datetime.timedelta(days=1))

        first = self.get("/news", limit=2).json()
        second = self.get("/news", limit=2, cursor=first["next_cursor"]).json()

        self.assertEqual([item["slug"] for item in first["items"] + second["items"]], ["item-0", "item-1", "item-2"])
        self.assertIsNone(second["next_cursor"])

    def test_detail_follows_accept_language(self):
        self.news("hello", title="Hallo", body="Dag **iedereen**", title_en="Hello", body_en="Hi everyone")

        dutch = self.api("get", "/news/hello", token=self.token, host=CLUB_HOST, HTTP_ACCEPT_LANGUAGE="nl").json()
        english = self.api("get", "/news/hello", token=self.token, host=CLUB_HOST, HTTP_ACCEPT_LANGUAGE="en").json()

        self.assertEqual((dutch["title"], dutch["language"]), ("Hallo", "default"))
        self.assertIn("<strong>iedereen</strong>", dutch["body_html"])
        self.assertEqual((english["title"], english["language"], english["body_html"]), ("Hello", "en", "<p>Hi everyone</p>"))

    def test_author(self):
        self.news("by-pat", created_by=self.member)
        self.news("anonymous")

        items = {item["slug"]: item["author"] for item in self.get("/news").json()["items"]}

        self.assertEqual(items, {"by-pat": {"first_name": "Pat", "last_name": "Parent"}, "anonymous": None})
        self.assertEqual(self.get("/news/by-pat").json()["author"], {"first_name": "Pat", "last_name": "Parent"})

    def test_scheduled_item_is_404(self):
        self.news("later", published_at=timezone.now() + datetime.timedelta(days=1))
        self.assertError(self.get("/news/later"), 404, "not_found")


class NotificationTests(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        self.item = News.objects.create(club=self.club, title="News", slug="news", body="…", status=News.Status.PUBLISHED, published_at=timezone.now())
        self.mine = Notification.objects.create(club=self.club, member=self.child, title="New article", body="Read it", source=self.item)
        self.theirs = Notification.objects.create(club=self.club, member=self.stranger(), title="Not yours", body="…")

    def test_inbox_and_badge(self):
        body = self.get("/notifications").json()

        self.assertEqual([item["title"] for item in body["items"]], ["New article"])
        self.assertEqual(body["items"][0]["source"], {"type": "news", "id": str(self.item.pk), "slug": "news"})
        self.assertEqual(self.get("/notifications/unread-count").json(), {"unread": 1})

    def test_mark_read_and_clear(self):
        self.assertIsNotNone(self.send("post", f"/notifications/{self.mine.pk}/read").json()["read_at"])
        self.assertError(self.send("post", f"/notifications/{self.theirs.pk}/read"), 404, "not_found")
        self.assertEqual(self.send("post", "/notifications/read-all").json(), {"count": 0})
        self.assertEqual(self.send("delete", "/notifications").json(), {"count": 1})
        self.assertTrue(Notification.objects.filter(pk=self.theirs.pk).exists())


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class ProfileTests(ClubApiTestCase):
    def test_child_profile(self):
        body = self.get(f"/members/{self.child.pk}").json()

        self.assertEqual((body["first_name"], body["date_of_birth"]), ("Kim", "2014-05-01"))
        self.assertEqual(body["guardians"], [{"first_name": "Pat", "last_name": "Parent", "role": "parent"}])
        self.assertIsNone(body["photo_url"])

    def test_strangers_profile_is_404(self):
        self.assertError(self.get(f"/members/{self.stranger().pk}"), 404, "not_found")

    def test_patch(self):
        body = self.send("patch", f"/members/{self.child.pk}", {"phone": "+32 470 12 34 56", "photo_public_consent": True}).json()

        self.assertEqual(body["phone"], "+32470123456")
        self.assertTrue(body["photo_public_consent"])
        self.assertEqual(body["first_name"], "Kim")

    def test_patch_validation(self):
        response = self.send("patch", f"/members/{self.child.pk}", {"phone": "not a number"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("phone", response.json()["error"]["fields"])

    def test_profile_picture_is_served_and_outsiders_get_404(self):
        self.child.profile_photo = SimpleUploadedFile("kim.png", png_bytes(), content_type="image/png")
        self.child.save()
        stranger = self.stranger()
        stranger.profile_photo = SimpleUploadedFile("sam.png", png_bytes(), content_type="image/png")
        stranger.save()
        outsider = Member.objects.create(first_name="Olga", last_name="Outsider", profile_photo=SimpleUploadedFile("o.png", png_bytes(), content_type="image/png"))

        body = self.get(f"/members/{self.child.pk}").json()
        response = self.api("get", body["photo_url"].split("/api/app/v1", 1)[1], token=self.token, host=CLUB_HOST)

        self.assertEqual(body["photo_url"], body["profile_photo_url"].replace("&kind=profile", ""))
        self.assertIsNone(body["player_photo_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Cache-Control"], "private, max-age=3600")
        self.assertEqual(self.get(f"/members/{stranger.pk}/photo").status_code, 200)  # club-wide
        self.assertError(self.get(f"/members/{outsider.pk}/photo"), 404, "not_found")

    def test_teammate_sees_the_player_picture_as_fallback_but_not_directly(self):
        stranger = self.stranger()
        stranger.player_photo = SimpleUploadedFile("sam.png", png_bytes(), content_type="image/png")
        stranger.save()

        self.assertEqual(self.get(f"/members/{stranger.pk}/photo").status_code, 200)
        self.assertError(self.get(f"/members/{stranger.pk}/photo?kind=player"), 404, "not_found")

    def test_effective_picture_falls_back_to_the_player_picture(self):
        self.child.player_photo = SimpleUploadedFile("official.png", png_bytes(), content_type="image/png")
        self.child.save()

        body = self.get(f"/members/{self.child.pk}").json()

        self.assertIsNotNone(body["photo_url"])
        self.assertIsNone(body["profile_photo_url"])
        self.assertIn("kind=player", body["player_photo_url"])
        self.assertEqual(self.get(f"/members/{self.child.pk}/photo").status_code, 200)
        self.assertError(self.get(f"/members/{self.child.pk}/photo?kind=profile"), 404, "not_found")
        self.assertEqual(self.get(f"/members/{self.child.pk}/photo?kind=player").status_code, 200)

    def test_profile_picture_wins_over_the_player_picture(self):
        self.child.player_photo = SimpleUploadedFile("official.png", png_bytes(), content_type="image/png")
        self.child.profile_photo = SimpleUploadedFile("kim.png", png_bytes(), content_type="image/png")
        self.child.save()

        body = self.get(f"/members/{self.child.pk}").json()

        self.assertNotIn("kind=", body["photo_url"])
        self.assertIn("kind=profile", body["profile_photo_url"])
        self.assertNotEqual(body["profile_photo_url"], body["player_photo_url"])


class RecordingPublisher(PushGateway):
    sent: list = []
    badges: list = []
    threads: list = []
    dead: set = set()

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
        RecordingPublisher.sent.append((device.installation_id, title, data))
        RecordingPublisher.badges.append(badge)
        RecordingPublisher.threads.append(thread_id)
        return device.installation_id not in RecordingPublisher.dead


@override_settings(APP_PUSH_GATEWAY="appapi.test_member.RecordingPublisher")
class PushDeliveryTests(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        RecordingPublisher.sent = []
        RecordingPublisher.badges = []
        RecordingPublisher.threads = []
        RecordingPublisher.dead = set()
        self.device = PushDevice.objects.create(user=self.user, installation_id="parent-phone", platform="ios", push_token="t")

    def test_one_push_per_account_for_a_family_wide_notification(self):
        second_child = Member.objects.create(first_name="Lou", last_name="Parent")
        FamilyMembership.objects.create(family=Family.objects.get(club=self.club), member=second_child, role=FamilyMembership.FamilyRole.CHILD)
        item = News.objects.create(club=self.club, title="News", slug="news", body="…", status=News.Status.PUBLISHED, published_at=timezone.now())
        with override_settings(NOTIFICATIONS_DISPATCH_EAGER=False), patch("notifications.outbox.run_in_background"):
            notifications = notify_members([self.member, self.child, second_child], club=self.club, title="New article", body="Read it", source=item, send_email=False)

        self.assertEqual(deliver([notification.pk for notification in notifications]), 1)
        installation, title, data = RecordingPublisher.sent[0]
        self.assertEqual((installation, title), ("parent-phone", "New article"))
        self.assertEqual(data["club_slug"], "ajax-united")
        self.assertEqual(RecordingPublisher.threads, ["ajax-united"])
        self.assertEqual(data["target"], {"type": "news", "id": str(item.pk), "slug": "news"})

    def test_the_push_is_in_the_language_the_device_registered_with(self):
        self.device.locale = "nl"
        self.device.save()
        with override_settings(NOTIFICATIONS_DISPATCH_EAGER=False), patch("notifications.outbox.run_in_background"):
            (notification,) = notify_members([self.member], club=self.club, title=lambda: "Hello" if get_language() == "en" else "Hallo", body="Body", send_email=False)

        deliver([notification.pk])

        _installation, title, data = RecordingPublisher.sent[0]
        self.assertEqual(title, "Hallo")
        self.assertEqual(data["kind"], "notification")
        self.assertEqual(data["notification_id"], str(notification.pk))

    def test_guardians_get_their_childs_notification(self):
        notification = Notification.objects.create(club=self.club, member=self.child, title="Hi", body="…")
        self.assertEqual(deliver([notification.pk]), 1)

    def test_dead_token_disables_the_device(self):
        RecordingPublisher.dead = {"parent-phone"}
        notification = Notification.objects.create(club=self.club, member=self.member, title="Hi", body="…")

        self.assertEqual(deliver([notification.pk]), 0)
        self.device.refresh_from_db()
        self.assertFalse(self.device.enabled)

    @override_settings(NOTIFICATIONS_DISPATCH_EAGER=False)
    def test_created_notifications_are_pushed_after_commit(self):
        with patch("notifications.outbox.run_in_background") as background, self.captureOnCommitCallbacks(execute=False) as callbacks:
            notifications = notify_members([self.child], club=self.club, title="Hi", body="…", send_email=False)
        background.assert_not_called()
        self.assertEqual(RecordingPublisher.sent, [])
        with patch("notifications.outbox.run_in_background") as background:
            for callback in callbacks:
                callback()
        entry = OutboxEntry.objects.get()
        self.assertEqual(entry.payload, {"ids": [str(notifications[0].pk)], "email": False})
        background.assert_called_once_with(outbox._process_all, [entry.pk])

    def test_eager_dispatch_pushes_straight_away(self):
        notify_members([self.child], club=self.club, title="Hi", body="…", send_email=False)
        self.assertEqual(len(RecordingPublisher.sent), 1)

    @override_settings(APP_PUSH_GATEWAY="")
    def test_native_channel_does_nothing_without_a_gateway(self):
        notification = Notification.objects.create(club=self.club, member=self.child, title="Hi", body="…")
        NativePushChannel().deliver_notifications([notification])
        self.assertEqual(RecordingPublisher.sent, [])


class UnknownIdTests(ClubApiTestCase):
    """API.md §13: every route that takes an id answers an unknown (or another club's)
    id with the 404 envelope -- walked from the route table, so a new endpoint is
    covered without anyone remembering to add a test."""

    BODIES = {
        "rsvp": {"member_id": "00000000-0000-0000-0000-000000000000", "status": "present"},
        "respond": {"response": "decline"},
        "{member_id}": {},
        "{item_id}": {"quantity": 1},
        "live-activity": {"installation_id": "unknown-install", "activity_token": "x"},
        "check-ins": {"items": []},
        "/status": {"status": "present"},
        "{event_id}": {},
        "{series_id}": {},
        "{membership_id}": {},
        "/roster/players": {"member_ids": ["00000000-0000-0000-0000-000000000001"]},
        "/roster/staff": {"member_ids": ["00000000-0000-0000-0000-000000000001"], "position_id": "00000000-0000-0000-0000-000000000002"},
        "/lineup": {"selected_member_ids": []},
        "/lineup/publish": {"mode": "cancel"},
        "/events": {"kind": "training", "start": "2030-01-01T10:00:00Z"},
        "/series": {"title": "Training", "kind": "training", "dtstart": "2030-01-01T10:00:00Z", "frequency": "monthly"},
        "/locations": {"name": "Rink", "address": "1 Ice Rd", "city": "Antwerp", "zip_code": "2000"},
        "/opponents": {"name": "Ajax"},
    }

    def test_unknown_ids_are_404_everywhere(self):
        import re
        import uuid as uuid_module

        from .api import app_api

        routes = [(path, method) for _prefix, router in app_api._routers for path, view in router.path_operations.items() for operation in view.operations for method in operation.methods if "{" in path]
        self.assertTrue(routes)
        for path, method in routes:
            concrete = re.sub(r"\{(\w+)\}", lambda match: "unknown-slug" if match.group(1) in ("slug", "installation_id") else str(uuid_module.uuid4()), path)
            host = "rosterchief.app" if path.startswith("/devices") else CLUB_HOST
            data = next((body for key, body in self.BODIES.items() if path.endswith(key)), None) if method in ("POST", "PATCH", "PUT") else None
            with self.subTest(method=method, path=path):
                response = self.api(method.lower(), concrete, token=self.token, host=host, data=data)
                self.assertError(response, 404, response.json()["error"]["code"] if response.status_code == 404 else "not_found")


class HomeSummaryCompatibilityTests(ClubApiTestCase):
    """The web app shows one hero and everything else in `needs_answer`: keeping every other
    person's open invitation there (while the API moves them to `member_cards`) is what
    stops the PWA from losing them."""

    def test_the_web_summary_keeps_other_peoples_invitations_in_needs_answer(self):
        from mobile.services.home import home_summary

        self.invite(self.event(title="Kid's training", start=self.soon), member=self.child)
        self.invite(self.event(title="Parent's meeting", start=self.soon + datetime.timedelta(days=1)))
        people = [self.member, self.child]

        web = home_summary(self.club, people, officials_enabled=False)
        api = home_summary(self.club, people, officials_enabled=False, exclude_card_rows=True)

        self.assertEqual(web["hero_attendance"].event.title, "Kid's training")
        self.assertEqual([row["event"].title for row in web["needs_answer"]], ["Parent's meeting"])
        self.assertEqual(api["needs_answer"], [])
        self.assertEqual([card["event"].title for card in api["member_cards"]], ["Kid's training", "Parent's meeting"])


class ClubSportTypeTests(ClubApiTestCase):
    def test_context_and_clubs_expose_the_clubs_sport(self):
        Club.objects.filter(pk=self.club.pk).update(sport_type=Club.SportType.ICE_HOCKEY)

        context = self.get("/context").json()["club"]
        clubs = self.api("get", "/clubs", token=self.token).json()["items"]

        self.assertEqual(context["sport_type"], "ice_hockey")
        self.assertEqual([club["sport_type"] for club in clubs if club["slug"] == self.club.slug], ["ice_hockey"])

    def test_a_club_without_a_chosen_sport_is_other(self):
        self.assertEqual(self.get("/context").json()["club"]["sport_type"], "other")


class NotificationTargetTests(ClubApiTestCase):
    """What the app opens: `target` in the push payload and `source` in the inbox share one shape."""

    def test_an_order_notification_points_at_the_order(self):
        order = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("12.00"))
        notification = Notification.objects.create(club=self.club, member=self.member, title="Order placed", body="…", source=order)

        self.assertEqual(payload_data(notification)["target"], {"type": "order", "id": str(order.pk)})
        inbox = self.get("/notifications").json()["items"][0]["source"]
        self.assertEqual((inbox["type"], inbox["id"]), ("order", str(order.pk)))

    def test_new_events_on_your_calendar_points_at_the_calendar(self):
        game = self.event(title="Game")
        notify_newly_invited(self.member, club=self.club, events=[game])
        notification = Notification.objects.get(member=self.member)

        self.assertEqual(payload_data(notification)["target"], {"type": "calendar"})
        self.assertEqual(self.get("/notifications").json()["items"][0]["source"], {"type": "calendar", "id": None, "slug": None})

    def test_a_source_wins_over_a_target_type(self):
        game = self.event(title="Game")
        notification = Notification.objects.create(club=self.club, member=self.member, title="Hi", body="…", source=game, target_type="calendar")

        self.assertEqual(payload_data(notification)["target"]["type"], "event")

    def test_no_source_and_no_target_type_means_no_target(self):
        notification = Notification.objects.create(club=self.club, member=self.member, title="Hi", body="…")

        self.assertIsNone(payload_data(notification)["target"])


@override_settings(VAPID_PRIVATE_KEY="test-private-key", VAPID_ADMIN_EMAIL="admin@example.com")
class WebPushDedupeTests(ClubApiTestCase):
    """The PWA's web push sits out for an account whose phone gets the native push."""

    def setUp(self):
        super().setUp()
        PushSubscription.objects.create(club=self.club, member=self.member, endpoint="https://fcm.googleapis.com/fcm/send/mine", p256dh="k", auth="a")
        self.notification = Notification.objects.create(club=self.club, member=self.member, title="Hi", body="…")

    def web_pushes(self):
        with patch("mobile.services.push._deliver") as deliver_web:
            send_push_for_notifications([self.notification])
        return deliver_web.call_count

    def native_device(self, **kwargs):
        return PushDevice.objects.create(user=self.user, installation_id="phone", platform="ios", push_token="t", **kwargs)

    def test_web_push_goes_out_when_there_is_no_native_device(self):
        self.assertEqual(self.web_pushes(), 1)

    def test_web_push_is_skipped_for_an_account_with_an_active_native_device(self):
        self.native_device()

        self.assertEqual(self.web_pushes(), 0)

    def test_a_disabled_device_does_not_suppress_web_push(self):
        self.native_device(enabled=False)

        self.assertEqual(self.web_pushes(), 1)

    def test_a_long_unseen_device_no_longer_suppresses_web_push(self):
        device = self.native_device()
        PushDevice.objects.filter(pk=device.pk).update(last_seen=timezone.now() - datetime.timedelta(days=61))

        self.assertEqual(self.web_pushes(), 1)

    def test_live_game_web_push_is_skipped_too(self):
        self.native_device()

        with patch("mobile.services.push._deliver") as deliver_web:
            sent = send_push_to_user(self.club, self.user, title="Goal", body="1-0")

        self.assertEqual((sent, deliver_web.call_count), (0, 0))


@override_settings(VAPID_PRIVATE_KEY="test-private-key", VAPID_ADMIN_EMAIL="admin@example.com")
class WebPushRecipientsTests(ClubApiTestCase):
    """Web push reaches the same accounts the email and native push do -- a parent gets their
    child's notification even though the child has no login -- once per distinct notification."""

    def setUp(self):
        super().setUp()
        self.parent_subscription = PushSubscription.objects.create(club=self.club, member=self.member, endpoint="https://fcm.googleapis.com/fcm/send/parent", p256dh="k", auth="a")

    def pushes(self, notifications):
        with patch("mobile.services.push._deliver") as deliver_web:
            send_push_for_notifications(notifications)
        return [call.args[0] for call in deliver_web.call_args_list]

    def test_a_parent_is_pushed_their_childs_notification(self):
        notification = Notification.objects.create(club=self.club, member=self.child, title="Hi", body="…")

        self.assertEqual(self.pushes([notification]), [self.parent_subscription])

    def test_one_push_per_subscription_for_a_family_wide_notification(self):
        second_child = Member.objects.create(first_name="Lou", last_name="Parent")
        FamilyMembership.objects.create(family=Family.objects.get(club=self.club), member=second_child, role=FamilyMembership.FamilyRole.CHILD)
        item = News.objects.create(club=self.club, title="News", slug="news", body="…", status=News.Status.PUBLISHED, published_at=timezone.now())
        notifications = [Notification.objects.create(club=self.club, member=member, title="New article", body="Read it", source=item) for member in (self.member, self.child, second_child)]

        self.assertEqual(self.pushes(notifications), [self.parent_subscription])

    def test_a_subscription_in_another_club_is_not_used(self):
        PushSubscription.objects.filter(pk=self.parent_subscription.pk).update(club=Club.objects.create(name="Rival FC", slug="rival-fc"))
        notification = Notification.objects.create(club=self.club, member=self.child, title="Hi", body="…")

        self.assertEqual(self.pushes([notification]), [])


class NativeBadgeTests(ClubApiTestCase):
    @override_settings(APP_PUSH_GATEWAY="appapi.test_member.RecordingPublisher")
    def test_the_push_carries_the_accounts_unread_total(self):
        RecordingPublisher.sent = []
        RecordingPublisher.badges = []
        RecordingPublisher.dead = set()
        PushDevice.objects.create(user=self.user, installation_id="phone", platform="ios", push_token="t")
        Notification.objects.create(club=self.club, member=self.child, title="Earlier", body="…")  # unread, a child's
        read = Notification.objects.create(club=self.club, member=self.member, title="Read", body="…")
        Notification.objects.filter(pk=read.pk).update(read_at=timezone.now())
        fresh = Notification.objects.create(club=self.club, member=self.member, title="New", body="…")

        deliver([fresh.pk])

        self.assertEqual(RecordingPublisher.badges, [2])  # the child's earlier one + the new one


class AccountSettingsTests(AppApiTestCase):
    def test_me_returns_the_email_setting_and_it_can_be_changed(self):
        token = self.login()
        self.assertEqual(self.api("get", "/me", token=token).json()["settings"], {"email_notifications": "always"})

        response = self.api("patch", "/me/settings", token=token, data={"email_notifications": "unless_app"})

        self.assertEqual(response.json(), {"email_notifications": "unless_app"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email_notifications, "unless_app")
        self.assertEqual(self.api("get", "/me", token=token).json()["settings"]["email_notifications"], "unless_app")

    def test_an_unknown_choice_is_rejected(self):
        token = self.login()
        self.assertEqual(self.api("patch", "/me/settings", token=token, data={"email_notifications": "never"}).status_code, 422)


class CalendarOpenTasksTests(ClubApiTestCase):
    def counts(self, **params):
        return {item["event"]["title"]: item["open_tasks"] for item in self.get("/calendar", **params).json()["items"]}

    def totals(self, **params):
        return {item["event"]["title"]: item["total_tasks"] for item in self.get("/calendar", **params).json()["items"]}

    def test_calendar_rows_count_the_tasks_that_still_have_a_free_slot(self):
        busy = self.event(title="Busy", start=self.soon)
        quiet = self.event(title="Quiet", start=self.soon + datetime.timedelta(days=1))
        for event in (busy, quiet):
            self.invite(event)
        fruit = EventTask.objects.create(event=busy, title="Fruit", needed_quantity=2)
        EventTask.objects.create(event=busy, title="Lifts", needed_quantity=1)
        taken = EventTask.objects.create(event=busy, title="Whistle", needed_quantity=1)
        EventTaskClaim.objects.create(task=taken, member=self.child)
        EventTaskClaim.objects.create(task=fruit, member=self.child)  # one of two slots: still open

        self.assertEqual(self.counts(), {"Busy": 2, "Quiet": 0})
        self.assertEqual(self.totals(), {"Busy": 3, "Quiet": 0})  # the full whistle task still counts in the total

    def test_a_task_stops_counting_once_every_slot_is_taken(self):
        event = self.event(title="Game")
        self.invite(event)
        task = EventTask.objects.create(event=event, title="Fruit", needed_quantity=2)
        self.assertEqual(self.counts(), {"Game": 1})

        EventTaskClaim.objects.create(task=task, member=self.child)
        EventTaskClaim.objects.create(task=task, member=self.member)

        self.assertEqual((self.counts(), self.totals()), ({"Game": 0}, {"Game": 1}))

    def test_every_row_of_one_event_shows_the_same_count(self):
        game = self.event(title="Game", kind=Event.EventKind.GAME)
        self.invite(game, member=self.child)
        RefereeSignup.objects.create(event=game, member=self.member)
        EventTask.objects.create(event=game, title="Fruit", needed_quantity=1)

        items = self.get("/calendar").json()["items"]

        self.assertEqual(sorted((item["type"], item["open_tasks"], item["total_tasks"]) for item in items), [("referee", 1, 1), ("rsvp", 1, 1)])


class HomeOpenTasksTests(ClubApiTestCase):
    """Every event Home shows carries how many of its tasks still have a free slot."""

    def test_cards_heroes_and_open_asks_all_carry_the_open_task_count(self):
        card_event = self.event(title="Card game", start=self.soon)
        going_event = self.event(title="Party", start=self.soon + datetime.timedelta(days=1))
        ask_event = self.event(title="Meeting", start=self.soon + datetime.timedelta(days=2))
        self.invite(card_event, member=self.child)
        self.invite(going_event, status=Attendance.AttendanceStatus.PRESENT)
        self.invite(ask_event, status=Attendance.AttendanceStatus.MAYBE)
        EventTask.objects.create(event=card_event, title="Fruit", needed_quantity=2)
        EventTask.objects.create(event=card_event, title="Lifts", needed_quantity=1)
        EventTask.objects.create(event=going_event, title="Drinks", needed_quantity=1)
        full = EventTask.objects.create(event=ask_event, title="Whistle", needed_quantity=1)
        EventTaskClaim.objects.create(task=full, member=self.child)

        home = self.get("/home").json()

        self.assertEqual({(card["kind"], card["event"]["title"]): (card["open_tasks"], card["total_tasks"]) for card in home["member_cards"]}, {("rsvp_needed", "Card game"): (2, 2), ("next_going", "Party"): (1, 1)})
        self.assertEqual((home["rsvp_needed"]["open_tasks"], home["next_going"]["open_tasks"]), (2, 1))
        self.assertEqual((home["rsvp_needed"]["total_tasks"], home["next_going"]["total_tasks"]), (2, 1))
        self.assertEqual([(row["event"]["title"], row["open_tasks"], row["total_tasks"]) for row in home["needs_answer"]], [("Meeting", 0, 1)])

    def test_an_event_without_tasks_counts_zero(self):
        self.invite(self.event(title="Plain"), member=self.child)

        home = self.get("/home").json()

        self.assertEqual([(card["open_tasks"], card["total_tasks"]) for card in home["member_cards"]], [(0, 0)])


class HomeHouseholdTasksTests(ClubApiTestCase):
    """A task the household has taken is listed on that event's Home card, so it isn't forgotten."""

    def setUp(self):
        super().setUp()
        self.game = self.event(title="Card game", start=self.soon)
        self.invite(self.game, member=self.child)
        self.fruit = EventTask.objects.create(event=self.game, title="Bring fruit", description="For 12 kids", needed_quantity=2)
        self.lifts = EventTask.objects.create(event=self.game, title="Lifts", needed_quantity=1)

    def card(self):
        return next(card for card in self.get("/home").json()["member_cards"] if card["event"]["title"] == "Card game")

    def test_a_task_the_account_took_is_on_the_card(self):
        EventTaskClaim.objects.create(task=self.fruit, member=self.member)

        card = self.card()

        self.assertEqual(card["my_tasks"], [{"id": str(self.fruit.pk), "title": "Bring fruit", "description": "For 12 kids", "claimed_by_me": True}])
        self.assertEqual((card["open_tasks"], card["total_tasks"]), (2, 2))  # one slot of the fruit task is still free

    def test_a_task_a_household_member_took_is_there_too_but_not_claimed_by_me(self):
        partner = Member.objects.create(first_name="Pam", last_name="Parent")
        FamilyMembership.objects.create(family=Family.objects.get(club=self.club), member=partner, role=FamilyMembership.FamilyRole.PARENT)
        EventTaskClaim.objects.create(task=self.lifts, member=partner)

        self.assertEqual([(task["title"], task["claimed_by_me"]) for task in self.card()["my_tasks"]], [("Lifts", False)])

    def test_a_task_someone_elses_family_took_is_not_listed(self):
        EventTaskClaim.objects.create(task=self.lifts, member=self.stranger())

        self.assertEqual(self.card()["my_tasks"], [])

    def test_a_task_both_of_us_took_appears_once_and_is_marked_mine(self):
        partner = Member.objects.create(first_name="Pam", last_name="Parent")
        FamilyMembership.objects.create(family=Family.objects.get(club=self.club), member=partner, role=FamilyMembership.FamilyRole.PARENT)
        EventTaskClaim.objects.create(task=self.fruit, member=partner)
        EventTaskClaim.objects.create(task=self.fruit, member=self.member)

        tasks = self.card()["my_tasks"]

        self.assertEqual([(task["title"], task["claimed_by_me"]) for task in tasks], [("Bring fruit", True)])

    def test_nothing_taken_means_an_empty_list_and_giving_it_back_removes_it(self):
        self.assertEqual(self.card()["my_tasks"], [])
        claim = EventTaskClaim.objects.create(task=self.lifts, member=self.member)
        self.assertEqual(len(self.card()["my_tasks"]), 1)

        claim.delete()

        self.assertEqual(self.card()["my_tasks"], [])

    def test_the_heroes_and_open_asks_carry_it_too(self):
        EventTaskClaim.objects.create(task=self.lifts, member=self.member)
        maybe = self.event(title="Maybe game", start=self.soon + datetime.timedelta(days=3))
        self.invite(maybe, status=Attendance.AttendanceStatus.MAYBE)
        EventTaskClaim.objects.create(task=EventTask.objects.create(event=maybe, title="Drinks"), member=self.member)

        home = self.get("/home").json()

        self.assertEqual([task["title"] for task in home["rsvp_needed"]["my_tasks"]], ["Lifts"])
        self.assertEqual([(row["event"]["title"], [task["title"] for task in row["my_tasks"]]) for row in home["needs_answer"]], [("Maybe game", ["Drinks"])])
