"""Safeguards for minors, moderation, retention, erasure and the club backup."""

import datetime
from unittest import mock

from django.core.files.base import ContentFile
from django.utils import timezone

from chat.models import ChatSettings, Conversation, Message, MessageAttachment, MessageReport, Participant
from chat.services import privacy, retention
from chat.tests_api import png_bytes
from chat.tests_base import ChatTestBase
from controlpanel.services import club_backup
from members.models import AuditEntry, Member
from members.services import members as member_services


class SafeguardTests(ChatTestBase):
    CLUB = "Guard FC"

    def setUp(self):
        super().setUp()
        patcher = mock.patch("chat.tasks.push_message.delay")
        patcher.start()
        self.addCleanup(patcher.stop)

    def ids(self, cid):
        return {p.member.first_name for p in Participant.objects.filter(conversation_id=cid, left_at__isnull=True).select_related("member")}

    # -- parents read along --

    def test_parent_reads_a_childs_chats_read_only(self):
        cid = self.start("p1", with_=["p3"], title="Kids")
        mid = self.say("p3", cid, "hi Pia").json()["id"]
        listing = self.get("parent", "/conversations").json()
        self.assertEqual([c["id"] for c in listing["results"]], [cid])
        doc = listing["results"][0]
        self.assertTrue(doc["read_only_parent_view"])
        self.assertFalse(doc["can_post"])
        self.assertFalse(doc["is_admin"])
        self.assertEqual(doc["unread"], 0)
        self.assertEqual(listing["unread_total"], 0)
        self.assertEqual([m["body"] for m in self.get("parent", f"/conversations/{cid}/messages").json()["messages"]], ["hi Pia"])
        self.assertEqual(self.get("parent", f"/conversations/{cid}").status_code, 200)
        self.assertEqual(self.say("parent", cid, "sneaky").status_code, 403)
        self.assertEqual(self.post("parent", f"/messages/{mid}/reactions", {"emoji": "👍"}).status_code, 403)
        self.assertEqual(self.call("parent", "patch", f"/conversations/{cid}", {"muted": True}).status_code, 403)
        self.assertEqual(self.call("parent", "delete", f"/messages/{mid}").status_code, 403)
        self.assertEqual(self.post("parent", f"/messages/{mid}/report", {"reason": "worried"}).status_code, 204)
        self.post("parent", f"/conversations/{cid}/read", {"seq": 1})
        self.assertFalse(Participant.objects.filter(conversation_id=cid, member=self.m["parent"]).exists())
        self.assertEqual(self.get("stranger", f"/conversations/{cid}").status_code, 404)

    def test_parent_in_the_chat_is_a_normal_participant(self):
        self.start("p1", with_=["parent"], title="Family")
        doc = self.get("parent", "/conversations").json()["results"][0]
        self.assertFalse(doc["read_only_parent_view"])
        self.assertTrue(doc["can_post"])

    def test_only_minors_chats_are_visible_to_parents(self):
        cid = self.start("p2", with_=["stranger"], title="Adults")
        self.assertEqual(self.get("parent", "/conversations").json()["results"], [])
        self.assertEqual(self.get("parent", f"/conversations/{cid}").status_code, 404)

    def test_attachments_of_a_childs_chat_are_visible_to_the_parent_only(self):
        cid = self.start("p1", with_=["p3"])
        message = Message.objects.create(conversation_id=cid, seq=1, author=self.m["p2"], body="")
        attachment = MessageAttachment.objects.create(conversation_id=cid, message=message, file=ContentFile(png_bytes(), "x.png"), name="x.png", content_type="image/png", size=1)
        from django.test import Client

        from controlpanel.tests import login_session

        def status(key):
            return Client().get(f"/chat/attachments/{attachment.pk}/", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.users[key])).status_code

        self.assertEqual(status("stranger"), 404)
        self.assertEqual(status("coach"), 404)
        self.assertEqual(status("parent"), 200)
        self.assertEqual(status("p3"), 200)
        self.assertEqual(status("mod"), 200)

    # -- minors with adults --

    def test_adult_who_is_not_staff_or_parent_chats_with_a_minor_only_with_the_parent_in(self):
        response = self.post("stranger", "/conversations", {"kind": "direct", "member_ids": [str(self.m["p1"].pk)]})
        self.assertEqual(response.status_code, 201)
        doc = response.json()
        self.assertEqual(doc["kind"], "group")
        self.assertEqual(self.ids(doc["id"]), {"Sam", "Pia", "Pat"})
        again = self.post("stranger", "/conversations", {"kind": "direct", "member_ids": [str(self.m["p1"].pk)]})
        self.assertEqual((again.status_code, again.json()["id"]), (200, doc["id"]))  # the same pair finds the same chat
        self.assertEqual(len(self.get("parent", "/conversations").json()["results"]), 1)

    def test_staff_and_parents_may_chat_directly_with_a_minor(self):
        self.assertEqual(self.post("coach", "/conversations", {"kind": "direct", "member_ids": [str(self.m["p1"].pk)]}).json()["kind"], "direct")
        self.assertEqual(self.post("parent", "/conversations", {"kind": "direct", "member_ids": [str(self.m["p1"].pk)]}).json()["kind"], "direct")
        self.assertEqual(self.post("p2", "/conversations", {"kind": "direct", "member_ids": [str(self.m["plain"].pk)]}).json()["kind"], "direct")  # two adults

    def test_adding_a_stranger_to_a_childs_group_adds_the_parent(self):
        cid = self.start("p1", with_=["p3"], title="Kids")
        self.assertEqual(self.ids(cid), {"Pia", "Pip"})
        self.post("p1", f"/conversations/{cid}/participants", {"member_ids": [str(self.m["stranger"].pk)]})
        self.assertEqual(self.ids(cid), {"Pia", "Pip", "Sam", "Pat"})

    def test_a_child_without_a_parent_login_cannot_be_put_with_strangers(self):
        Member.objects.filter(pk=self.m["parent"].pk).update(user=None)
        response = self.post("stranger", "/conversations", {"kind": "direct", "member_ids": [str(self.m["p1"].pk)]})
        self.assertEqual((response.status_code, response.json()["code"]), (422, "minor_needs_parent"))
        self.assertFalse(Conversation.objects.exists())

    # -- moderation --

    def test_moderators_see_chats_with_minors_and_every_look_is_audited(self):
        kids = self.start("p1", with_=["p3"], title="Kids")
        adults = self.start("stranger", with_=["plain"], title="Adults")
        self.say("p1", kids, "private stuff")
        self.assertEqual(self.get("stranger", "/moderation/conversations?minors=1").status_code, 403)
        self.assertEqual(self.get("coach", f"/moderation/conversations/{kids}/messages").status_code, 403)
        listing = self.get("mod", "/moderation/conversations?minors=1").json()["results"]
        self.assertEqual([c["id"] for c in listing], [kids])
        self.assertFalse(listing[0]["read_only_parent_view"])
        self.assertEqual({c["id"] for c in self.get("mod", "/moderation/conversations").json()["results"]}, {kids, adults})
        read = self.get("mod", f"/moderation/conversations/{kids}/messages").json()
        self.assertEqual([m["body"] for m in read["messages"]], ["private stuff"])
        self.assertEqual(len(read["participants"]), 2)
        actions = list(AuditEntry.objects.filter(action__startswith="chat.").values_list("action", flat=True))
        self.assertIn("chat.moderation_listed", actions)
        self.assertIn("chat.moderation_viewed", actions)
        self.assertEqual(self.get("mod", "/conversations").json()["results"], [])  # moderating does not put you in the chats

    def test_moderator_deletes_a_message_and_it_is_audited(self):
        cid = self.start("p1", with_=["p3"])
        mid = self.say("p1", cid, "bad").json()["id"]
        self.assertEqual(self.call("mod", "delete", f"/messages/{mid}").status_code, 204)
        self.assertTrue(Message.objects.get(pk=mid).deleted)
        self.assertTrue(AuditEntry.objects.filter(action="chat.message_deleted").exists())

    def test_handling_a_report_is_audited(self):
        cid = self.start("p1", with_=["p3"])
        mid = self.say("p1", cid, "bad").json()["id"]
        self.post("p3", f"/messages/{mid}/report", {"reason": "bad"})
        report = MessageReport.objects.get()
        self.post("mod", f"/moderation/reports/{report.pk}/handle")
        self.assertTrue(AuditEntry.objects.filter(action="chat.report_handled").exists())
        self.assertEqual(self.get("mod", "/moderation/reports").status_code, 200)
        self.assertTrue(AuditEntry.objects.filter(action="chat.reports_listed").exists())

    def test_moderators_can_open_attachments_of_any_chat(self):
        from members.services import access

        self.assertTrue(access.can_moderate_chat(self.users["mod"]))
        self.assertFalse(access.can_moderate_chat(self.users["coach"]))


class HousekeepingTests(ChatTestBase):
    CLUB = "Keep FC"

    def setUp(self):
        super().setUp()
        patcher = mock.patch("chat.tasks.push_message.delay")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cid = self.start("p2", with_=["stranger"])

    def age(self, message_id, days):
        Message.objects.filter(pk=message_id).update(created=timezone.now() - datetime.timedelta(days=days))

    def test_retention_defaults_to_24_months_and_is_configurable(self):
        self.assertEqual(ChatSettings.current().retention_months, 24)
        old = self.say("p2", self.cid, "old").json()["id"]
        recent = self.say("p2", self.cid, "recent").json()["id"]
        self.age(old, 800)
        self.age(recent, 700)
        self.assertEqual(retention.apply()["messages"], 1)
        self.assertEqual(list(Message.objects.values_list("body", flat=True)), ["recent"])
        ChatSettings.objects.update(retention_months=12)
        self.assertEqual(retention.apply()["messages"], 1)
        self.assertFalse(Message.objects.exists())

    def test_unhandled_reports_keep_a_message_until_handled(self):
        mid = self.say("p2", self.cid, "evidence").json()["id"]
        self.post("stranger", f"/messages/{mid}/report", {"reason": "x"})
        self.age(mid, 2000)
        self.assertEqual(retention.apply()["messages"], 0)
        MessageReport.objects.update(handled_at=timezone.now())
        self.assertEqual(retention.apply()["messages"], 1)

    def test_unused_uploads_are_cleaned_up_after_a_day(self):
        stale = MessageAttachment.objects.create(conversation_id=self.cid, uploader=self.m["p2"], file="chat/a.png", name="a.png", content_type="image/png", size=1)
        fresh = MessageAttachment.objects.create(conversation_id=self.cid, uploader=self.m["p2"], file="chat/b.png", name="b.png", content_type="image/png", size=1)
        MessageAttachment.objects.filter(pk=stale.pk).update(created=timezone.now() - datetime.timedelta(days=2))
        retention.apply()
        self.assertEqual(list(MessageAttachment.objects.values_list("pk", flat=True)), [fresh.pk])

    def test_task_loops_over_clubs_with_the_feature(self):
        from chat.tasks import apply_retention

        old = self.say("p2", self.cid, "old").json()["id"]
        self.age(old, 900)
        with mock.patch("features.entitlements.resolve_entitlements", return_value=self.features):
            self.assertEqual(apply_retention.run(), "1 message(s) removed")

    def test_deleting_a_member_anonymises_what_they_wrote(self):
        mine = self.say("stranger", self.cid, "my address is ...").json()["id"]
        self.say("p2", self.cid, "other")
        member_services.delete_member(self.club.owner, self.m["stranger"], detach_login=True)
        message = Message.objects.get(pk=mine)
        self.assertEqual((message.author_id, message.body, message.deleted), (None, "", True))
        doc = self.get("p2", f"/conversations/{self.cid}/messages").json()["messages"]
        self.assertEqual(doc[0]["author"]["name"], "Former member")
        self.assertEqual(doc[1]["body"], "other")

    def test_anonymise_counts(self):
        self.say("stranger", self.cid, "a")
        self.say("stranger", self.cid, "b")
        self.assertEqual(privacy.anonymise_member(self.m["stranger"]), 2)

    def test_chat_is_part_of_the_club_backup(self):
        self.assertIn("chat.conversation", club_backup.tenant_models())
        self.assertIn("chat.message", club_backup.tenant_models())
        self.assertIn("chat.messageattachment", club_backup.tenant_models())
