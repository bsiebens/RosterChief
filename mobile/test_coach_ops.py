"""Coach operations screens, inline (htmx) behaviour: attendance check-ins and the edit sheet,
line-up picks and publish, squad staff removal, add-player suggestions, team news and the
evaluation form's unsupported-field guard."""

import datetime

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from club.models import ClubMembership, Season
from events.models import Attendance, Event, Lineup, LineupSelection
from members.models import Member
from news.models import News
from notifications.models import Notification
from rosterchief.test_support import start_auto_mfa_enrolment, stop_auto_mfa_enrolment
from teams.models import Position, StaffAssignment, Team, TeamMembership

from .test_support_coach import make_club

User = get_user_model()
HOST = "ajax-united.rosterchief.app"
HTMX = {"HTTP_HX_REQUEST": "true"}
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"


def setUpModule():
    start_auto_mfa_enrolment()


def tearDownModule():
    stop_auto_mfa_enrolment()


class CoachOpsBase(TestCase):
    event_kind = Event.EventKind.TRAINING

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.manager_position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.manager_position)
        cls.player = Member.objects.create(first_name="Anna", last_name="Player")
        TeamMembership.objects.create(team=cls.team, member=cls.player, season=cls.season, jersey_number=9)
        cls.event = Event.objects.create(club=cls.club, title="Big game", kind=cls.event_kind, start=timezone.now() + datetime.timedelta(days=2))
        cls.event.teams.add(cls.team)
        cls.attendance, _created = Attendance.objects.update_or_create(event=cls.event, member=cls.player, defaults={"status": Attendance.AttendanceStatus.PRESENT})

    def eligible(self, first_name, last_name, **kwargs):
        member = Member.objects.create(first_name=first_name, last_name=last_name, **kwargs)
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE, kind=ClubMembership.Kind.MEMBER)
        return member

    def physio(self):
        position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        member = Member.objects.create(first_name="Pat", last_name="Physio", user=user)
        StaffAssignment.objects.create(team=self.team, member=member, season=self.season, position=position)
        return user


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class AttendanceInlineTests(CoachOpsBase):
    def check_in_url(self):
        return reverse("mobile:coach_attendance_check_in", kwargs={"event_id": self.event.pk, "attendance_id": self.attendance.pk})

    def edit_url(self):
        return reverse("mobile:coach_attendance_edit", kwargs={"event_id": self.event.pk, "attendance_id": self.attendance.pk})

    def test_check_in_returns_the_partial_and_records_it(self):
        self.client.force_login(self.user)

        response = self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST, **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mobile/coach/_attendance_body.html")
        self.assertNotContains(response, "<html")
        self.assertContains(response, 'id="inline-status"')
        self.attendance.refresh_from_db()
        self.assertTrue(self.attendance.showed_up)
        self.assertContains(response, 'aria-pressed="true"')

    def test_posting_the_same_value_again_clears_the_check_in(self):
        self.client.force_login(self.user)
        self.client.post(self.check_in_url(), {"showed_up": "false"}, HTTP_HOST=HOST, **HTMX)

        self.client.post(self.check_in_url(), {"showed_up": "false"}, HTTP_HOST=HOST, **HTMX)

        self.attendance.refresh_from_db()
        self.assertIsNone(self.attendance.showed_up)

    def test_non_inline_check_in_redirects(self):
        self.client.force_login(self.user)

        response = self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST)

        self.assertRedirects(response, reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk}), fetch_redirect_response=False)

    def test_a_bad_value_is_rejected(self):
        self.client.force_login(self.user)

        response = self.client.post(self.check_in_url(), {"showed_up": "maybe"}, HTTP_HOST=HOST, **HTMX)

        self.assertEqual(response.status_code, 400)

    def test_non_managers_cannot_check_in_or_edit(self):
        self.client.force_login(self.physio())

        self.assertEqual(self.client.post(self.check_in_url(), {"showed_up": "true"}, HTTP_HOST=HOST, **HTMX).status_code, 403)
        self.assertEqual(self.client.post(self.edit_url(), {"status": "absent"}, HTTP_HOST=HOST, **HTMX).status_code, 403)
        self.attendance.refresh_from_db()
        self.assertIsNone(self.attendance.showed_up)
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.PRESENT)

    def test_edit_sets_the_answer_and_the_private_note(self):
        self.client.force_login(self.user)

        response = self.client.post(self.edit_url() + "?filter=all", {"status": "absent", "coach_note": "Injured knee"}, HTTP_HOST=HOST, **HTMX)

        self.assertEqual(response.status_code, 200)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.ABSENT)
        self.assertEqual(self.attendance.coach_note, "Injured knee")
        self.assertTrue(self.attendance.answered_by_coach)
        self.assertContains(response, "Set by coach")

    def test_the_private_note_never_reaches_the_toast_or_the_player(self):
        self.client.force_login(self.user)

        response = self.client.post(self.edit_url(), {"status": "absent", "coach_note": "SECRET-NOTE", "notify": "on"}, HTTP_HOST=HOST, **HTMX)

        toast = response.content.decode().split('id="inline-toast"')[1]
        self.assertNotIn("SECRET-NOTE", toast)
        self.assertFalse(Notification.objects.filter(member=self.player, body__contains="SECRET-NOTE").exists())

    def test_responses_are_not_cacheable(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk}), HTTP_HOST=HOST)

        self.assertIn("no-store", response["Cache-Control"])

    def test_the_note_is_only_shown_to_managers(self):
        self.attendance.coach_note = "SECRET-NOTE"
        self.attendance.save()
        url = reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk})
        self.client.force_login(self.user)
        self.assertContains(self.client.get(url, HTTP_HOST=HOST), "SECRET-NOTE")

        self.client.force_login(self.physio())

        self.assertNotContains(self.client.get(url, HTTP_HOST=HOST), "SECRET-NOTE")

    def test_a_note_over_the_limit_is_refused_with_an_error_toast(self):
        self.client.force_login(self.user)

        response = self.client.post(self.edit_url(), {"status": "present", "coach_note": "x" * 501}, HTTP_HOST=HOST, **HTMX)

        self.assertContains(response, "too long")
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.coach_note, "")

    def test_reset_clears_the_answer(self):
        self.client.force_login(self.user)

        self.client.post(self.edit_url(), {"status": "no_response"}, HTTP_HOST=HOST, **HTMX)

        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.NO_RESPONSE)

    def test_a_cancelled_event_cannot_be_edited(self):
        self.event.cancelled = True
        self.event.save()
        self.client.force_login(self.user)

        response = self.client.post(self.edit_url(), {"status": "absent"}, HTTP_HOST=HOST, **HTMX)

        self.assertContains(response, "cancelled")
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.PRESENT)

    def test_marking_a_selected_player_out_offers_the_lineup_shortcut(self):
        self.attendance.status = Attendance.AttendanceStatus.SELECTED
        self.attendance.save()
        self.client.force_login(self.user)

        response = self.client.post(self.edit_url(), {"status": "absent"}, HTTP_HOST=HOST, **HTMX)

        self.assertContains(response, "still in the published line-up")

    def test_remind_inline_returns_the_partial_with_a_toast(self):
        silent = Member.objects.create(first_name="Ben", last_name="Silent")
        TeamMembership.objects.create(team=self.team, member=silent, season=self.season)
        Attendance.objects.update_or_create(event=self.event, member=silent, defaults={"status": Attendance.AttendanceStatus.NO_RESPONSE})
        self.client.force_login(self.user)

        response = self.client.post(reverse("mobile:coach_attendance_remind_silent", kwargs={"event_id": self.event.pk}), HTTP_HOST=HOST, **HTMX)

        self.assertTemplateUsed(response, "mobile/coach/_attendance_body.html")
        self.assertContains(response, "Reminder sent")
        self.assertTrue(Notification.objects.filter(member=silent).exists())

    def test_the_remind_button_shows_the_count(self):
        silent = Member.objects.create(first_name="Ben", last_name="Silent")
        Attendance.objects.update_or_create(event=self.event, member=silent, defaults={"status": Attendance.AttendanceStatus.NO_RESPONSE})
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk}), HTTP_HOST=HOST)

        self.assertContains(response, "Remind 1 player")

    def test_the_all_filter_lists_everyone(self):
        silent = Member.objects.create(first_name="Ben", last_name="Silent")
        Attendance.objects.update_or_create(event=self.event, member=silent, defaults={"status": Attendance.AttendanceStatus.NO_RESPONSE})
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk}) + "?filter=all", HTTP_HOST=HOST)

        self.assertEqual(len(response.context["rows"]), 2)

    def test_showed_up_buttons_are_labelled_toggles(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk}), HTTP_HOST=HOST)

        self.assertContains(response, "Showed up: Anna Player")
        self.assertContains(response, "aria-pressed")
        self.assertContains(response, 'aria-live="polite"')


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class LineupInlineTests(CoachOpsBase):
    event_kind = Event.EventKind.GAME

    def toggle_url(self, member=None):
        return reverse("mobile:coach_lineup_toggle", kwargs={"event_id": self.event.pk, "member_id": (member or self.player).pk})

    def publish_url(self):
        return reverse("mobile:coach_lineup_publish", kwargs={"event_id": self.event.pk})

    def test_a_tap_selects_and_saves_inline(self):
        self.client.force_login(self.user)

        response = self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)

        self.assertTemplateUsed(response, "mobile/coach/_lineup_body.html")
        self.assertTrue(LineupSelection.objects.filter(lineup__event=self.event, member=self.player).exists())
        self.assertContains(response, "Nobody has been told yet")
        self.assertContains(response, "1 will hear they're in")

    def test_the_tap_is_idempotent(self):
        self.client.force_login(self.user)
        self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)

        self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)

        self.assertEqual(LineupSelection.objects.filter(lineup__event=self.event).count(), 1)

    def test_a_second_tap_deselects(self):
        self.client.force_login(self.user)
        self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)

        self.client.post(self.toggle_url(), {"selected": "false"}, HTTP_HOST=HOST, **HTMX)

        self.assertFalse(LineupSelection.objects.filter(lineup__event=self.event).exists())

    def test_an_unavailable_player_cannot_be_picked(self):
        self.attendance.status = Attendance.AttendanceStatus.ABSENT
        self.attendance.save()
        self.client.force_login(self.user)

        response = self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)

        self.assertEqual(response.status_code, 404)

    def test_non_managers_cannot_toggle(self):
        self.client.force_login(self.physio())

        response = self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)

        self.assertEqual(response.status_code, 403)

    def test_publish_inline_shows_a_success_toast_with_the_count(self):
        self.client.force_login(self.user)
        self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)

        response = self.client.post(self.publish_url(), HTTP_HOST=HOST, **HTMX)

        self.assertTemplateUsed(response, "mobile/coach/_lineup_body.html")
        self.assertContains(response, "1 player was told.")
        self.assertIsNotNone(Lineup.objects.get(event=self.event).published_at)

    def test_publish_inline_refuses_an_empty_lineup(self):
        self.client.force_login(self.user)
        Lineup.objects.create(event=self.event, team=self.team)

        response = self.client.post(self.publish_url(), HTTP_HOST=HOST, **HTMX)

        self.assertContains(response, "Pick at least one player first.")
        self.assertIsNone(Lineup.objects.get(event=self.event).published_at)

    def test_schedule_inline_shows_the_scheduled_time(self):
        self.client.force_login(self.user)
        self.client.post(self.toggle_url(), {"selected": "true"}, HTTP_HOST=HOST, **HTMX)
        when = (timezone.localtime() + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

        response = self.client.post(self.publish_url(), {"action": "schedule", "publish_at": when}, HTTP_HOST=HOST, **HTMX)

        self.assertContains(response, "Publish scheduled")
        self.assertContains(response, "Cancel schedule")
        self.assertIsNotNone(Lineup.objects.get(event=self.event).scheduled_publish_at)

    def test_the_toggle_rows_are_pressable_buttons(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), HTTP_HOST=HOST)

        self.assertContains(response, 'aria-pressed="false"')
        self.assertContains(response, 'id="lineup-publish-sheet"')


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class SquadInlineTests(CoachOpsBase):
    def test_removing_staff_inline_returns_the_squad_partial(self):
        other = Member.objects.create(first_name="Ass", last_name="Istant")
        assistant = StaffAssignment.objects.create(team=self.team, member=other, season=self.season, position=self.manager_position)
        self.client.force_login(self.user)

        response = self.client.post(reverse("mobile:coach_staff_remove", kwargs={"assignment_pk": assistant.pk}), HTTP_HOST=HOST, **HTMX)

        self.assertTemplateUsed(response, "mobile/coach/_squad_body.html")
        self.assertContains(response, "Staff removed")
        self.assertFalse(StaffAssignment.objects.filter(pk=assistant.pk).exists())

    def test_removing_yourself_inline_is_a_toast_not_a_removal(self):
        own = StaffAssignment.objects.get(member=self.member)
        self.client.force_login(self.user)

        response = self.client.post(reverse("mobile:coach_staff_remove", kwargs={"assignment_pk": own.pk}), HTTP_HOST=HOST, **HTMX)

        self.assertContains(response, "You can&#x27;t remove yourself from the staff.")
        self.assertTrue(StaffAssignment.objects.filter(pk=own.pk).exists())

    def test_the_player_sheet_offers_edit_and_evaluations_only_when_allowed(self):
        membership = TeamMembership.objects.get(member=self.player)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_squad"), HTTP_HOST=HOST)

        self.assertContains(response, "Edit player")
        self.assertContains(response, reverse("mobile:coach_roster_member", kwargs={"membership_pk": membership.pk}))
        self.assertNotContains(response, reverse("mobile:coach_evaluation_history", kwargs={"player_pk": self.player.pk}))

    def test_a_pending_signup_is_flagged(self):
        from club.models import ClubMembership

        ClubMembership.objects.create(club=self.club, member=self.player, season=self.season, status=ClubMembership.StatusChoices.PENDING)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_squad"), HTTP_HOST=HOST)

        self.assertContains(response, "Sign-up waiting for approval")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class AddPlayerSuggestionTests(CoachOpsBase):
    def test_age_match_and_previous_team_reasons_are_labelled(self):
        self.team.age_min = 15
        self.team.age_max = 16
        self.team.save()
        year = self.season.start_date.year
        young = self.eligible("Yara", "Young", date_of_birth=datetime.date(year - 15, 6, 1))
        near = self.eligible("Nina", "Near", date_of_birth=datetime.date(year - 14, 6, 1))
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?filter=suggested", HTTP_HOST=HOST)

        self.assertEqual([member.pk for member in response.context["candidates"]], [young.pk, near.pk])
        self.assertContains(response, "Age match")
        self.assertContains(response, "Close in age")

    def test_without_an_age_band_nobody_is_suggested_by_age(self):
        self.eligible("Yara", "Young", date_of_birth=datetime.date(2010, 6, 1))
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?filter=suggested", HTTP_HOST=HOST)

        self.assertEqual(response.context["candidates"], [])

    def test_search_narrows_the_staff_candidates(self):
        self.eligible("Zara", "Zed")
        self.eligible("Other", "Person")
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_staff") + "?q=zar", HTTP_HOST=HOST)

        self.assertEqual([member.first_name for member in response.context["candidates"]], ["Zara"])

    def test_candidates_show_birth_year_and_current_team(self):
        other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        kid = self.eligible("Kid", "Kay", date_of_birth=datetime.date(2011, 1, 1))
        TeamMembership.objects.create(team=other_team, member=kid, season=self.season)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player"), HTTP_HOST=HOST)

        self.assertContains(response, "2011")
        self.assertContains(response, "U14")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class TeamNewsTests(CoachOpsBase):
    def make_post(self, **kwargs):
        post = News.objects.create(club=self.club, title=kwargs.pop("title", "Post"), body="Body", created_by=self.member, **kwargs)
        post.teams.add(self.team)
        return post

    def test_the_list_shows_only_my_posts_with_their_status(self):
        self.make_post(title="Pending one", status=News.Status.PENDING_REVIEW)
        self.make_post(title="Live one", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(days=1))
        self.make_post(title="Future one", status=News.Status.PUBLISHED, published_at=timezone.now() + datetime.timedelta(days=1))
        self.make_post(title="Draft one", status=News.Status.DRAFT)
        other = News.objects.create(club=self.club, title="Someone else", body="x", status=News.Status.PENDING_REVIEW, created_by=self.player)
        other.teams.add(self.team)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_news"), HTTP_HOST=HOST)

        self.assertEqual([post.status_key for post in response.context["posts"]], ["draft", "scheduled", "published", "pending_review"])
        self.assertContains(response, "Waiting for review")
        self.assertContains(response, "Not published, ask the editor")
        self.assertNotContains(response, "Someone else")
        self.assertEqual(response.context["active_tab"], "coach_news")

    def test_the_empty_state_and_share_button(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_news"), HTTP_HOST=HOST)

        self.assertContains(response, "No posts yet")
        self.assertContains(response, reverse("mobile:coach_create_news") + "?next=news")

    def test_a_physio_has_no_share_button(self):
        self.client.force_login(self.physio())

        response = self.client.get(reverse("mobile:coach_news"), HTTP_HOST=HOST)

        self.assertNotContains(response, "?next=news")

    def test_sending_from_the_news_tab_returns_to_the_list(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mobile:coach_create_news"),
            {"title": "Hello", "body": "World", "teams": [str(self.team.pk)], "next": "news"},
            HTTP_HOST=HOST,
        )

        self.assertRedirects(response, reverse("mobile:coach_news"), fetch_redirect_response=False)

    def test_more_than_ten_photos_are_refused(self):
        self.client.force_login(self.user)
        photos = [SimpleUploadedFile(f"p{i}.png", PNG, content_type="image/png") for i in range(11)]

        response = self.client.post(
            reverse("mobile:coach_create_news"),
            {"title": "Hello", "body": "World", "teams": [str(self.team.pk)], "images": photos},
            HTTP_HOST=HOST,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(News.objects.filter(title="Hello").exists())

    def test_the_compose_form_has_a_photo_picker(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_news"), HTTP_HOST=HOST)

        self.assertContains(response, 'name="images"')
        self.assertContains(response, "Add photos")
        self.assertContains(response, "Send for review")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class EvaluationGateTests(CoachOpsBase):
    def test_the_flag_off_is_a_404(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_evaluation_history", kwargs={"player_pk": self.player.pk}), HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 404)
