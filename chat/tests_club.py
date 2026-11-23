"""Club-wide announcement chats (``audience: "club"``) and the chat settings (retention)."""

import datetime
from unittest import mock

from django.utils import timezone

from chat.models import ChatSettings, Conversation, Message, Participant
from chat.services import retention, roster
from chat.tests_base import ChatTestBase
from club.services import grant_login
from members.models import Family, FamilyMembership, Member
from teams.models import TeamMembership


class ClubAudienceTests(ChatTestBase):
    CLUB = "Whole FC"

    def setUp(self):
        super().setUp()
        patcher = mock.patch("chat.tasks.push_message.delay")
        patcher.start()
        self.addCleanup(patcher.stop)

    def club_chat(self, key="announcer", **body):
        return self.post(key, "/conversations", {"kind": "group", "title": "Club news", "audience": "club", **body})

    def names(self, cid, left=False):
        return {p.member.first_name for p in Participant.objects.filter(conversation_id=cid, left_at__isnull=not left).select_related("member")}

    def test_only_announcers_may_create_and_mode_defaults_to_announcement(self):
        for key in ("coach", "plain", "mod"):
            self.assertEqual(self.club_chat(key).status_code, 403, key)
        response = self.club_chat()
        self.assertEqual(response.status_code, 201, response.content)
        doc = response.json()
        self.assertEqual((doc["mode"], doc["audience"], doc["kind"], doc["is_admin"], doc["can_post"]), ("announcement", "club", "group", True, True))
        cid = doc["id"]
        self.assertTrue(Participant.objects.get(conversation_id=cid, member=self.m["announcer"]).is_admin)
        self.assertFalse(Participant.objects.get(conversation_id=cid, member=self.m["coach"]).is_admin)  # staff do not get to post
        self.assertFalse(self.get("plain", "/conversations").json()["results"][0]["can_post"])
        self.assertEqual(self.club_chat(mode="discussion").json()["mode"], "discussion")

    def test_validation(self):
        self.assertEqual(self.post("announcer", "/conversations", {"kind": "team", "audience": "club", "team_id": str(self.team.pk)}).status_code, 422)
        self.assertEqual(self.club_chat(title="").status_code, 422)
        self.assertEqual(self.club_chat(include={"staff": False, "players": False, "parents": False}).status_code, 422)
        self.assertEqual(self.club_chat(team_ids=["00000000-0000-0000-0000-000000000000"]).status_code, 422)
        self.assertEqual(self.get("plain", "/conversations").json()["results"], [])

    def test_whole_club_reaches_everybody_with_a_login_or_a_guardian_with_one(self):
        kid = Member.objects.create(first_name="Kid", last_name="Nologin", date_of_birth=datetime.date(2018, 1, 1))  # no login
        grown = Member.objects.create(first_name="Gina", last_name="Guardian", email="gina@chat.test", date_of_birth=datetime.date(1985, 1, 1))
        grant_login(self.club, grown)
        family = Family.objects.create(name="Nologin")
        FamilyMembership.objects.create(family=family, member=kid, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=grown, role=FamilyMembership.FamilyRole.PARENT)
        lone = Member.objects.create(first_name="Lone", last_name="Minor", email="lone@chat.test", date_of_birth=datetime.date(2014, 1, 1))  # a login, but no parent with one
        grant_login(self.club, lone)
        Member.objects.create(first_name="Silent", last_name="Adult", date_of_birth=datetime.date(1970, 1, 1))  # no login, no guardian
        cid = self.club_chat().json()["id"]
        names = self.names(cid)
        self.assertTrue({"Carl", "Pia", "Pip", "Paul", "Pat", "Sam", "Mo", "Ann", "Pam", "Gina"} <= names, names)
        self.assertFalse({"Kid", "Lone", "Silent"} & names)

    def test_include_flags_and_teams(self):
        cid = self.club_chat(include={"staff": False, "players": True, "parents": False}).json()["id"]
        self.assertEqual(self.names(cid), {"Pia", "Paul", "Pat", "Ann"})  # Pat: the minors rule brings Pia's parent along
        cid = self.club_chat(include={"staff": True, "players": False, "parents": False}).json()["id"]
        self.assertEqual(self.names(cid), {"Carl", "Ann"})
        cid = self.club_chat(team_ids=[str(self.team.pk)], include={"staff": True, "players": True, "parents": False}).json()["id"]
        self.assertEqual(self.names(cid), {"Carl", "Pia", "Paul", "Pat", "Ann"})
        cid = self.club_chat(team_ids=[str(self.team.pk), str(self.other_team.pk)], include={"staff": False, "players": True, "parents": True}).json()["id"]
        self.assertEqual(self.names(cid), {"Pia", "Paul", "Pat", "Ann"})
        self.assertEqual(self.club_chat(team_ids=[str(self.other_team.pk)]).status_code, 201)

    def test_sync_adds_joiners_removes_leavers_and_respects_manual_removals(self):
        cid = self.club_chat(include={"staff": False, "players": True, "parents": False}).json()["id"]
        conversation = Conversation.objects.get(pk=cid)
        membership = TeamMembership.objects.create(team=self.team, member=self.m["plain"], season=self.season, position=self.forward)
        self.assertEqual(roster.sync_all()["added"], 1)
        self.assertIn("Pam", self.names(cid))
        self.call("announcer", "delete", f"/conversations/{cid}/participants/{self.m['p2'].pk}")
        membership.delete()
        roster.sync_all()
        self.assertIn("Pam", self.names(cid, left=True))
        self.assertNotIn("Pam", self.names(cid))
        self.assertNotIn("Paul", self.names(cid))  # taken out by hand: stays out
        roster.sync(conversation)
        self.assertNotIn("Paul", self.names(cid))
        # a new member joins the whole-club chat
        wide = self.club_chat().json()["id"]
        newcomer = Member.objects.create(first_name="Nina", last_name="New", email="nina@chat.test", date_of_birth=datetime.date(1995, 1, 1))
        grant_login(self.club, newcomer)
        roster.sync_all()
        self.assertIn("Nina", self.names(wide))

    def test_minors_rule_unchanged_for_plain_groups(self):
        cid = self.start("announcer", with_=["p1", "stranger"])
        self.assertIn("Pat", self.names(cid))


class ChatSettingsTests(ChatTestBase):
    CLUB = "Settings FC"

    def put(self, key, value):
        return self.call(key, "put", "/settings", {"retention_months": value})

    def test_permissions(self):
        self.assertEqual(self.get("plain", "/settings").status_code, 403)
        self.assertEqual(self.get("announcer", "/settings").status_code, 403)
        self.assertEqual(self.put("plain", 6).status_code, 403)
        self.assertEqual(self.get("mod", "/settings").json(), {"retention_months": 24})

    def test_update_and_validation(self):
        self.assertEqual(self.put("mod", 6).json(), {"retention_months": 6})
        self.assertEqual(ChatSettings.current().retention_months, 6)
        self.assertEqual(self.get("mod", "/settings").json(), {"retention_months": 6})
        self.assertEqual(self.put("mod", 121).status_code, 422)
        self.assertEqual(self.put("mod", -1).status_code, 422)
        self.assertEqual(self.put("mod", 120).json(), {"retention_months": 120})
        self.assertEqual(self.put("mod", None).json(), {"retention_months": None})
        self.assertEqual(ChatSettings.current().retention_months, 0)

    def test_retention_keeps_everything_when_forever(self):
        cid = self.start("coach", with_=["p2"])
        self.say("coach", cid)
        Message.objects.update(created=timezone.now() - datetime.timedelta(days=4000))
        settings = ChatSettings.current()
        settings.retention_months = 0
        settings.save()
        self.assertEqual(retention.apply()["messages"], 0)
        self.assertEqual(Message.objects.count(), 1)
        settings.retention_months = 24
        settings.save()
        self.assertEqual(retention.apply()["messages"], 1)
