import base64
import datetime
from unittest import mock

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, ensure_member_for_user
from controlpanel.tests import PASSWORD, login_session, make_user
from events.models import Attendance, Event
from members.models import Family, FamilyMembership, Member
from notifications.models import WebPushSubscription
from sponsoring.models import Sponsor


class ParentPortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = make_user("owner@portal.test")
        platform = Club(schema_name="public", name="Platform", owner=owner)
        platform.auto_create_schema = False
        platform.save()
        Domain.objects.create(domain="testserver", tenant=platform, is_primary=True)
        cls.club = create_club("Portal FC", owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        cls.parent = make_user("parent@portal.test", password=f"  {PASSWORD} ")
        cls.outsider = make_user("outsider@portal.test")
        cls.club.add_user(cls.parent)
        cls.viewer = ensure_member_for_user(cls.club, cls.parent)
        with schema_context(cls.club.schema_name):
            cls.child = Member.objects.create(first_name="My", last_name="Child")
            cls.other = Member.objects.create(first_name="Other", last_name="Child")
            family = Family.objects.create(name="Family")
            FamilyMembership.objects.create(family=family, member=cls.viewer, role=FamilyMembership.FamilyRole.PARENT)
            FamilyMembership.objects.create(family=family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)
            event = Event.objects.create(title="Training", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=1))
            event.invited_members.add(cls.child, cls.other)
            cls.child_reply, _ = Attendance.objects.update_or_create(event=event, member=cls.child, defaults={"coach_note": "Private"})
            cls.other_reply, _ = Attendance.objects.get_or_create(event=event, member=cls.other)

    def tearDown(self):
        connection.set_schema_to_public()

    def call(self, method, path, token, data=None):
        return getattr(Client(), method)(f"/api/v1{path}", data=data, content_type="application/json", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=token)

    def test_parent_password_and_family_permissions(self):
        login = Client().post(
            "/_allauth/app/v1/auth/login", data={"email": self.parent.email, "password": f"  {PASSWORD} "},
            content_type="application/json", HTTP_HOST=self.host,
        )
        self.assertEqual(login.status_code, 200, login.content)
        token = login.json()["meta"]["session_token"]
        self.assertEqual(self.call("get", "/me", token).json()["code"], "no_access")
        self.assertIs(self.call("get", "/portal/access", token).json()["can_manage"], False)

        with schema_context(self.club.schema_name):
            Sponsor.objects.create(name="Current sponsor", url="https://sponsor.example")
            Sponsor.objects.create(name="Future sponsor", start_date=timezone.localdate() + datetime.timedelta(days=1))
            Sponsor.objects.create(name="Former sponsor", start_date=timezone.localdate() - datetime.timedelta(days=2), end_date=timezone.localdate() - datetime.timedelta(days=1))
        home = self.call("get", "/portal/home", token)
        self.assertEqual(home.status_code, 200, home.content)
        self.assertEqual({person["id"] for person in home.json()["people"]}, {str(self.viewer.pk), str(self.child.pk)})
        [event] = home.json()["events"]
        self.assertEqual(event["id"], str(self.child_reply.pk))
        self.assertNotIn("coach_note", event)
        self.assertEqual([sponsor["name"] for sponsor in home.json()["sponsors"]], ["Current sponsor"])
        self.assertEqual(self.call("post", f"/portal/attendance/{self.other_reply.pk}", token, {"status": "present"}).status_code, 404)
        reply = self.call("post", f"/portal/attendance/{self.child_reply.pk}", token, {"status": "present"})
        self.assertEqual(reply.status_code, 200, reply.content)
        self.assertEqual(reply.json()["status"], "present")
        self.assertEqual(self.call("get", "/portal/coach", token).status_code, 403)
        connection.set_schema_to_public()
        denied = self.call("get", "/portal/home", login_session(self.outsider))
        self.assertIn(denied.status_code, (401, 403))

    def test_web_push_subscription_belongs_to_club_and_user(self):
        token = login_session(self.parent)
        public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)

        def key(value):
            return base64.urlsafe_b64encode(value).decode().rstrip("=")

        endpoint = "https://fcm.googleapis.com/fcm/send/portal-test"
        body = {"endpoint": endpoint, "keys": {"p256dh": key(public), "auth": key(b"0" * 16)}}
        with mock.patch("notifications.gateways.webpush_public_key", return_value=key(public)):
            self.assertEqual(self.call("get", "/web-push/config", token).json()["public_key"], key(public))
            self.assertEqual(self.call("post", "/web-push/subscriptions", token, {**body, "endpoint": "https://127.0.0.1/push"}).status_code, 400)
            self.assertEqual(self.call("post", "/web-push/subscriptions", token, body).status_code, 204)
        self.assertTrue(WebPushSubscription.objects.filter(user=self.parent, club=self.club, endpoint=endpoint).exists())
        self.assertTrue(self.call("post", "/web-push/status", token, {"endpoint": endpoint}).json()["subscribed"])
        self.assertEqual(self.call("delete", "/web-push/subscriptions", token, {"endpoint": endpoint}).status_code, 204)
        self.assertFalse(WebPushSubscription.objects.filter(endpoint=endpoint).exists())



class PortalProfileTests(ParentPortalTests):
    def token(self):
        login = Client().post(
            "/_allauth/app/v1/auth/login", data={"email": self.parent.email, "password": f"  {PASSWORD} "},
            content_type="application/json", HTTP_HOST=self.host,
        )
        return login.json()["meta"]["session_token"]

    def test_edit_own_and_childs_details_but_nobody_elses(self):
        token = self.token()
        profile = self.call("get", "/portal/me/profile", token).json()
        self.assertEqual({p["id"] for p in profile["people"]}, {str(self.viewer.pk), str(self.child.pk)})
        saved = self.call("patch", f"/portal/me/profile/{self.child.pk}", token, {"first_name": "Kid", "phone": "+32 470 12 34 56", "date_of_birth": "2015-04-02"})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual((saved.json()["first_name"], saved.json()["date_of_birth"]), ("Kid", "2015-04-02"))
        self.assertEqual(self.call("patch", f"/portal/me/profile/{self.other.pk}", token, {"first_name": "Nope"}).status_code, 404)
        self.assertEqual(self.call("patch", f"/portal/me/profile/{self.child.pk}", token, {"first_name": " "}).status_code, 422)
        self.assertEqual(self.call("patch", f"/portal/me/profile/{self.child.pk}", token, {"email": "x@y.test"}).status_code, 200)  # unknown fields are ignored by the schema
        with schema_context(self.club.schema_name):
            self.assertEqual(Member.objects.get(pk=self.child.pk).email, "")

    def test_notification_switches_round_trip(self):
        token = self.token()
        before = self.call("get", "/portal/me/notifications", token).json()
        self.assertTrue(all(row["push"] for row in before["categories"]))
        after = self.call("put", "/portal/me/notifications", token, {"master": {"email": False}, "categories": {"news": {"push": False}}, "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "Europe/Brussels"}})
        self.assertEqual(after.status_code, 200, after.content)
        data = after.json()
        self.assertIs(data["master"]["email"], False)
        self.assertIs(next(row for row in data["categories"] if row["key"] == "news")["push"], False)
        self.assertEqual(data["quiet_hours"]["start"], "22:00")
        self.assertEqual(self.call("put", "/portal/me/notifications", token, {"categories": {"admin": {"push": False}}}).status_code, 422)

    def test_home_says_why_a_reply_is_not_possible_instead_of_offering_it(self):
        token = self.token()
        [row] = self.call("get", "/portal/home", token).json()["events"]
        self.assertIsNone(row["blocked"])
        with schema_context(self.club.schema_name):
            event = self.child_reply.event
            event.invited_members.remove(self.child)
            Attendance.objects.get_or_create(event=event, member=self.child)  # a stale row, as left behind by an audience change
        [row] = self.call("get", "/portal/home", token).json()["events"]
        self.assertEqual(row["blocked"], "not_invited")
        self.assertEqual(row["blocked_detail"]["reason"], "not_in_audience")
        with schema_context(self.club.schema_name):
            event.excluded_members.add(self.child)
            Attendance.objects.get_or_create(event=event, member=self.child)
        [row] = self.call("get", "/portal/home", token).json()["events"]
        self.assertEqual(row["blocked_detail"]["reason"], "excluded")
        self.assertEqual(self.call("post", f"/portal/attendance/{row['id']}", token, {"status": "present"}).status_code, 403)


    def test_home_does_a_fixed_number_of_lookups_however_many_events_there_are(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        token = self.token()

        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.call("get", "/portal/home", token).status_code, 200)
            return len(queries)

        before = count()
        with schema_context(self.club.schema_name):
            for day in range(2, 12):
                event = Event.objects.create(title=f"Training {day}", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=day))
                event.invited_members.add(self.child)
        after = count()
        self.assertLessEqual(after - before, 2, f"{before} lookups with 1 event, {after} with 11")

    def test_event_extras_for_the_family_lineup_tasks_attachments_and_claiming(self):
        from django.core.files.base import ContentFile

        from events.models import EventAttachment, EventTask, Lineup, LineupSelection
        from teams.models import Team

        token = self.token()
        with schema_context(self.club.schema_name):
            event = self.child_reply.event
            team = Team.objects.create(name="U14")
            lineup = Lineup.objects.create(event=event, team=team, published_at=timezone.now())
            LineupSelection.objects.create(lineup=lineup, member=self.child)
            task = EventTask.objects.create(event=event, title="Timekeeper", needed_quantity=1)
            attachment = EventAttachment(event=event, name="info.pdf", size=3)
            attachment.file.save("info.pdf", ContentFile(b"abc"), save=True)
        extras = self.call("get", f"/portal/me/../events/{event.pk}", token)
        self.assertEqual(extras.status_code, 404)  # not under /me
        extras = self.call("get", f"/portal/events/{event.pk}", token).json()
        self.assertEqual([p["mine"] for p in extras["lineup"]["players"]], [True])
        self.assertIsNone(extras["game"])
        self.assertEqual([t["title"] for t in extras["tasks"]], ["Timekeeper"])
        self.assertEqual(extras["attachments"][0]["name"], "info.pdf")
        # open on Home until someone takes it
        self.assertEqual([t["title"] for t in self.call("get", "/portal/home", token).json()["tasks"]], ["Timekeeper"])
        claimed = self.call("post", f"/portal/events/{event.pk}/tasks/{task.pk}/claim", token)
        self.assertEqual((claimed.status_code, claimed.json()["claimed_by_me"]), (200, True))
        self.assertEqual(self.call("get", "/portal/home", token).json()["tasks"], [])
        self.assertEqual(self.call("delete", f"/portal/events/{event.pk}/tasks/{task.pk}/claim", token).json()["claimed_by_me"], False)
        download = Client().get(f"/api/v1/portal/events/{event.pk}/attachments/{attachment.pk}/download", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=token)
        self.assertEqual((download.status_code, b"".join(download.streaming_content)), (200, b"abc"))
        # an event the family has nothing to do with stays invisible
        with schema_context(self.club.schema_name):
            stranger = Event.objects.create(title="Other", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=3))
        self.assertEqual(self.call("get", f"/portal/events/{stranger.pk}", token).status_code, 404)


    def test_event_extras_give_the_captured_game_sheet(self):
        from events.models import GameTimeline

        token = self.token()
        with schema_context(self.club.schema_name):
            event = self.child_reply.event
            document = {
                "score": {"home": 3, "away": 1},
                "periods": [{"label": "1", "home": 2, "away": 0}],
                "lineups": {"home": [], "away": [{"pos": "F", "number": 12, "name": "Child, My", "license": "x"}]},
                "events": [{"id": "g1", "type": "goal", "side": "away", "period": "1", "time": "03:10", "goal": {"scorer": {"name": "Child, My", "number": 12}, "assists": [], "score_after": {"home": 1, "away": 0}}}],
            }
            GameTimeline.objects.create(event=event, source="test", data=document, status=GameTimeline.Status.FINAL)
        game = self.call("get", f"/portal/events/{event.pk}", token).json()["game"]
        self.assertEqual(game["periods"][0]["rows"][0]["kind"], "Goal")
        self.assertEqual(game["lineup"]["ours"][0]["number"], 12)


    def test_followed_games_card_data_and_following_from_the_app(self):
        token = self.token()
        with schema_context(self.club.schema_name):
            game = Event.objects.create(title="Game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(hours=3), is_live=False)
            game.invited_members.add(self.child)
        self.assertEqual(self.call("get", "/portal/followed", token).json(), [])
        followed = self.call("put", f"/portal/events/{game.pk}/follow", token)
        self.assertEqual((followed.status_code, followed.json()["directly"]), (200, True))
        [card] = self.call("get", "/portal/followed", token).json()
        self.assertEqual((card["event_id"], card["status"]), (str(game.pk), "scheduled"))
        with schema_context(self.club.schema_name):
            Event.objects.filter(pk=game.pk).update(is_live=True, score_for=2, score_against=1)
        [card] = self.call("get", "/portal/followed", token).json()
        self.assertEqual((card["status"], card["score_for"], card["score_against"]), ("live", 2, 1))
        self.assertEqual(self.call("get", f"/portal/events/{game.pk}/game", token).status_code, 200)
        self.assertEqual(self.call("delete", f"/portal/events/{game.pk}/follow", token).json()["directly"], False)
        # a game the family has nothing to do with cannot be followed or read from here
        with schema_context(self.club.schema_name):
            stranger = Event.objects.create(title="Other", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=3))
        self.assertEqual(self.call("put", f"/portal/events/{stranger.pk}/follow", token).status_code, 404)
        self.assertEqual(self.call("get", f"/portal/events/{stranger.pk}/game", token).status_code, 404)

    def test_a_person_held_back_by_a_requirement_still_gets_their_row_on_the_event(self):
        from events.models import Season
        from members.models import ClubMembership, OnboardingRequirement

        token = self.token()
        today = datetime.date.today()
        with schema_context(self.club.schema_name):
            season = Season.objects.create(start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
            ClubMembership.objects.create(member=self.child, season=season, status=ClubMembership.StatusChoices.ACTIVE)
            OnboardingRequirement.objects.create(name="Medical certificate", blocked_event_kinds=["training"])
            Attendance.objects.filter(event=self.child_reply.event, member=self.child).delete()  # what the invite sync does to a blocked person
        [row] = [r for r in self.call("get", "/portal/home", token).json()["events"] if r["member_id"] == str(self.child.pk)]
        self.assertEqual((row["blocked"], row["blocked_detail"]["reason"], row["blocked_detail"]["requirements"]), ("not_invited", "requirements", ["Medical certificate"]))
        self.assertTrue(row["id"].startswith("requirement-"))


    def test_payment_help_history_and_submitted_forms_are_given(self):
        from formbuilder.models import Answer, Field, Form, FormSend, Submission

        token = self.token()
        with schema_context(self.club.schema_name):
            form = Form.objects.create(title="Medical information")
            field = Field.objects.create(form=form, label="Allergies", field_type="text")
            send = FormSend.objects.create(form=form)
            submission = Submission.objects.create(send=send, member=self.child, submitted_by=self.viewer)
            Answer.objects.create(submission=submission, field=field, value="Peanuts")
        home = self.call("get", "/portal/home", token).json()
        self.assertEqual([f["title"] for f in home["submitted_forms"]], ["Medical information"])
        self.assertIn("payments", home)
        self.assertIn("payment_instructions", home)
        answers = self.call("get", f"/portal/forms/submissions/{submission.pk}", token).json()["answers"]
        self.assertEqual(answers, [{"label": "Allergies", "value": "Peanuts"}])
        with schema_context(self.club.schema_name):
            other = Submission.objects.create(send=send, member=self.other)
        self.assertEqual(self.call("get", f"/portal/forms/submissions/{other.pk}", token).status_code, 404)
