"""Push for new messages: who is told, throttling, collapse id, category and priority."""

import datetime
from unittest import mock

from django.utils import timezone

from chat.models import Message, MessageAttachment, Participant
from chat.services import push
from chat.tests_base import ChatTestBase
from notifications.models import Category, InboxNotification, Priority


class PushTests(ChatTestBase):
    CLUB = "Push FC"

    def setUp(self):
        super().setUp()
        patcher = mock.patch("chat.tasks.push_message.delay")
        patcher.start()
        self.addCleanup(patcher.stop)
        notify = mock.patch("chat.services.push.notify_user")
        self.notify = notify.start()
        self.addCleanup(notify.stop)
        self.cid = self.start("p2", with_=["stranger", "plain"], title="Friends")

    def message(self, key="p2", body="hello there", **extra):
        mid = self.say(key, self.cid, body, **extra).json()["id"]
        return Message.objects.select_related("conversation", "author").get(pk=mid)

    def recipients(self):
        return sorted(c.args[0].email for c in self.notify.call_args_list)

    def test_pushes_to_unread_unmuted_participants_except_the_author(self):
        self.call("plain", "patch", f"/conversations/{self.cid}", {"muted": True})
        message = self.message()
        self.assertEqual(push.notify_message(message), 1)
        self.assertEqual(self.recipients(), ["stranger@chat.test"])
        kwargs = self.notify.call_args.kwargs
        self.assertEqual(kwargs["title"], "Friends")
        self.assertEqual(kwargs["body"], "Paul: hello there")
        self.assertEqual(kwargs["category"], Category.CHAT)
        self.assertEqual(kwargs["priority"], Priority.NORMAL)
        self.assertEqual(kwargs["data"]["collapse_id"], f"chat:{self.cid}")
        self.assertEqual(kwargs["data"]["conversation_id"], self.cid)
        self.assertEqual(InboxNotification.objects.count(), 0)  # never an inbox entry per message

    def test_somebody_who_already_read_it_is_not_pushed(self):
        message = self.message()
        self.post("stranger", f"/conversations/{self.cid}/read", {"seq": message.seq})
        push.notify_message(message)
        self.assertEqual(self.recipients(), ["plain@chat.test"])

    def test_one_push_per_conversation_and_person_every_five_minutes(self):
        self.assertEqual(push.notify_message(self.message(body="one")), 2)
        self.assertEqual(push.notify_message(self.message(body="two")), 0)
        self.assertEqual(push.notify_message(self.message(body="three")), 0)
        Participant.objects.filter(conversation_id=self.cid).update(last_push_at=timezone.now() - datetime.timedelta(minutes=6))
        self.assertEqual(push.notify_message(self.message(body="four")), 2)
        other = self.start("p2", with_=["stranger"], title="Other")  # a different conversation has its own allowance
        message = Message.objects.get(pk=self.say("p2", other).json()["id"])
        self.assertEqual(push.notify_message(message), 1)

    def test_attachment_only_messages_get_a_label(self):
        message = self.message(body="x")
        message.body = ""
        MessageAttachment.objects.create(conversation_id=self.cid, message=message, file="x.png", name="x.png", content_type="image/png", size=1)
        self.assertEqual(push.preview(message), "Paul: sent a photo")
        MessageAttachment.objects.create(conversation_id=self.cid, message=message, file="x.pdf", name="x.pdf", content_type="application/pdf", size=1)
        self.assertEqual(push.preview(message), "Paul: sent a file")

    def test_long_messages_are_cut(self):
        self.assertLessEqual(len(push.preview(self.message(body="word " * 200))), 150)

    def test_announcement_chats_use_the_announcement_category_and_urgent_is_critical_and_unthrottled(self):
        announce = self.start("announcer", "group", title="News", mode="announcement", with_=["stranger"])
        first = Message.objects.get(pk=self.say("announcer", announce, "Normal news").json()["id"])
        self.assertEqual(push.notify_message(first), 1)
        self.assertEqual(self.notify.call_args.kwargs["category"], Category.ANNOUNCEMENT)
        self.assertEqual(self.notify.call_args.kwargs["priority"], Priority.NORMAL)
        urgent = Message.objects.get(pk=self.say("announcer", announce, "Hall flooded", urgent=True).json()["id"])
        self.assertEqual(push.notify_message(urgent), 1)  # within five minutes, still goes out
        self.assertEqual(self.notify.call_args.kwargs["priority"], Priority.CRITICAL)

    def test_a_failing_push_never_breaks_the_rest(self):
        self.notify.side_effect = [RuntimeError("boom"), None]
        self.assertEqual(push.notify_message(self.message()), 1)

    def test_deleted_messages_and_archived_chats_are_not_pushed(self):
        message = self.message()
        self.call("p2", "delete", f"/messages/{message.pk}")
        message.refresh_from_db()
        self.assertEqual(push.notify_message(message), 0)
        fresh = self.message(body="again")
        self.call("p2", "patch", f"/conversations/{self.cid}", {"archived": True})
        fresh.conversation.refresh_from_db()
        self.assertEqual(push.notify_message(fresh), 0)

    def test_task_runs_the_service(self):
        from chat.tasks import push_message

        message = self.message()
        self.assertEqual(push_message.run(str(message.pk)), "2 push(es)")
        self.assertEqual(push_message.run("00000000-0000-0000-0000-000000000000"), "0 push(es)")
