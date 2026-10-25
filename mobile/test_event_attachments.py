import datetime
import os
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import User
from club.models import ClubMembership, Season
from events.models import Attendance, Event, EventAttachment
from members.models import Family, FamilyMembership, Member
from rosterchief.test_support import start_auto_mfa_enrolment, stop_auto_mfa_enrolment
from teams.models import Position, StaffAssignment, Team

from .tests import make_club

HOST = "ajax-united.rosterchief.app"


def setUpModule():
    # Management-position staff require MFA (authentication.middleware); model ones already enrolled.
    start_auto_mfa_enrolment()


def tearDownModule():
    stop_auto_mfa_enrolment()


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class EventAttachmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        manager_position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        physio_position = Position.objects.create(club=cls.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)

        cls.manager_user = User.objects.create_user(email="att-manager@example.com", password="pw-secret-123")
        manager = Member.objects.create(first_name="Mia", last_name="Manager", user=cls.manager_user)
        StaffAssignment.objects.create(team=cls.team, member=manager, season=cls.season, position=manager_position)

        cls.physio_user = User.objects.create_user(email="att-physio@example.com", password="pw-secret-123")
        physio = Member.objects.create(first_name="Pat", last_name="Physio", user=cls.physio_user)
        StaffAssignment.objects.create(team=cls.team, member=physio, season=cls.season, position=physio_position)

        cls.parent_user = User.objects.create_user(email="att-parent@example.com", password="pw-secret-123")
        parent = Member.objects.create(first_name="Pim", last_name="Parent", user=cls.parent_user)
        child = Member.objects.create(first_name="Noor", last_name="Parent")
        family = Family.objects.create(club=cls.club, name="Parent")
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        cls.child = child
        ClubMembership.objects.create(club=cls.club, member=child, season=cls.season)

        cls.stranger_user = User.objects.create_user(email="att-stranger@example.com", password="pw-secret-123")
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger", user=cls.stranger_user)
        stranger_family = Family.objects.create(club=cls.club, name="Stranger")
        FamilyMembership.objects.create(family=stranger_family, member=stranger, role=FamilyMembership.FamilyRole.PARENT)
        ClubMembership.objects.create(club=cls.club, member=stranger, season=cls.season)

    def setUp(self):
        self.event = Event.objects.create(club=self.club, title="Tournament", kind=Event.EventKind.TOURNAMENT, start=timezone.now() + datetime.timedelta(days=3))
        self.event.teams.add(self.team)
        Attendance.objects.get_or_create(event=self.event, member=self.child)

    def _upload(self, user, name="schedule.pdf", content=b"%PDF-1.4 hi", url_name="mobile:coach_event_attachment_upload"):
        self.client.force_login(user)
        return self.client.post(reverse(url_name, args=[self.event.pk]), {"files": [SimpleUploadedFile(name, content)]}, HTTP_HOST=HOST)

    def _attach(self, name="schedule.pdf"):
        return EventAttachment.objects.create(event=self.event, file=SimpleUploadedFile(name, b"%PDF-1.4 hi"), name=name, size=11)

    def test_manager_can_upload_several_files(self):
        self.client.force_login(self.manager_user)
        files = [SimpleUploadedFile("a.pdf", b"a"), SimpleUploadedFile("b.png", b"b")]
        response = self.client.post(reverse("mobile:coach_event_attachment_upload", args=[self.event.pk]), {"files": files}, HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(sorted(self.event.attachments.values_list("name", flat=True)), ["a.pdf", "b.png"])

    def test_non_managing_staff_cannot_upload(self):
        response = self._upload(self.physio_user)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.event.attachments.exists())

    def test_parent_cannot_upload(self):
        response = self._upload(self.parent_user)
        self.assertEqual(response.status_code, 404)  # not on any team's staff: Coach mode doesn't exist for them
        self.assertFalse(self.event.attachments.exists())

    def test_disallowed_extension_is_refused(self):
        self._upload(self.manager_user, name="evil.html", content=b"<script>")
        self.assertFalse(self.event.attachments.exists())

    def test_oversized_file_is_refused(self):
        with mock.patch("events.services.attachments.MAX_ATTACHMENT_BYTES", 5):
            self._upload(self.manager_user, content=b"123456")
        self.assertFalse(self.event.attachments.exists())

    def test_parent_of_an_invited_child_sees_and_downloads(self):
        attachment = self._attach()
        self.client.force_login(self.parent_user)

        page = self.client.get(reverse("mobile:event_detail", args=[self.event.pk]), HTTP_HOST=HOST)
        download = self.client.get(reverse("mobile:event_attachment", args=[self.event.pk, attachment.pk]), HTTP_HOST=HOST)

        self.assertContains(page, "schedule.pdf")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertEqual(b"".join(download.streaming_content), b"%PDF-1.4 hi")

    def test_uninvited_account_neither_sees_nor_downloads(self):
        attachment = self._attach()
        self.client.force_login(self.stranger_user)

        page = self.client.get(reverse("mobile:event_detail", args=[self.event.pk]), HTTP_HOST=HOST)
        download = self.client.get(reverse("mobile:event_attachment", args=[self.event.pk, attachment.pk]), HTTP_HOST=HOST)

        self.assertNotContains(page, "schedule.pdf")
        self.assertEqual(download.status_code, 404)

    def test_staff_can_download(self):
        attachment = self._attach()
        self.client.force_login(self.physio_user)
        response = self.client.get(reverse("mobile:event_attachment", args=[self.event.pk, attachment.pk]), HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)

    def test_manager_deletes_and_file_is_removed(self):
        attachment = self._attach()
        path = attachment.file.path
        self.client.force_login(self.manager_user)
        response = self.client.post(reverse("mobile:coach_event_attachment_delete", args=[self.event.pk, attachment.pk]), HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EventAttachment.objects.filter(pk=attachment.pk).exists())
        self.assertFalse(os.path.exists(path))

    def test_physio_cannot_delete(self):
        attachment = self._attach()
        self.client.force_login(self.physio_user)
        response = self.client.post(reverse("mobile:coach_event_attachment_delete", args=[self.event.pk, attachment.pk]), HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(EventAttachment.objects.filter(pk=attachment.pk).exists())

    def test_deleting_the_event_removes_the_files(self):
        path = self._attach().file.path
        self.event.delete()
        self.assertFalse(os.path.exists(path))

    def test_coach_page_shows_upload_only_to_managers(self):
        self._attach()
        upload_url = reverse("mobile:coach_event_attachment_upload", args=[self.event.pk])
        self.client.force_login(self.manager_user)
        self.assertContains(self.client.get(reverse("mobile:coach_event_detail", args=[self.event.pk]), HTTP_HOST=HOST), upload_url)
        self.client.force_login(self.physio_user)
        page = self.client.get(reverse("mobile:coach_event_detail", args=[self.event.pk]), HTTP_HOST=HOST)
        self.assertContains(page, "schedule.pdf")
        self.assertNotContains(page, upload_url)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class ManagementEventAttachmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.other_team = Team.objects.create(club=cls.club, name="U12", short_name="U12")
        position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        cls.manager_user = User.objects.create_user(email="mgmt-att-manager@example.com", password="pw-secret-123")
        StaffAssignment.objects.create(team=cls.team, member=Member.objects.create(first_name="Mia", last_name="Manager", user=cls.manager_user), season=cls.season, position=position)

    def setUp(self):
        self.event = Event.objects.create(club=self.club, title="Tournament", kind=Event.EventKind.TOURNAMENT, start=timezone.now() + datetime.timedelta(days=3))
        self.event.teams.add(self.team)

    def test_team_manager_uploads_downloads_and_deletes(self):
        self.client.force_login(self.manager_user)
        self.client.post(reverse("management:event_attachment_upload", args=[self.event.pk]), {"files": [SimpleUploadedFile("map.pdf", b"x")]}, HTTP_HOST=HOST)
        attachment = self.event.attachments.get()

        page = self.client.get(reverse("management:event_detail", args=[self.event.pk]), HTTP_HOST=HOST)
        download = self.client.get(reverse("management:event_attachment_download", args=[self.event.pk, attachment.pk]), HTTP_HOST=HOST)
        self.client.post(reverse("management:event_attachment_delete", args=[self.event.pk, attachment.pk]), HTTP_HOST=HOST)

        self.assertContains(page, "map.pdf")
        self.assertEqual(download.status_code, 200)
        self.assertFalse(self.event.attachments.exists())

    def test_manager_of_another_team_cannot_upload(self):
        other_event = Event.objects.create(club=self.club, title="U12 game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=3))
        other_event.teams.add(self.other_team)
        self.client.force_login(self.manager_user)

        response = self.client.post(reverse("management:event_attachment_upload", args=[other_event.pk]), {"files": [SimpleUploadedFile("map.pdf", b"x")]}, HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(other_event.attachments.exists())
