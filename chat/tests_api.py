"""Chat API: feature gate, who may see and post what, polling cursors, idempotent sends, reactions, attachments, moderation."""

import io
import uuid
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from chat.models import Conversation, Message, Participant
from chat.tests_base import WITHOUT, ChatTestBase


def png_bytes():
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, "PNG")
    return out.getvalue()


class GateTests(ChatTestBase):
    CLUB = "Gate FC"

    def test_feature_off_is_404_everywhere(self):
        self.features = WITHOUT
        for method, path in [("get", "/conversations"), ("post", "/conversations"), ("get", "/people"), ("get", "/teams"), ("get", "/moderation/reports")]:
            self.assertEqual(self.call("coach", method, path, {"kind": "group", "title": "x"} if method == "post" else None).status_code, 404, path)

    def test_me_flags(self):
        from django.test import Client

        from controlpanel.tests import login_session

        def fetch(key):
            return Client().get("/api/v1/me", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.users[key])).json()

        self.assertTrue(fetch("coach")["can_use_chat"])
        self.assertFalse(fetch("coach")["can_moderate_chat"])
        self.assertTrue(fetch("mod")["can_moderate_chat"])
        self.assertTrue(fetch("announcer")["can_announce_chat"])
        self.features = WITHOUT
        self.assertFalse(fetch("mod")["can_use_chat"])
        self.assertFalse(fetch("mod")["can_moderate_chat"])

    def test_signed_in_without_member_is_403(self):
        self.assertEqual(self.call("nomember", "get", "/conversations").status_code, 403)

    def test_players_and_parents_are_not_kept_out_by_the_management_door(self):
        self.assertEqual(self.get("p2", "/conversations").status_code, 200)
        self.assertEqual(self.get("parent", "/conversations").status_code, 200)


class ConversationTests(ChatTestBase):
    CLUB = "Convo FC"

    def test_direct_chat_is_unique_per_pair_and_titled_for_the_viewer(self):
        a = self.post("p2", "/conversations", {"kind": "direct", "member_ids": [str(self.m["stranger"].pk)]})
        self.assertEqual(a.status_code, 201)
        b = self.post("stranger", "/conversations", {"kind": "direct", "member_ids": [str(self.m["p2"].pk)]})
        self.assertEqual(b.status_code, 200)
        self.assertEqual(a.json()["id"], b.json()["id"])
        self.assertEqual(a.json()["title"], "Sam Stranger")
        self.assertEqual(b.json()["title"], "Paul Player")
        self.assertEqual(self.post("p2", "/conversations", {"kind": "direct", "member_ids": [str(self.m["p2"].pk)]}).status_code, 422)

    def test_group_needs_title_and_people_and_creator_is_admin(self):
        self.assertEqual(self.post("p2", "/conversations", {"kind": "group", "title": "", "member_ids": [str(self.m["stranger"].pk)]}).status_code, 422)
        self.assertEqual(self.post("p2", "/conversations", {"kind": "group", "title": "X", "member_ids": []}).status_code, 422)
        cid = self.start("p2", with_=["stranger", "plain"])
        doc = self.get("p2", f"/conversations/{cid}").json()
        self.assertTrue(doc["conversation"]["is_admin"])
        self.assertEqual(doc["conversation"]["participant_count"], 3)
        self.assertFalse(self.get("stranger", f"/conversations/{cid}").json()["conversation"]["is_admin"])
        self.assertEqual(self.get("coach", f"/conversations/{cid}").status_code, 404)  # not in it

    def test_list_is_mine_newest_first_with_unread(self):
        c1 = self.start("p2", with_=["stranger"], title="One")
        c2 = self.start("p2", with_=["stranger"], title="Two")
        self.say("p2", c1, "first")
        self.say("p2", c2, "second")
        self.say("p2", c1, "third")
        listing = self.get("stranger", "/conversations").json()
        self.assertEqual([c["title"] for c in listing["results"]], ["One", "Two"])
        self.assertEqual(listing["unread_total"], 3)
        self.assertEqual(listing["results"][0]["unread"], 2)
        self.assertEqual(listing["results"][0]["last_message"]["body"], "third")
        self.assertEqual(self.get("coach", "/conversations").json()["results"], [])

    def test_people_search_and_teams(self):
        found = self.get("p2", "/people?q=sam").json()["results"]
        self.assertEqual([p["name"] for p in found], ["Sam Stranger"])
        self.assertNotIn(self.m["p2"].pk, [uuid.UUID(p["member_id"]) for p in self.get("p2", "/people").json()["results"]])
        roles = {p["name"]: p["role"] for p in self.get("p2", "/people").json()["results"]}
        self.assertEqual(roles["Carl Coach"], "staff")
        self.assertEqual(roles["Pia Player"], "player")
        self.assertEqual(roles["Pat Parent"], "parent")
        self.assertEqual(roles["Sam Stranger"], "other")
        labels = {p["name"]: p["role_label"] for p in self.get("p2", "/people").json()["results"]}
        self.assertEqual(labels["Carl Coach"], "Head coach")
        self.assertEqual([t["name"] for t in self.get("coach", "/teams").json()["results"]], ["U14"])
        self.assertEqual(self.get("p2", "/teams").json()["results"], [])
        self.assertEqual(len(self.get("mod", "/teams").json()["results"]), 2)

    def test_team_chat_creation_rights_and_announcements(self):
        body = {"kind": "team", "team_id": str(self.team.pk), "include": {"staff": True, "players": True, "parents": False}}
        self.assertEqual(self.post("p2", "/conversations", body).status_code, 403)
        self.assertEqual(self.post("stranger", "/conversations", {**body, "mode": "announcement"}).status_code, 403)
        other = {**body, "team_id": str(self.other_team.pk)}
        self.assertEqual(self.post("coach", "/conversations", other).status_code, 403)  # not staff of that team
        self.assertEqual(self.post("mod", "/conversations", other).status_code, 201)  # moderators may
        self.assertEqual(self.post("mod", "/conversations", {**body, "mode": "announcement"}).status_code, 403)  # but not announce without the right
        created = self.post("coach", "/conversations", {**body, "mode": "announcement"})
        self.assertEqual(created.status_code, 201)
        doc = created.json()
        self.assertEqual(doc["kind"], "team")
        self.assertEqual(doc["mode"], "announcement")
        self.assertEqual(doc["team"]["name"], "U14")
        self.assertTrue(doc["is_admin"])
        self.assertEqual(doc["participant_count"], 3)  # coach, two players
        club_wide = self.post("announcer", "/conversations", {"kind": "group", "title": "News", "mode": "announcement", "member_ids": [str(self.m["plain"].pk)]})
        self.assertEqual(club_wide.status_code, 201)
        self.assertEqual(self.post("p2", "/conversations", {"kind": "group", "title": "News", "mode": "announcement", "member_ids": [str(self.m["plain"].pk)]}).status_code, 403)

    def test_patch_rights_mute_title_mode_archive(self):
        cid = self.start("p2", with_=["stranger"])
        self.assertEqual(self.call("stranger", "patch", f"/conversations/{cid}", {"muted": True}).json()["muted"], True)
        self.assertEqual(self.call("stranger", "patch", f"/conversations/{cid}", {"title": "Hack"}).status_code, 403)
        self.assertEqual(self.call("p2", "patch", f"/conversations/{cid}", {"title": "Renamed", "archived": True}).json()["title"], "Renamed")
        self.assertEqual(self.say("p2", cid).status_code, 422)  # archived
        self.assertEqual(self.call("p2", "patch", f"/conversations/{cid}", {"mode": "announcement"}).status_code, 403)  # needs chat.announce
        self.assertTrue(Participant.objects.get(conversation_id=cid, member=self.m["stranger"]).muted)

    def test_participants_add_remove_and_no_leaving(self):
        cid = self.start("p2", with_=["stranger"])
        self.assertEqual(self.post("stranger", f"/conversations/{cid}/participants", {"member_ids": [str(self.m["plain"].pk)]}).status_code, 403)
        added = self.post("p2", f"/conversations/{cid}/participants", {"member_ids": [str(self.m["plain"].pk)]})
        self.assertEqual(len(added.json()["participants"]), 3)
        self.assertEqual(self.call("stranger", "delete", f"/conversations/{cid}/participants/{self.m['stranger'].pk}").status_code, 403)  # cannot leave
        self.assertEqual(self.call("p2", "delete", f"/conversations/{cid}/participants/{self.m['p2'].pk}").status_code, 422)  # nor can the admin drop themselves
        self.assertEqual(self.call("p2", "delete", f"/conversations/{cid}/participants/{self.m['plain'].pk}").status_code, 204)
        self.assertEqual(self.get("plain", f"/conversations/{cid}").status_code, 404)
        direct = self.start("p2", "direct", member_ids=[str(self.m["stranger"].pk)])
        self.assertEqual(self.post("p2", f"/conversations/{direct}/participants", {"member_ids": [str(self.m["plain"].pk)]}).status_code, 422)


class MessageTests(ChatTestBase):
    CLUB = "Msg FC"

    def setUp(self):
        super().setUp()
        patcher = mock.patch("chat.tasks.push_message.delay")
        self.push = patcher.start()
        self.addCleanup(patcher.stop)
        self.cid = self.start("p2", with_=["stranger", "plain"])

    def test_send_and_idempotent_client_key(self):
        first = self.post("p2", f"/conversations/{self.cid}/messages", {"body": " hi ", "client_key": "k1"})
        self.assertEqual(first.status_code, 201)
        doc = first.json()
        self.assertEqual((doc["seq"], doc["body"], doc["mine"], doc["client_key"]), (1, "hi", True, "k1"))
        self.assertEqual(doc["author"]["name"], "Paul Player")
        again = self.post("p2", f"/conversations/{self.cid}/messages", {"body": "hi again", "client_key": "k1"})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["id"], doc["id"])
        self.assertEqual(Message.objects.filter(conversation_id=self.cid).count(), 1)
        other = self.post("stranger", f"/conversations/{self.cid}/messages", {"body": "same key other person", "client_key": "k1"})
        self.assertEqual(other.status_code, 201)
        self.assertEqual(other.json()["seq"], 2)
        self.assertIsNone(self.get("p2", f"/conversations/{self.cid}/messages").json()["messages"][1]["client_key"])  # only your own keys are shown

    def test_validation(self):
        url = f"/conversations/{self.cid}/messages"
        self.assertEqual(self.post("p2", url, {"body": "", "client_key": "a"}).status_code, 422)
        self.assertEqual(self.post("p2", url, {"body": "x" * 4001, "client_key": "b"}).status_code, 422)
        self.assertEqual(self.post("p2", url, {"body": "x", "client_key": ""}).status_code, 422)
        self.assertEqual(self.post("p2", url, {"body": "x", "client_key": "c", "attachment_ids": [str(uuid.uuid4())]}).status_code, 422)
        self.assertEqual(self.post("p2", url, {"body": "x", "client_key": "d", "urgent": True}).status_code, 422)  # urgent is for announcements
        self.assertEqual(self.post("coach", url, {"body": "x", "client_key": "e"}).status_code, 404)  # not in the chat

    def test_polling_cursors_and_read_state(self):
        for i in range(1, 6):
            self.say("p2", self.cid, f"m{i}")
        url = f"/conversations/{self.cid}/messages"
        latest = self.get("stranger", f"{url}?limit=2").json()
        self.assertEqual([m["seq"] for m in latest["messages"]], [4, 5])
        self.assertTrue(latest["has_more_before"])
        older = self.get("stranger", f"{url}?before=4&limit=2").json()
        self.assertEqual([m["seq"] for m in older["messages"]], [2, 3])
        self.assertEqual([m["seq"] for m in self.get("stranger", f"{url}?before=2&limit=5").json()["messages"]], [1])
        self.assertFalse(self.get("stranger", f"{url}?before=2&limit=5").json()["has_more_before"])
        self.assertEqual([m["seq"] for m in self.get("stranger", f"{url}?after=3").json()["messages"]], [4, 5])
        self.assertEqual(self.get("stranger", f"{url}?after=5").json()["messages"], [])
        self.assertEqual(self.get("stranger", "/conversations").json()["unread_total"], 5)  # reading the messages did not mark them read
        self.assertEqual(self.post("stranger", f"/conversations/{self.cid}/read", {"seq": 3}).json()["unread_total"], 2)
        self.assertEqual(self.post("stranger", f"/conversations/{self.cid}/read", {"seq": 1}).json()["unread_total"], 2)  # never goes back
        self.assertEqual(self.post("stranger", f"/conversations/{self.cid}/read", {"seq": 99}).json()["unread_total"], 0)
        self.assertEqual(self.get("p2", "/conversations").json()["unread_total"], 0)  # my own messages are read

    def test_changed_after_returns_reactions_and_deletions_on_older_messages(self):
        first = self.say("p2", self.cid, "one").json()["id"]
        self.say("p2", self.cid, "two")
        url = f"/conversations/{self.cid}/messages"
        stamp = self.get("stranger", f"{url}?after=2&changed_after=2000-01-01T00:00:00Z").json()
        self.assertEqual(stamp["messages"], [])
        poll = stamp["server_time"]
        self.post("plain", f"/messages/{first}/reactions", {"emoji": "🙏"})
        self.say("p2", self.cid, "three")
        doc = self.get("stranger", f"{url}?after=2&changed_after={poll}").json()
        self.assertEqual([m["seq"] for m in doc["messages"]], [3])
        self.assertEqual([m["id"] for m in doc["changed"]], [first])
        self.assertEqual(doc["changed"][0]["reactions"][0]["emoji"], "🙏")
        self.assertNotIn("changed", self.get("stranger", f"{url}?after=2").json())

    def test_portal_access_exposes_chat_enabled(self):
        from django.test import Client

        from controlpanel.tests import login_session

        def flag():
            return Client().get("/api/v1/portal/access", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.users["parent"])).json()["chat_enabled"]

        self.assertTrue(flag())
        self.features = WITHOUT
        self.assertFalse(flag())

    def test_muted_chats_do_not_count_in_the_total(self):
        self.say("p2", self.cid)
        self.call("stranger", "patch", f"/conversations/{self.cid}", {"muted": True})
        listing = self.get("stranger", "/conversations").json()
        self.assertEqual(listing["unread_total"], 0)
        self.assertEqual(listing["results"][0]["unread"], 1)

    def test_delete_rights_and_soft_delete(self):
        mine = self.say("stranger", self.cid, "oops").json()["id"]
        theirs = self.say("p2", self.cid, "admin says").json()["id"]
        self.assertEqual(self.call("plain", "delete", f"/messages/{mine}").status_code, 403)
        self.assertEqual(self.call("stranger", "delete", f"/messages/{theirs}").status_code, 403)
        self.assertEqual(self.call("stranger", "delete", f"/messages/{mine}").status_code, 204)
        self.assertEqual(self.call("p2", "delete", f"/messages/{theirs}").status_code, 204)  # chat admin
        docs = self.get("p2", f"/conversations/{self.cid}/messages").json()["messages"]
        self.assertTrue(all(m["deleted"] and m["body"] == "" for m in docs))
        self.assertEqual(self.call("coach", "delete", f"/messages/{mine}").status_code, 404)

    def test_reactions_toggle_and_allowed_set(self):
        mid = self.say("p2", self.cid).json()["id"]
        r = self.post("stranger", f"/messages/{mid}/reactions", {"emoji": "👍"}).json()["reactions"]
        self.assertEqual(r, [{"emoji": "👍", "count": 1, "mine": True}])
        r = self.post("plain", f"/messages/{mid}/reactions", {"emoji": "👍"}).json()["reactions"]
        self.assertEqual(r, [{"emoji": "👍", "count": 2, "mine": True}])
        self.assertEqual(self.post("stranger", f"/messages/{mid}/reactions", {"emoji": "👍"}).json()["reactions"], [{"emoji": "👍", "count": 1, "mine": False}])
        self.assertEqual(self.post("stranger", f"/messages/{mid}/reactions", {"emoji": "💩"}).status_code, 422)
        self.assertEqual(self.post("coach", f"/messages/{mid}/reactions", {"emoji": "👍"}).status_code, 404)

    def test_report_and_moderation_queue(self):
        mid = self.say("p2", self.cid, "rude").json()["id"]
        self.assertEqual(self.post("stranger", f"/messages/{mid}/report", {"reason": "rude"}).status_code, 204)
        self.assertEqual(self.post("stranger", f"/messages/{mid}/report", {"reason": ""}).status_code, 422)
        self.assertEqual(self.get("stranger", "/moderation/reports").status_code, 403)
        reports = self.get("mod", "/moderation/reports").json()["results"]
        self.assertEqual(len(reports), 1)
        self.assertEqual((reports[0]["reason"], reports[0]["handled"], reports[0]["reporter"]["name"], reports[0]["message"]["body"]), ("rude", False, "Sam Stranger", "rude"))
        self.assertEqual(self.post("stranger", f"/moderation/reports/{reports[0]['id']}/handle").status_code, 403)
        self.assertTrue(self.post("mod", f"/moderation/reports/{reports[0]['id']}/handle").json()["handled"])
        self.assertTrue(self.get("mod", "/moderation/reports").json()["results"][0]["handled"])

    def test_attachments_upload_send_and_serve(self):
        url = f"/conversations/{self.cid}/attachments"
        up = self.call("p2", "post", url, content_type="multipart/form-data; boundary=BoUnDaRy", data=self._multipart(SimpleUploadedFile("pic.png", png_bytes(), "image/png")))
        self.assertEqual(up.status_code, 201, up.content)
        att = up.json()
        self.assertEqual((att["name"], att["content_type"]), ("pic.png", "image/png"))
        bad = self.call("p2", "post", url, content_type="multipart/form-data; boundary=BoUnDaRy", data=self._multipart(SimpleUploadedFile("x.png", b"not an image", "image/png")))
        self.assertEqual(bad.status_code, 422)
        svg = self.call("p2", "post", url, content_type="multipart/form-data; boundary=BoUnDaRy", data=self._multipart(SimpleUploadedFile("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")))
        self.assertEqual(svg.status_code, 422)
        pdf = self.call("p2", "post", url, content_type="multipart/form-data; boundary=BoUnDaRy", data=self._multipart(SimpleUploadedFile("a.pdf", b"%PDF-1.4 test", "application/pdf")))
        self.assertEqual(pdf.status_code, 201)
        self.assertEqual(self.post("stranger", f"/conversations/{self.cid}/messages", {"body": "", "client_key": "z", "attachment_ids": [att["id"]]}).status_code, 422)  # not theirs
        sent = self.post("p2", f"/conversations/{self.cid}/messages", {"body": "", "client_key": "z", "attachment_ids": [att["id"]]})
        self.assertEqual(sent.status_code, 201)
        self.assertEqual(sent.json()["attachments"][0]["id"], att["id"])
        self.assertEqual(self.post("p2", f"/conversations/{self.cid}/messages", {"body": "", "client_key": "z2", "attachment_ids": [att["id"]]}).status_code, 422)  # already used
        from django.test import Client

        from controlpanel.tests import login_session

        def fetch(key):
            return Client().get(sent.json()["attachments"][0]["url"], HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=login_session(self.users[key]))

        ok = fetch("stranger")
        self.assertEqual((ok.status_code, ok["Content-Type"]), (200, "image/png"))
        self.assertEqual(fetch("coach").status_code, 404)

    @staticmethod
    def _multipart(upload):
        body = b"--BoUnDaRy\r\n" + f'Content-Disposition: form-data; name="file"; filename="{upload.name}"\r\nContent-Type: {upload.content_type}\r\n\r\n'.encode() + upload.read() + b"\r\n--BoUnDaRy--\r\n"
        return body

    def test_announcement_mode_only_admins_post_everyone_reacts_and_can_dm_sender(self):
        announce = self.start("announcer", "group", title="Club news", mode="announcement", with_=["stranger"])
        self.assertEqual(self.say("stranger", announce).status_code, 403)
        sent = self.say("announcer", announce, "Hall closed", urgent=True)
        self.assertEqual(sent.status_code, 201)
        self.assertTrue(sent.json()["urgent"])
        self.assertTrue(self.get("stranger", f"/conversations/{announce}").json()["conversation"]["can_post"] is False)
        self.assertEqual(self.post("stranger", f"/messages/{sent.json()['id']}/reactions", {"emoji": "✅"}).status_code, 200)
        dm = self.post("stranger", "/conversations", {"kind": "direct", "member_ids": [str(self.m["announcer"].pk)]})
        self.assertEqual(dm.status_code, 201)

    def test_sending_queues_a_push_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.say("p2", self.cid, "ping")
        self.push.assert_called_once()
        self.push.side_effect = RuntimeError("broker down")
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.say("p2", self.cid, "ping2").status_code, 201)  # never fails the send


class ModelSanityTests(ChatTestBase):
    CLUB = "Sanity FC"

    def test_seq_is_strictly_increasing_per_conversation(self):
        a, b = self.start("p2", with_=["stranger"]), self.start("p2", with_=["stranger"])
        for cid in (a, b, a):
            self.say("p2", cid)
        self.assertEqual(list(Message.objects.filter(conversation_id=a).values_list("seq", flat=True)), [1, 2])
        self.assertEqual(Conversation.objects.get(pk=a).last_seq, 2)
        self.assertEqual(Conversation.objects.get(pk=b).last_seq, 1)
