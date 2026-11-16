"""Event attachments (events.services.attachments): upload rules, permissions, private storage and cleanup."""

import os
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from events.models import EventAttachment
from events.services import attachments as svc
from events.tests_tasks import EventPeopleMixin
from members.models import AuditEntry
from members.services.errors import Invalid, NotAllowed


def upload(name="schedule.pdf", content=b"%PDF-1.4 hi"):
    return SimpleUploadedFile(name, content)


class AttachmentTests(EventPeopleMixin, TestCase):
    prefix = "att"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="rc-attachments-test-")
        cls._override = override_settings(PRIVATE_MEDIA_ROOT=cls._tmp)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def path(self, attachment):
        return os.path.join(self._tmp, attachment.file.name)

    def files_on_disk(self):
        return [os.path.join(root, f) for root, _, files in os.walk(os.path.join(self._tmp, "event-attachments", str(self.event.pk))) for f in files]

    def test_manager_uploads_into_private_storage(self):
        added = svc.add_attachments(self.coach.user, self.event, [upload("a.pdf", b"a"), upload("dir/b.png", b"bb")])
        self.assertEqual(sorted(a.name for a in added), ["a.pdf", "b.png"])
        for attachment in added:
            self.assertTrue(os.path.exists(self.path(attachment)))
            self.assertTrue(attachment.file.name.startswith(f"event-attachments/{self.event.pk}/"))
            self.assertEqual(attachment.uploaded_by, self.coach)
        self.assertEqual(added[1].size, 2)
        with self.assertRaises(ValueError):  # never a public URL
            _ = added[0].file.url
        self.assertEqual(AuditEntry.objects.filter(action="event.attachment_created").count(), 2)

    def test_write_matrix(self):
        for user, allowed in ((self.owner, True), (self.coach.user, True), (self.assistant.user, False), (self.other_coach.user, False), (self.parent.user, False), (self.stranger.user, False)):
            with self.subTest(user=user.email):
                self.assertEqual(svc.can_manage_attachments(user, self.event), allowed)
                if not allowed:
                    with self.assertRaises(NotAllowed):
                        svc.add_attachments(user, self.event, [upload()])
        attachment = svc.add_attachments(self.coach.user, self.event, [upload()])[0]
        for user in (self.assistant.user, self.other_coach.user, self.parent.user):
            with self.assertRaises(NotAllowed):
                svc.delete_attachment(user, attachment)
        self.assertTrue(EventAttachment.objects.filter(pk=attachment.pk).exists())

    def test_view_and_open(self):
        attachment = svc.add_attachments(self.coach.user, self.event, [upload()])[0]
        for user in (self.owner, self.coach.user, self.assistant.user, self.other_coach.user, self.parent.user, self.solo.user):
            with self.subTest(user=user.email):
                self.assertEqual([a.name for a in svc.list_attachments(user, self.event)], ["schedule.pdf"])
                with svc.open_attachment(attachment, actor=user) as handle:
                    self.assertEqual(handle.read(), b"%PDF-1.4 hi")
        with self.assertRaises(NotAllowed):
            svc.list_attachments(self.stranger.user, self.event)
        with self.assertRaises(NotAllowed):
            svc.open_attachment(attachment, actor=self.stranger.user)
        os.remove(self.path(attachment))
        with self.assertRaises(FileNotFoundError):
            svc.open_attachment(attachment)

    def test_validation(self):
        cases = [upload("evil.html", b"<script>"), upload("icon.svg", b"<svg/>"), upload("noext", b"x"), upload(".pdf", b"x"), upload("empty.pdf", b"")]
        for bad in cases:
            with self.subTest(name=bad.name), self.assertRaises(Invalid) as ctx:
                svc.add_attachments(self.coach.user, self.event, [bad])
            self.assertIn("files", ctx.exception.errors)
        with mock.patch.object(svc, "MAX_ATTACHMENT_BYTES", 5), self.assertRaises(Invalid):
            svc.add_attachments(self.coach.user, self.event, [upload(content=b"123456")])
        with self.assertRaises(Invalid):
            svc.add_attachments(self.coach.user, self.event, [])
        # One bad file in a batch stores nothing.
        with self.assertRaises(Invalid):
            svc.add_attachments(self.coach.user, self.event, [upload("ok.pdf"), upload("bad.exe")])
        self.assertFalse(EventAttachment.objects.exists())
        self.assertEqual(self.files_on_disk(), [])
        self.assertTrue(svc.validate_attachment_file(upload("Photo.JPG")))

    def test_filename_safety(self):
        self.assertEqual(svc.display_name("C:\\Users\\me\\map.pdf"), "map.pdf")
        self.assertEqual(svc.display_name("../../etc/pass\x00wd.txt"), "passwd.txt")
        self.assertEqual(svc.display_name(".."), "")
        long = svc.display_name("x" * 300 + ".pdf")
        self.assertEqual((len(long), long[-4:]), (255, ".pdf"))
        attachment = svc.add_attachments(self.coach.user, self.event, [upload("../../my schedule (v2).pdf")])[0]
        self.assertEqual(attachment.name, "my schedule (v2).pdf")
        stored = os.path.realpath(self.path(attachment))
        self.assertTrue(stored.startswith(os.path.realpath(self._tmp) + os.sep))
        self.assertNotIn(" ", os.path.basename(attachment.file.name))

    def test_per_event_limit(self):
        with mock.patch.object(svc, "MAX_ATTACHMENTS_PER_EVENT", 2):
            svc.add_attachments(self.coach.user, self.event, [upload("a.pdf")])
            with self.assertRaises(Invalid):
                svc.add_attachments(self.coach.user, self.event, [upload("b.pdf"), upload("c.pdf")])
            svc.add_attachments(self.coach.user, self.event, [upload("b.pdf")])
        self.assertEqual(self.event.attachments.count(), 2)
        self.assertEqual(len(self.files_on_disk()), 2)

    def test_failed_save_removes_written_files(self):
        original = EventAttachment.save
        calls = {"n": 0}

        def flaky(instance, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("db down")
            return original(instance, *args, **kwargs)

        with mock.patch.object(EventAttachment, "save", flaky), self.assertRaises(RuntimeError):
            svc.add_attachments(self.coach.user, self.event, [upload("a.pdf"), upload("b.pdf")])
        self.assertEqual(self.files_on_disk(), [])

    def test_delete_removes_the_file_after_commit(self):
        attachment = svc.add_attachments(self.coach.user, self.event, [upload()])[0]
        path = self.path(attachment)
        with self.captureOnCommitCallbacks(execute=True):
            svc.delete_attachment(self.coach.user, attachment)
        self.assertFalse(EventAttachment.objects.exists())
        self.assertFalse(os.path.exists(path))
        entry = AuditEntry.objects.get(action="event.attachment_deleted")
        self.assertEqual(entry.changes["name"], {"from": "schedule.pdf", "to": None})

    def test_deleting_the_event_removes_its_files(self):
        path = self.path(svc.add_attachments(self.owner, self.event, [upload()])[0])
        with self.captureOnCommitCallbacks(execute=True):
            self.event.delete()
        self.assertFalse(os.path.exists(path))
