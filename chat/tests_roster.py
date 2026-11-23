"""Team chats and the roster sync."""

import datetime
from unittest import mock

from django.utils import timezone

from chat.models import Participant
from chat.services import roster
from chat.tasks import sync_team_chats
from chat.tests_base import ChatTestBase
from events.models import Season
from teams.models import StaffAssignment, TeamMembership


class RosterSyncTests(ChatTestBase):
    CLUB = "Roster FC"

    def setUp(self):
        super().setUp()
        patcher = mock.patch("chat.tasks.push_message.delay")
        patcher.start()
        self.addCleanup(patcher.stop)

    def team_chat(self, key="coach", **include):
        include = {"staff": True, "players": True, "parents": False, **include}
        response = self.post(key, "/conversations", {"kind": "team", "team_id": str(self.team.pk), "include": include})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def members_of(self, cid, left=False):
        return {p.member.first_name for p in Participant.objects.filter(conversation_id=cid, left_at__isnull=not left).select_related("member")}

    def test_include_flags(self):
        self.assertEqual(self.members_of(self.team_chat(staff=True, players=True, parents=False)), {"Carl", "Pia", "Paul"})
        self.assertEqual(self.members_of(self.team_chat(staff=False, players=True, parents=True)), {"Carl", "Pia", "Paul", "Pat"})  # the coach made it, so is in it
        self.assertEqual(self.members_of(self.team_chat(staff=True, players=False, parents=False)), {"Carl"})

    def test_staff_are_admins_of_the_team_chat_and_labels_use_the_position(self):
        cid = self.team_chat()
        doc = self.get("p2", f"/conversations/{cid}").json()
        by_name = {p["person"]["name"]: p for p in doc["participants"]}
        self.assertTrue(by_name["Carl Coach"]["is_admin"])
        self.assertFalse(by_name["Paul Player"]["is_admin"])
        self.assertEqual(by_name["Carl Coach"]["person"]["role_label"], "Head coach")
        self.assertEqual(by_name["Pia Player"]["person"]["role"], "player")
        self.assertEqual(doc["conversation"]["title"], "U14")

    def test_new_roster_member_is_added_and_leavers_get_left_at(self):
        cid = self.team_chat()
        with self.captureOnCommitCallbacks(execute=True):
            TeamMembership.objects.create(team=self.team, member=self.m["plain"], season=self.season, position=self.forward)
        self.assertIn("Pam", self.members_of(cid))
        self.assertEqual(self.get("plain", "/conversations").json()["results"][0]["id"], cid)
        with self.captureOnCommitCallbacks(execute=True):
            TeamMembership.objects.filter(member=self.m["plain"]).delete()
        self.assertNotIn("Pam", self.members_of(cid))
        self.assertIn("Pam", self.members_of(cid, left=True))
        self.assertEqual(self.get("plain", f"/conversations/{cid}").status_code, 404)  # no longer sees it
        with self.captureOnCommitCallbacks(execute=True):
            TeamMembership.objects.create(team=self.team, member=self.m["plain"], season=self.season, position=self.forward)
        self.assertIn("Pam", self.members_of(cid))  # back, same row

    def test_staff_changes_sync_too(self):
        cid = self.team_chat()
        with self.captureOnCommitCallbacks(execute=True):
            StaffAssignment.objects.create(team=self.team, member=self.m["stranger"], season=self.season, position=self.head_coach)
        self.assertTrue(Participant.objects.get(conversation_id=cid, member=self.m["stranger"]).is_admin)

    def test_people_added_by_hand_stay_and_removed_people_do_not_come_back(self):
        cid = self.team_chat()
        self.post("coach", f"/conversations/{cid}/participants", {"member_ids": [str(self.m["stranger"].pk)]})
        self.call("coach", "delete", f"/conversations/{cid}/participants/{self.m['p2'].pk}")
        roster.sync(roster.Conversation.objects.get(pk=cid))
        self.assertIn("Sam", self.members_of(cid))  # not on the roster, but added by hand
        self.assertNotIn("Paul", self.members_of(cid))  # on the roster, but taken out by an admin
        TeamMembership.objects.filter(team=self.team).delete()
        roster.sync(roster.Conversation.objects.get(pk=cid))
        self.assertEqual(self.members_of(cid), {"Carl", "Sam"})  # the coach made the chat, Sam was added by hand

    def test_nightly_task_and_season_rollover(self):
        cid = self.team_chat()
        today = timezone.localdate()
        next_season = Season.objects.create(start_date=today + datetime.timedelta(days=201), end_date=today + datetime.timedelta(days=500))
        TeamMembership.objects.create(team=self.team, member=self.m["plain"], season=next_season, position=self.forward)
        self.assertNotIn("Pam", self.members_of(cid))  # next season's roster is not this season's
        with mock.patch("chat.services.people.current_season", return_value=next_season):
            result = roster.sync_all()
        self.assertEqual(result["conversations"], 1)
        self.assertIn("Pam", self.members_of(cid))
        self.assertNotIn("Paul", self.members_of(cid))  # last season's players left
        self.assertIn("Paul", self.members_of(cid, left=True))
        self.assertIn("Carl", self.members_of(cid))  # the creator stays: added by hand as the chat's admin

    def test_task_skips_clubs_without_the_feature(self):
        with mock.patch("features.entitlements.resolve_entitlements") as resolve:
            resolve.return_value.has.return_value = False
            self.assertEqual(sync_team_chats.run(), "0 added, 0 left")
