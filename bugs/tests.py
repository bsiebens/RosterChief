from django.core import mail
from django.test import TestCase

from authentication.models import User
from club.models import Club
from members.models import Member

from .models import BugReport
from .services import add_note, file_report, update_bug


class BugReportTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United")
        cls.member = Member.objects.create(first_name="Jane", last_name="Doe")

    def file(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("reported_by", self.member)
        kwargs.setdefault("title", "Calendar crashes")
        kwargs.setdefault("description", "Tapping a date closes the app.")
        return file_report(**kwargs)


class FileReportTests(BugReportTestBase):
    def test_filing_a_report_records_the_reporter_and_club(self):
        bug = self.file()

        self.assertEqual(bug.reported_by, self.member)
        self.assertEqual(bug.club, self.club)
        self.assertEqual(bug.status, BugReport.Status.SUBMITTED)
        self.assertEqual(bug.priority, BugReport.Priority.MEDIUM)

    def test_filing_a_report_emails_platform_admins(self):
        User.objects.create_user(email="staff@example.com", password="pw-secret-123", is_staff=True)

        self.file(title="Calendar crashes")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("staff@example.com", mail.outbox[0].to)
        self.assertIn("Ajax United", mail.outbox[0].subject)
        self.assertIn("Calendar crashes", mail.outbox[0].body)

    def test_no_email_when_there_are_no_platform_admins(self):
        self.file()

        self.assertEqual(len(mail.outbox), 0)


class UpdateBugTests(BugReportTestBase):
    def test_marking_fixed_stamps_fixed_at(self):
        bug = self.file()

        update_bug(bug, status=BugReport.Status.FIXED, priority=BugReport.Priority.HIGH, fixed_version="2026.9.1")

        bug.refresh_from_db()
        self.assertEqual(bug.status, BugReport.Status.FIXED)
        self.assertEqual(bug.priority, BugReport.Priority.HIGH)
        self.assertEqual(bug.fixed_version, "2026.9.1")
        self.assertIsNotNone(bug.fixed_at)

    def test_reopening_a_fixed_bug_clears_fixed_at(self):
        bug = self.file()
        update_bug(bug, status=BugReport.Status.FIXED, priority=BugReport.Priority.MEDIUM, fixed_version="2026.9.1")

        update_bug(bug, status=BugReport.Status.IN_PROGRESS, priority=BugReport.Priority.MEDIUM, fixed_version="2026.9.1")

        bug.refresh_from_db()
        self.assertIsNone(bug.fixed_at)

    def test_re_fixing_does_not_move_the_original_fixed_at(self):
        bug = self.file()
        update_bug(bug, status=BugReport.Status.FIXED, priority=BugReport.Priority.MEDIUM, fixed_version="2026.9.1")
        bug.refresh_from_db()
        first_fixed_at = bug.fixed_at

        update_bug(bug, status=BugReport.Status.FIXED, priority=BugReport.Priority.MEDIUM, fixed_version="2026.9.2")

        bug.refresh_from_db()
        self.assertEqual(bug.fixed_at, first_fixed_at)
        self.assertEqual(bug.fixed_version, "2026.9.2")


class AddNoteTests(BugReportTestBase):
    def test_adding_a_note_attaches_it_to_the_bug(self):
        bug = self.file()
        admin_user = User.objects.create_user(email="staff@example.com", password="pw-secret-123", is_staff=True)

        note = add_note(bug, author=admin_user, body="Reproduced on Android.")

        self.assertEqual(note.bug, bug)
        self.assertEqual(note.author, admin_user)
        self.assertEqual(list(bug.notes.all()), [note])
