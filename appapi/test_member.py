"""Phase 2 of the app API: member endpoints, news, notifications, profiles and native
push delivery. Fixtures come from appapi.tests.AppApiTestCase: a parent (Pat) with a
child (Kim) in Ajax United, current season, signed in through allauth headless."""

import datetime
import io
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from waffle import get_waffle_flag_model

from appapi.presenters import game_status
from club.models import ClubMembership, OnboardingRequirement
from events.models import Attendance, Event, EventTask, EventTaskClaim, Lineup, LineupSelection, OfficialSignup, RefereeSignup
from events.services.agenda import member_agenda
from events.services.game_timeline import TimelineFetch
from events.services.game_timeline import store as store_game_timeline
from members.models import Family, FamilyMembership, Member
from news.models import News
from notifications.models import Notification
from notifications.services import notify_members
from teams.models import Position, Team, TeamMembership

from .delivery import deliver, on_notifications_created
from .models import PushDevice
from .push import NullGateway
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
        team = Team.objects.create(club=self.club, name="U12", short_name="U12")  # show_player_names_publicly stays off
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

    def test_photo_follows_the_website_rule(self):
        self.child.photo = SimpleUploadedFile("kim.png", png_bytes(), content_type="image/png")
        self.child.save()
        stranger = self.stranger()
        stranger.photo = SimpleUploadedFile("sam.png", png_bytes(), content_type="image/png")
        stranger.save()

        photo_url = self.get(f"/members/{self.child.pk}").json()["photo_url"]
        response = self.api("get", photo_url.split("/api/app/v1", 1)[1], token=self.token, host=CLUB_HOST)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Cache-Control"], "private, max-age=3600")
        self.assertError(self.get(f"/members/{stranger.pk}/photo"), 404, "not_found")


class RecordingPublisher(NullGateway):
    sent: list = []
    dead: set = set()

    def publish(self, device, *, title, body, data):
        RecordingPublisher.sent.append((device.installation_id, title, data))
        return device.installation_id not in RecordingPublisher.dead


@override_settings(APP_PUSH_GATEWAY="appapi.test_member.RecordingPublisher")
class PushDeliveryTests(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        RecordingPublisher.sent = []
        RecordingPublisher.dead = set()
        self.device = PushDevice.objects.create(user=self.user, installation_id="parent-phone", platform="ios", push_token="t", endpoint_arn="arn:1")

    def test_one_push_per_account_for_a_family_wide_notification(self):
        second_child = Member.objects.create(first_name="Lou", last_name="Parent")
        FamilyMembership.objects.create(family=Family.objects.get(club=self.club), member=second_child, role=FamilyMembership.FamilyRole.CHILD)
        item = News.objects.create(club=self.club, title="News", slug="news", body="…", status=News.Status.PUBLISHED, published_at=timezone.now())
        with patch("appapi.delivery.run_in_background"):
            notifications = notify_members([self.member, self.child, second_child], club=self.club, title="New article", body="Read it", source=item, send_email=False)

        self.assertEqual(deliver([notification.pk for notification in notifications]), 1)
        installation, title, data = RecordingPublisher.sent[0]
        self.assertEqual((installation, title), ("parent-phone", "New article"))
        self.assertEqual(data["club_slug"], "ajax-united")
        self.assertEqual(data["target"], {"type": "news", "id": str(item.pk), "slug": "news"})

    def test_guardians_get_their_childs_notification(self):
        notification = Notification.objects.create(club=self.club, member=self.child, title="Hi", body="…")
        self.assertEqual(deliver([notification.pk]), 1)

    def test_dead_token_disables_the_device(self):
        RecordingPublisher.dead = {"parent-phone"}
        notification = Notification.objects.create(club=self.club, member=self.member, title="Hi", body="…")

        self.assertEqual(deliver([notification.pk]), 0)
        self.device.refresh_from_db()
        self.assertFalse(self.device.enabled)

    def test_created_notifications_are_pushed_after_commit(self):
        with patch("appapi.delivery.run_in_background") as background, self.captureOnCommitCallbacks(execute=True):
            notifications = notify_members([self.child], club=self.club, title="Hi", body="…", send_email=False)
        background.assert_called_once_with(deliver, [notifications[0].pk])

    @override_settings(APP_PUSH_GATEWAY="")
    def test_nothing_is_scheduled_without_sns(self):
        with patch("appapi.delivery.run_in_background") as background, self.captureOnCommitCallbacks(execute=True):
            on_notifications_created(None, notifications=[Notification(pk=1)])
        background.assert_not_called()


class UnknownIdTests(ClubApiTestCase):
    """API.md §13: every route that takes an id answers an unknown (or another club's)
    id with the 404 envelope -- walked from the route table, so a new endpoint is
    covered without anyone remembering to add a test."""

    BODIES = {"rsvp": {"member_id": "00000000-0000-0000-0000-000000000000", "status": "present"}, "respond": {"response": "decline"}, "{member_id}": {}, "{item_id}": {"quantity": 1}}

    def test_unknown_ids_are_404_everywhere(self):
        import re
        import uuid as uuid_module

        from .api import app_api

        routes = [(path, method) for _prefix, router in app_api._routers for path, view in router.path_operations.items() for operation in view.operations for method in operation.methods if "{" in path]
        self.assertTrue(routes)
        for path, method in routes:
            concrete = re.sub(r"\{(\w+)\}", lambda match: "unknown-slug" if match.group(1) in ("slug", "installation_id") else str(uuid_module.uuid4()), path)
            host = "rosterchief.app" if path.startswith("/devices") else CLUB_HOST
            data = next((body for key, body in self.BODIES.items() if path.endswith(key)), None) if method in ("POST", "PATCH") else None
            with self.subTest(method=method, path=path):
                response = self.api(method.lower(), concrete, token=self.token, host=host, data=data)
                self.assertError(response, 404, response.json()["error"]["code"] if response.status_code == 404 else "not_found")
