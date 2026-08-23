import datetime
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from icalendar import Calendar as ICalCalendar

from club.models import Club, ClubMembership, DuesInvoice, MemberRequirementStatus, OnboardingRequirement, Season, Sponsor
from events.models import Attendance, Competition, Event, EventReferee, EventSeries, Lineup, LineupSelection, Location, Opponent, RefereeSignup
from events.services.attendance import record_check_in
from events.services.calendar import week_bounds
from members.models import Family, FamilyMembership, Member
from news.models import News
from notifications.models import Notification
from teams.models import Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership

from .coach_views import CoachTodayView
from .models import CalendarFeedToken, PushSubscription
from .services.icons import render_fallback_icon

User = get_user_model()

ONE_PIXEL_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"


def make_image_file(name="photo.png"):
    return SimpleUploadedFile(name, ONE_PIXEL_PNG, content_type="image/png")


def make_club(**kwargs):
    return Club.objects.create(name="Ajax United", slug="ajax-united", secondary_color="#e4002b", **kwargs)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class MobileShellTests(TestCase):
    """The app shell (base.html) plus the PWA plumbing every screen sits on."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        Season.objects.create(club=cls.club, start_date=datetime.date(2025, 8, 1), end_date=datetime.date(2026, 5, 31))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=Season.objects.first())

    def _get(self, url_name, **kwargs):
        return self.client.get(reverse(f"mobile:{url_name}", kwargs=kwargs), HTTP_HOST="ajax-united.rosterchief.app")

    def test_home_requires_login(self):
        response = self._get("home")
        self.assertEqual(response.status_code, 302)

    def test_home_renders_the_app_shell_when_signed_in(self):
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.club.name)
        self.assertContains(response, 'rel="manifest"')

    def test_header_crest_falls_back_to_round_initials_without_a_logo(self):
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertContains(response, 'class="app-crest app-crest-fallback"')
        self.assertContains(response, self.club.initials)

    def test_manifest_names_the_club_and_points_at_its_icon(self):
        response = self._get("manifest")

        self.assertEqual(response["Content-Type"], "application/manifest+json")
        self.assertEqual(response.json()["name"], self.club.name)
        self.assertIn("icon", response.json()["icons"][0]["src"])

    def test_icon_falls_back_to_a_rendered_png_without_a_logo(self):
        response = self._get("icon", size=192)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_service_worker_is_served_at_the_app_scope(self):
        response = self.client.get("/app/sw.js", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response["Content-Type"])
        self.assertIn("addEventListener", response.content.decode())

    def test_mode_switcher_shows_for_an_account_with_a_staff_assignment(self):
        today = timezone.localdate()
        current_season = Season.objects.create(club=self.club, start_date=today - datetime.timedelta(days=10), end_date=today + datetime.timedelta(days=300))
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        position = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=current_season, position=position)
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertContains(response, reverse("mobile:coach_today"))

    def test_mode_switcher_hidden_without_a_staff_assignment(self):
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertNotContains(response, reverse("mobile:coach_today"))

    def test_person_switcher_lists_managed_children_alongside_me(self):
        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=Season.objects.first())
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertContains(response, "Noor")

    def test_scope_person_switches_via_the_as_query_param(self):
        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=Season.objects.first())
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:home") + f"?as={child.pk}", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.context["scope_person"], child)

    def test_bottom_tab_bar_highlights_the_active_screen(self):
        self.client.force_login(self.user)

        home_response = self._get("home")
        self.assertContains(home_response, 'class="tab-bar-item tab-bar-item-active"', count=1)
        self.assertEqual(home_response.context["active_tab"], "home")

        me_response = self.client.get(reverse("mobile:me"), HTTP_HOST="ajax-united.rosterchief.app")
        self.assertContains(me_response, 'class="tab-bar-item tab-bar-item-active"', count=1)
        self.assertEqual(me_response.context["active_tab"], "me")

    def test_all_chip_only_appears_once_theres_more_than_one_managed_person(self):
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertNotContains(response, 'href="?as=all"')

    def test_all_chip_appears_and_is_selected_by_default_with_a_child(self):
        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=Season.objects.first())
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertContains(response, 'href="?as=all"')
        self.assertTrue(response.context["scope_everyone"])


@override_settings(
    VAPID_PRIVATE_KEY="",
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"],
)
class PushSubscribeViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", user=cls.user)

    def test_subscribing_creates_a_push_subscription_row(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mobile:push_subscribe"),
            data={"endpoint": "https://push.example.com/abc", "keys": {"p256dh": "key1", "auth": "key2"}},
            content_type="application/json",
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(PushSubscription.objects.filter(member=self.member, endpoint="https://push.example.com/abc").exists())

    def test_resubscribing_the_same_endpoint_updates_rather_than_duplicates(self):
        self.client.force_login(self.user)
        PushSubscription.objects.create(club=self.club, member=self.member, endpoint="https://push.example.com/abc", p256dh="old", auth="old")

        self.client.post(
            reverse("mobile:push_subscribe"),
            data={"endpoint": "https://push.example.com/abc", "keys": {"p256dh": "new", "auth": "new"}},
            content_type="application/json",
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertEqual(PushSubscription.objects.filter(endpoint="https://push.example.com/abc").count(), 1)
        self.assertEqual(PushSubscription.objects.get(endpoint="https://push.example.com/abc").p256dh, "new")

    def test_anonymous_cannot_subscribe(self):
        response = self.client.post(
            reverse("mobile:push_subscribe"),
            data={"endpoint": "https://push.example.com/abc", "keys": {"p256dh": "key1", "auth": "key2"}},
            content_type="application/json",
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertEqual(response.status_code, 302)


class RenderFallbackIconTests(TestCase):
    def test_renders_a_png_without_a_logo(self):
        club = make_club()

        png_bytes = render_fallback_icon(club, size=64)

        self.assertTrue(png_bytes.startswith(b"\x89PNG"))


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class HomeViewTests(TestCase):
    """M1 -- design_handoff_rosterchief_platform/README.md's M1 section."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)
        cls.future = timezone.now() + datetime.timedelta(days=7)

    def _get(self, url_name=None, url=None):
        url = url or reverse(f"mobile:{url_name}")
        return self.client.get(url, HTTP_HOST="ajax-united.rosterchief.app")

    def make_event(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("title", "Training")
        kwargs.setdefault("start", self.future)
        return Event.objects.create(**kwargs)

    def test_hero_shows_the_soonest_upcoming_event(self):
        soon = self.make_event(title="Away · Herentals", start=self.future)
        later = self.make_event(title="Practice · Ice 3", start=self.future + datetime.timedelta(days=5))
        Attendance.objects.create(event=soon, member=self.member)
        Attendance.objects.create(event=later, member=self.member)
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(response.context["hero_attendance"].event, soon)
        self.assertContains(response, "Away")

    def test_hero_shows_the_clubs_event_background_in_grayscale_when_set(self):
        event = self.make_event(title="Practice")
        Attendance.objects.create(event=event, member=self.member)
        self.club.event_background = make_image_file()
        self.club.save(update_fields=["event_background"])
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertContains(response, 'class="absolute inset-0 h-full w-full object-cover grayscale"')

    def test_hero_is_absent_when_no_upcoming_event(self):
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertIsNone(response.context["hero_attendance"])

    def test_needs_your_answer_only_lists_no_response_and_maybe(self):
        answered = self.make_event(title="Already answered", start=self.future)
        awaiting = self.make_event(title="Awaiting reply", start=self.future + datetime.timedelta(days=2))
        maybe = self.make_event(title="Maybe reply", start=self.future + datetime.timedelta(days=4))
        Attendance.objects.create(event=answered, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        Attendance.objects.create(event=awaiting, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        Attendance.objects.create(event=maybe, member=self.member, status=Attendance.AttendanceStatus.MAYBE)
        self.client.force_login(self.user)

        response = self._get("home")

        needs_answer_events = {attendance.event for attendance in response.context["needs_answer"]}
        self.assertEqual(needs_answer_events, {awaiting, maybe})

    def test_needs_your_answer_excludes_events_with_a_closed_registration_deadline(self):
        # A distinct, already-answered earlier event so it becomes the hero --
        # otherwise the closed-deadline event below would become the hero
        # itself (still shown there, just read-only) rather than reaching
        # needs_answer's own exclusion at all.
        hero_event = self.make_event(title="Soonest", start=self.future)
        Attendance.objects.create(event=hero_event, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        closed = self.make_event(title="Deadline passed", start=self.future + datetime.timedelta(days=2), deadline=timezone.now() - datetime.timedelta(hours=1))
        open_deadline = self.make_event(title="Deadline still open", start=self.future + datetime.timedelta(days=3), deadline=timezone.now() + datetime.timedelta(hours=1))
        Attendance.objects.create(event=closed, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        Attendance.objects.create(event=open_deadline, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._get("home")

        needs_answer_events = {attendance.event for attendance in response.context["needs_answer"]}
        self.assertEqual(needs_answer_events, {open_deadline})

    def test_needs_your_answer_is_capped_at_five_with_a_remaining_count(self):
        # A distinct, already-answered earlier event so it becomes the hero and
        # none of the seven "Practice N" events below get excluded as the hero.
        hero_event = self.make_event(title="Soonest", start=self.future)
        Attendance.objects.create(event=hero_event, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        for day in range(1, 8):
            event = self.make_event(title=f"Practice {day}", start=self.future + datetime.timedelta(days=day))
            Attendance.objects.create(event=event, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(len(response.context["needs_answer"]), 5)
        self.assertEqual(response.context["needs_answer_remaining"], 2)
        self.assertContains(response, "+2 more in Calendar")

    def test_needs_your_answer_excludes_the_hero_event_even_if_unanswered(self):
        soon = self.make_event(title="Soonest", start=self.future)
        Attendance.objects.create(event=soon, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(response.context["hero_attendance"].event, soon)
        self.assertEqual(list(response.context["needs_answer"]), [])

    def test_dues_card_shows_the_outstanding_balance(self):
        membership = ClubMembership.objects.get(club=self.club, member=self.member, season=self.season)
        membership.fee_amount = Decimal("420.00")
        membership.save(update_fields=["fee_amount"])
        DuesInvoice.objects.create(club=self.club, membership=membership, amount=Decimal("420.00"), due_date=timezone.localdate() + datetime.timedelta(days=10))
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(response.context["dues_rows"][0]["balance"], Decimal("420.00"))
        self.assertContains(response, "420")

    def test_dues_card_is_absent_when_nothing_is_owed(self):
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(response.context["dues_rows"], [])

    def test_news_teaser_shows_the_latest_published_items_newest_first(self):
        oldest = News.objects.create(club=self.club, title="Old news", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(days=5))
        latest = News.objects.create(club=self.club, title="Signed: New Player", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(days=1))
        News.objects.create(club=self.club, title="Still a draft", body="Body.", status=News.Status.DRAFT)
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(list(response.context["news_items"]), [latest, oldest])
        self.assertContains(response, "Signed: New Player")

    def test_news_teaser_caps_at_three_with_a_link_to_all_news(self):
        for day in range(5):
            News.objects.create(club=self.club, title=f"Item {day}", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(days=day))
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(len(response.context["news_items"]), 3)
        self.assertContains(response, 'href="/app/news/"')

    def test_news_teaser_excludes_external_only_items(self):
        News.objects.create(club=self.club, title="Public site only", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now(), visibility=News.Visibility.EXTERNAL)
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(list(response.context["news_items"]), [])

    def test_news_teaser_includes_a_managed_childs_team_news_even_when_scoped_to_me(self):
        # A parent with no team of their own, scoped to their own "Me" chip
        # (no team membership -> empty team_ids for `people`) should still see
        # news about a child's team -- news isn't a per-person action like
        # RSVP/dues, so it's keyed off every managed person, not just scope.
        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        position = Position.objects.create(club=self.club, name="Forward", short_name="F")
        TeamMembership.objects.create(team=team, member=child, season=self.season, position=position)
        team_news = News.objects.create(club=self.club, title="U12 news", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())
        team_news.teams.add(team)
        self.client.force_login(self.user)

        response = self._get(url=reverse("mobile:home") + f"?as={self.member.pk}")

        self.assertEqual(response.context["scope_person"], self.member)
        self.assertIn(team_news, response.context["news_items"])

    def test_news_card_shows_an_empty_state_with_a_link_to_all_news_when_there_is_none(self):
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(list(response.context["news_items"]), [])
        self.assertContains(response, "No news yet.")
        self.assertContains(response, 'href="/app/news/"')

    def test_empty_account_gets_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get("home")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["scope_person"])
        self.assertContains(response, "No one to show yet")

    def test_sponsors_shows_only_active_ones(self):
        today = timezone.localdate()
        active = Sponsor.objects.create(club=self.club, name="Active Co", start_date=today - datetime.timedelta(days=10))
        Sponsor.objects.create(club=self.club, name="Future Co", start_date=today + datetime.timedelta(days=10))
        Sponsor.objects.create(club=self.club, name="Past Co", start_date=today - datetime.timedelta(days=100), end_date=today - datetime.timedelta(days=1))
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(list(response.context["sponsors"]), [active])
        self.assertContains(response, "Active Co")
        self.assertNotContains(response, "Future Co")
        self.assertNotContains(response, "Past Co")

    def test_sponsors_are_absent_when_none_are_defined(self):
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(list(response.context["sponsors"]), [])
        self.assertNotContains(response, "Sponsors")

    def test_sponsors_still_show_for_a_brand_new_account_with_no_managed_people(self):
        today = timezone.localdate()
        Sponsor.objects.create(club=self.club, name="Active Co", start_date=today - datetime.timedelta(days=10))
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get("home")

        self.assertContains(response, "No one to show yet")
        self.assertContains(response, "Active Co")

    def add_child(self, first_name="Noor"):
        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name=first_name, last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        return child

    def test_all_is_the_default_scope_once_theres_more_than_one_managed_person(self):
        self.add_child()
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertTrue(response.context["scope_everyone"])
        self.assertIsNone(response.context["scope_person"])

    def test_a_lone_member_never_defaults_to_all(self):
        # No child added -- managed_people is just [self.member].
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertFalse(response.context["scope_everyone"])
        self.assertEqual(response.context["scope_person"], self.member)

    def test_all_scope_aggregates_the_hero_across_every_managed_person(self):
        child = self.add_child()
        mine = self.make_event(title="Lars's practice", start=self.future)
        theirs = self.make_event(title="Noor's game", start=self.future - datetime.timedelta(hours=1))
        Attendance.objects.create(event=mine, member=self.member)
        Attendance.objects.create(event=theirs, member=child)
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(response.context["hero_attendance"].event, theirs)
        self.assertContains(response, "Noor")

    def test_all_scope_combines_needs_answer_and_dues_across_everyone(self):
        child = self.add_child()
        mine = self.make_event(title="Lars's practice", start=self.future)
        theirs = self.make_event(title="Noor's game", start=self.future + datetime.timedelta(days=1))
        Attendance.objects.create(event=mine, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        Attendance.objects.create(event=theirs, member=child, status=Attendance.AttendanceStatus.NO_RESPONSE)
        child_membership = ClubMembership.objects.get(club=self.club, member=child, season=self.season)
        child_membership.fee_amount = Decimal("100.00")
        child_membership.save(update_fields=["fee_amount"])
        self.client.force_login(self.user)

        response = self._get("home")

        needs_answer_events = {a.event for a in response.context["needs_answer"]}
        self.assertEqual(needs_answer_events, {theirs})  # mine is the hero, excluded from the list
        self.assertEqual(len(response.context["dues_rows"]), 1)
        self.assertEqual(response.context["dues_rows"][0]["membership"].member, child)

    def test_selecting_one_specific_person_narrows_back_to_just_them(self):
        child = self.add_child()
        theirs = self.make_event(title="Noor's game", start=self.future)
        Attendance.objects.create(event=theirs, member=child, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._get(url=reverse("mobile:home") + f"?as={self.member.pk}")

        self.assertFalse(response.context["scope_everyone"])
        self.assertEqual(response.context["scope_person"], self.member)
        self.assertEqual(list(response.context["needs_answer"]), [])

    def test_as_all_explicitly_selects_everyone(self):
        self.add_child()
        self.client.force_login(self.user)

        response = self._get(url=reverse("mobile:home") + "?as=all")

        self.assertTrue(response.context["scope_everyone"])


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class EventDetailRsvpTests(TestCase):
    """M1's quick In/Out RSVP action, posted from the Home hero card."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)
        cls.event = Event.objects.create(club=cls.club, title="Training", start=timezone.now() + datetime.timedelta(days=7))
        cls.attendance = Attendance.objects.create(event=cls.event, member=cls.member, status=Attendance.AttendanceStatus.NO_RESPONSE)

    def _post(self, event, data):
        return self.client.post(reverse("mobile:event_detail", kwargs={"pk": event.pk}), data=data, HTTP_HOST="ajax-united.rosterchief.app")

    def test_posting_present_updates_attendance_and_redirects_home(self):
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "present"})

        self.assertRedirects(response, reverse("mobile:home"), fetch_redirect_response=False)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.PRESENT)

    def test_posting_absent_creates_attendance_when_none_existed(self):
        other_event = Event.objects.create(club=self.club, title="Away game", start=timezone.now() + datetime.timedelta(days=8))
        self.client.force_login(self.user)

        self._post(other_event, {"status": "absent", "note": "Sick"})

        self.assertEqual(Attendance.objects.get(event=other_event, member=self.member).status, Attendance.AttendanceStatus.ABSENT)

    def test_cannot_rsvp_for_a_member_who_isnt_managed(self):
        stranger = Member.objects.create(first_name="Someone", last_name="Else")
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "present", "member_id": str(stranger.pk)})

        self.assertEqual(response.status_code, 400)

    def test_explicit_member_id_still_works_once_all_is_the_default_scope(self):
        # Home's hero form always sends an explicit member_id -- this must keep
        # working once a second managed person makes "All" the default scope
        # (scope_person is None in that case, so the old implicit fallback alone
        # would no longer resolve who's RSVPing).
        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        child_attendance = Attendance.objects.create(event=self.event, member=child, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "present", "member_id": str(child.pk)})

        self.assertRedirects(response, reverse("mobile:home"), fetch_redirect_response=False)
        child_attendance.refresh_from_db()
        self.assertEqual(child_attendance.status, Attendance.AttendanceStatus.PRESENT)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.NO_RESPONSE)

    def test_posting_maybe_updates_attendance(self):
        self.client.force_login(self.user)

        self._post(self.event, {"status": "maybe"})

        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.MAYBE)

    def test_posting_maybe_without_a_reason_is_allowed(self):
        # Unlike Out, a reason is optional for Maybe -- no 400 without one.
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "maybe"})

        self.assertRedirects(response, reverse("mobile:home"), fetch_redirect_response=False)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.note, "")

    def test_posting_maybe_with_a_reason_stores_the_note(self):
        self.client.force_login(self.user)

        self._post(self.event, {"status": "maybe", "note": "Might have to leave early"})

        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.MAYBE)
        self.assertEqual(self.attendance.note, "Might have to leave early")

    def test_posting_absent_with_a_reason_stores_the_note(self):
        self.client.force_login(self.user)

        self._post(self.event, {"status": "absent", "note": "Sick this week"})

        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.ABSENT)
        self.assertEqual(self.attendance.note, "Sick this week")

    def test_note_is_stripped_of_surrounding_whitespace(self):
        self.client.force_login(self.user)

        self._post(self.event, {"status": "absent", "note": "  Sick this week  \n"})

        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.note, "Sick this week")

    def test_posting_present_clears_a_previous_absent_reason(self):
        self.attendance.status = Attendance.AttendanceStatus.ABSENT
        self.attendance.note = "Sick this week"
        self.attendance.save()
        self.client.force_login(self.user)

        self._post(self.event, {"status": "present"})

        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.PRESENT)
        self.assertEqual(self.attendance.note, "")

    def test_a_note_submitted_alongside_a_non_absent_status_is_ignored(self):
        self.client.force_login(self.user)

        self._post(self.event, {"status": "present", "note": "This shouldn't be saved"})

        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.note, "")

    def test_absent_without_a_reason_is_rejected(self):
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "absent"})

        self.assertEqual(response.status_code, 400)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.NO_RESPONSE)

    def test_absent_with_a_whitespace_only_reason_is_rejected(self):
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "absent", "note": "   \n  "})

        self.assertEqual(response.status_code, 400)

    def test_absent_with_a_punctuation_only_reason_is_rejected(self):
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "absent", "note": "..."})

        self.assertEqual(response.status_code, 400)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Attendance.AttendanceStatus.NO_RESPONSE)

    def test_rejects_an_unknown_status_value(self):
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "excused"})

        self.assertEqual(response.status_code, 400)

    def test_next_event_detail_redirects_back_to_the_event_instead_of_home(self):
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "present", "next": "event_detail"})

        self.assertRedirects(response, reverse("mobile:event_detail", kwargs={"pk": self.event.pk}), fetch_redirect_response=False)

    def test_cannot_rsvp_for_an_event_from_another_club(self):
        other_club = Club.objects.create(name="Other Club", slug="other-club", secondary_color="#e4002b")
        other_event = Event.objects.create(club=other_club, title="Not ours", start=timezone.now() + datetime.timedelta(days=3))
        self.client.force_login(self.user)

        response = self._post(other_event, {"status": "present"})

        self.assertEqual(response.status_code, 404)

    def test_cannot_rsvp_once_the_deadline_has_passed(self):
        closed_event = Event.objects.create(club=self.club, title="Cup final", start=timezone.now() + datetime.timedelta(days=3), deadline=timezone.now() - datetime.timedelta(hours=1))
        closed_attendance = Attendance.objects.create(event=closed_event, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._post(closed_event, {"status": "present"})

        self.assertEqual(response.status_code, 400)
        closed_attendance.refresh_from_db()
        self.assertEqual(closed_attendance.status, Attendance.AttendanceStatus.NO_RESPONSE)

    def test_can_still_rsvp_before_the_deadline(self):
        open_event = Event.objects.create(club=self.club, title="Cup final", start=timezone.now() + datetime.timedelta(days=3), deadline=timezone.now() + datetime.timedelta(hours=1))
        Attendance.objects.create(event=open_event, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._post(open_event, {"status": "present"})

        self.assertRedirects(response, reverse("mobile:home"), fetch_redirect_response=False)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class EventDetailDropoutTests(TestCase):
    """The "Can't make it after all" action a SELECTED member sees on a
    published line-up (event_detail.html) -- posts status=dropout, which
    EventDetailView.post resolves to an ordinary ABSENT plus a manager
    notification, since the closed-deadline guard doesn't apply here."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        # Deadline in the past -- a published line-up is typically well after it,
        # and the dropout path specifically has to work despite this.
        cls.event = Event.objects.create(club=cls.club, title="Away game", start=timezone.now() + datetime.timedelta(days=1), deadline=timezone.now() - datetime.timedelta(hours=1))
        cls.event.teams.add(cls.team)
        cls.manager = Member.objects.create(first_name="Cara", last_name="Coach")
        management_position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.manager, season=cls.season, position=management_position)

    def _post(self, data):
        return self.client.post(reverse("mobile:event_detail", kwargs={"pk": self.event.pk}), data=data, HTTP_HOST="ajax-united.rosterchief.app")

    def test_dropout_flips_a_selected_member_to_absent_with_the_given_reason(self):
        Attendance.objects.create(event=self.event, member=self.member, status=Attendance.AttendanceStatus.SELECTED)
        self.client.force_login(self.user)

        response = self._post({"status": "dropout", "note": "Twisted an ankle"})

        self.assertEqual(response.status_code, 302)
        attendance = Attendance.objects.get(event=self.event, member=self.member)
        self.assertEqual(attendance.status, Attendance.AttendanceStatus.ABSENT)
        self.assertEqual(attendance.note, "Twisted an ankle")

    def test_dropout_bypasses_the_closed_deadline_guard(self):
        # setUpTestData's event already has a deadline in the past -- an
        # ordinary Out would 400 here (see EventDetailRsvpTests), dropout must not.
        Attendance.objects.create(event=self.event, member=self.member, status=Attendance.AttendanceStatus.SELECTED)
        self.client.force_login(self.user)

        response = self._post({"status": "dropout", "note": "Twisted an ankle"})

        self.assertEqual(response.status_code, 302)

    def test_dropout_notifies_the_teams_managers(self):
        Attendance.objects.create(event=self.event, member=self.member, status=Attendance.AttendanceStatus.SELECTED)
        self.client.force_login(self.user)

        self._post({"status": "dropout", "note": "Twisted an ankle"})

        notification = Notification.objects.get(member=self.manager)
        self.assertIn(self.member.get_full_name(), notification.body)
        self.assertIn("Twisted an ankle", notification.body)

    def test_dropout_without_a_reason_is_rejected(self):
        Attendance.objects.create(event=self.event, member=self.member, status=Attendance.AttendanceStatus.SELECTED)
        self.client.force_login(self.user)

        response = self._post({"status": "dropout"})

        self.assertEqual(response.status_code, 400)
        attendance = Attendance.objects.get(event=self.event, member=self.member)
        self.assertEqual(attendance.status, Attendance.AttendanceStatus.SELECTED)

    def test_dropout_is_rejected_when_the_member_was_not_selected(self):
        Attendance.objects.create(event=self.event, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        self.client.force_login(self.user)

        response = self._post({"status": "dropout", "note": "Twisted an ankle"})

        self.assertEqual(response.status_code, 400)
        attendance = Attendance.objects.get(event=self.event, member=self.member)
        self.assertEqual(attendance.status, Attendance.AttendanceStatus.PRESENT)

    def test_dropout_is_rejected_when_there_is_no_attendance_row_at_all(self):
        self.client.force_login(self.user)

        response = self._post({"status": "dropout", "note": "Twisted an ankle"})

        self.assertEqual(response.status_code, 400)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CalendarViewTests(TestCase):
    """M3 -- design_handoff_rosterchief_platform/README.md's M3 section: a
    "This week"/"Next week" agenda. No person switcher and no club-wide
    toggle -- always every event self.managed_people is invited to."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)
        cls.soon = timezone.now() + datetime.timedelta(days=1)

    def _get(self, **params):
        url = reverse("mobile:calendar")
        if params:
            url += "?" + "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get(url, HTTP_HOST="ajax-united.rosterchief.app")

    def make_event(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("title", "Training")
        kwargs.setdefault("start", self.soon)
        return Event.objects.create(**kwargs)

    def _events_in_context(self, response):
        rows = response.context["this_week"] + response.context["next_week"]
        for month in response.context["later_months"]:
            rows += month["items"]
        return {row["event"] for row in rows}

    def add_child(self, first_name="Noor"):
        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name=first_name, last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        return child

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_header_shows_a_calendar_title_merged_into_the_shared_header(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, '<span class="font-display text-2xl font-extrabold text-white uppercase">Calendar</span>')

    def test_shows_only_events_the_managed_people_are_invited_to(self):
        invited = self.make_event(title="Lars's practice")
        not_invited = self.make_event(title="Someone else's practice")
        Attendance.objects.create(event=invited, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(self._events_in_context(response), {invited})
        self.assertNotContains(response, not_invited.title)

    def test_excludes_cancelled_events(self):
        cancelled = self.make_event(title="Cancelled practice", cancelled=True)
        Attendance.objects.create(event=cancelled, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotIn(cancelled, self._events_in_context(response))

    def test_aggregates_across_every_managed_person(self):
        child = self.add_child()
        mine = self.make_event(title="Lars's practice")
        theirs = self.make_event(title="Noor's game")
        Attendance.objects.create(event=mine, member=self.member)
        Attendance.objects.create(event=theirs, member=child)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(self._events_in_context(response), {mine, theirs})
        self.assertContains(response, "Noor")

    def test_no_person_switcher_is_rendered(self):
        self.add_child()
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, 'href="?as=')

    def test_games_filter_shows_only_games(self):
        game = self.make_event(title="vs Leuven", kind=Event.EventKind.GAME)
        practice = self.make_event(title="Ice 3", kind=Event.EventKind.TRAINING)
        Attendance.objects.create(event=game, member=self.member)
        Attendance.objects.create(event=practice, member=self.member)
        self.client.force_login(self.user)

        response = self._get(kind="game")

        self.assertEqual(self._events_in_context(response), {game})
        self.assertEqual(response.context["kind_filter"], "game")

    def test_practices_filter_shows_only_practices(self):
        game = self.make_event(title="vs Leuven", kind=Event.EventKind.GAME)
        practice = self.make_event(title="Ice 3", kind=Event.EventKind.TRAINING)
        Attendance.objects.create(event=game, member=self.member)
        Attendance.objects.create(event=practice, member=self.member)
        self.client.force_login(self.user)

        response = self._get(kind="training")

        self.assertEqual(self._events_in_context(response), {practice})

    def test_unrecognized_kind_falls_back_to_all(self):
        event = self.make_event(title="Social night", kind=Event.EventKind.SOCIAL)
        Attendance.objects.create(event=event, member=self.member)
        self.client.force_login(self.user)

        response = self._get(kind="bogus")

        self.assertEqual(self._events_in_context(response), {event})
        self.assertEqual(response.context["kind_filter"], "")

    def test_no_managed_people_shows_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No one to show yet")

    def test_past_events_are_excluded(self):
        past = self.make_event(title="Past practice", start=timezone.now() - datetime.timedelta(days=1))
        Attendance.objects.create(event=past, member=self.member)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(self._events_in_context(response), set())

    def test_events_beyond_next_week_are_grouped_by_month(self):
        far_future = self.make_event(title="Far future game", start=timezone.now() + datetime.timedelta(days=45))
        Attendance.objects.create(event=far_future, member=self.member)
        self.client.force_login(self.user)

        response = self._get()

        self.assertIn(far_future, self._events_in_context(response))
        later_months = response.context["later_months"]
        self.assertEqual(len(later_months), 1)
        month_start = timezone.localtime(far_future.start).date().replace(day=1)
        self.assertEqual(later_months[0]["month_start"], month_start)
        self.assertEqual([row["event"] for row in later_months[0]["items"]], [far_future])
        self.assertContains(response, month_start.strftime("%B %Y"))

    def test_further_out_events_are_split_across_separate_month_groups(self):
        # >=32 days apart guarantees two different calendar months regardless
        # of which day-of-month "today" happens to be (the longest month is
        # 31 days), so this can't flake depending on when the suite runs.
        this_month = self.make_event(title="This month game", start=timezone.now() + datetime.timedelta(days=45))
        next_month = self.make_event(title="Next month game", start=timezone.now() + datetime.timedelta(days=80))
        Attendance.objects.create(event=this_month, member=self.member)
        Attendance.objects.create(event=next_month, member=self.member)
        self.client.force_login(self.user)

        response = self._get()

        later_months = response.context["later_months"]
        self.assertEqual(len(later_months), 2)

    def test_window_covers_at_least_fourteen_days_regardless_of_which_weekday_today_is(self):
        # An event 13 days out (a noon start, so today's own time-of-day can't
        # push it across a date boundary) must always still show up somewhere
        # on the agenda, whatever day the test happens to run on -- whether
        # that's "Next week" or (on weekdays where the calendar week ends
        # sooner) the first month group.
        thirteen_days_out = timezone.make_aware(datetime.datetime.combine(timezone.localdate() + datetime.timedelta(days=13), datetime.time(12, 0)))
        event = self.make_event(title="Two weeks out", start=thirteen_days_out)
        Attendance.objects.create(event=event, member=self.member)
        self.client.force_login(self.user)

        response = self._get()

        self.assertIn(event, self._events_in_context(response))

    def test_a_blocked_event_shows_up_with_an_explanation_instead_of_disappearing(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        position = Position.objects.create(club=self.club, name="Forward", short_name="F")
        TeamMembership.objects.create(team=team, member=self.member, season=self.season, position=position)
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        event = self.make_event(title="Cup game", kind=Event.EventKind.GAME)
        event.teams.add(team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Cup game")
        self.assertContains(response, "Blocked")
        self.assertContains(response, "Medical certificate")
        self.assertFalse(Attendance.objects.filter(event=event, member=self.member).exists())

    def test_a_resolved_requirement_shows_the_event_normally_not_blocked(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        position = Position.objects.create(club=self.club, name="Forward", short_name="F")
        TeamMembership.objects.create(team=team, member=self.member, season=self.season, position=position)
        requirement = OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        club_membership = ClubMembership.objects.get(club=self.club, member=self.member, season=self.season)
        MemberRequirementStatus.objects.create(membership=club_membership, requirement=requirement, is_complete=True)
        event = self.make_event(title="Cup game", kind=Event.EventKind.GAME)
        event.teams.add(team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Cup game")
        self.assertNotContains(response, "Blocked")
        self.assertTrue(Attendance.objects.filter(event=event, member=self.member).exists())


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CalendarRefereeSignupTests(TestCase):
    """M3's Calendar merges in every managed person's referee sign-ups, not
    just self.me -- see mobile/_calendar_referee_row.html and CalendarView's
    own docstring."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="ref@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Ref", last_name="Eree", email="ref@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)

        cls.home_ground = Location.objects.create(club=cls.club, name="Home Rink", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.level = RefereeLevel.objects.create(club=cls.club, name="Regional")
        cls.level.teams.add(cls.team)
        RefereeProfile.objects.create(member=cls.member, level=cls.level, valid_until=today + datetime.timedelta(days=30))

        cls.game = Event.objects.create(club=cls.club, title="Home game", kind=Event.EventKind.GAME, location=cls.home_ground, start=timezone.now() + datetime.timedelta(days=1))
        cls.game.teams.add(cls.team)  # triggers sync_referee_invites via events/signals.py

    def _get(self, **params):
        url = reverse("mobile:calendar")
        if params:
            url += "?" + "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get(url, HTTP_HOST="ajax-united.rosterchief.app")

    def test_invited_game_shows_up_on_the_calendar(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Referee")
        self.assertContains(response, "Reply needed")
        self.assertContains(response, f'href="{reverse("mobile:event_detail", kwargs={"pk": self.game.pk})}"')

    def test_declined_signup_is_not_shown(self):
        signup = RefereeSignup.objects.get(event=self.game, member=self.member)
        signup.status = RefereeSignup.Status.DECLINED
        signup.save()
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "Reply needed")

    def test_accepted_signup_shows_a_confirmed_pill(self):
        signup = RefereeSignup.objects.get(event=self.game, member=self.member)
        signup.status = RefereeSignup.Status.ACCEPTED
        signup.save()
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Confirmed")
        self.assertNotContains(response, "Reply needed")

    def test_training_filter_excludes_referee_rows(self):
        self.client.force_login(self.user)

        response = self._get(kind="training")

        self.assertNotContains(response, "Reply needed")

    def test_a_managed_childs_invite_shows_up_and_names_them(self):
        family = Family.objects.create(name="Eree")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Kid", last_name="Eree")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        RefereeProfile.objects.create(member=child, level=self.level, valid_until=timezone.localdate() + datetime.timedelta(days=30))
        other_game = Event.objects.create(club=self.club, title="Second game", kind=Event.EventKind.GAME, location=self.home_ground, start=timezone.now() + datetime.timedelta(days=2))
        other_game.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertTrue(RefereeSignup.objects.filter(event=other_game, member=child).exists())
        self.assertContains(response, "Kid")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class RefereeSignupRespondViewTests(TestCase):
    """mobile:referee_signup_respond -- Accept/Decline from the Calendar."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="ref@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Ref", last_name="Eree", email="ref@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)

        cls.home_ground = Location.objects.create(club=cls.club, name="Home Rink", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.level = RefereeLevel.objects.create(club=cls.club, name="Regional")
        cls.level.teams.add(cls.team)
        RefereeProfile.objects.create(member=cls.member, level=cls.level, valid_until=today + datetime.timedelta(days=30))

        cls.game = Event.objects.create(club=cls.club, title="Home game", kind=Event.EventKind.GAME, location=cls.home_ground, start=timezone.now() + datetime.timedelta(days=1))
        cls.game.teams.add(cls.team)

    def _post(self, response_value, **extra):
        signup = RefereeSignup.objects.get(event=self.game, member=self.member)
        return self.client.post(reverse("mobile:referee_signup_respond", kwargs={"signup_id": signup.pk}), {"response": response_value, **extra}, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._post("accept")

        self.assertEqual(response.status_code, 302)

    def test_accept_creates_a_real_assignment(self):
        self.client.force_login(self.user)

        response = self._post("accept")

        self.assertRedirects(response, reverse("mobile:calendar"), fetch_redirect_response=False)
        self.assertTrue(EventReferee.objects.filter(event=self.game, member=self.member).exists())
        signup = RefereeSignup.objects.get(event=self.game, member=self.member)
        self.assertEqual(signup.status, RefereeSignup.Status.ACCEPTED)

    def test_accept_shows_an_error_when_the_game_is_already_full(self):
        self.game.max_referees = 0
        self.game.save(update_fields=["max_referees"])
        self.client.force_login(self.user)

        response = self._post("accept")

        self.assertRedirects(response, reverse("mobile:calendar"), fetch_redirect_response=False)
        self.assertFalse(EventReferee.objects.filter(event=self.game, member=self.member).exists())
        signup = RefereeSignup.objects.get(event=self.game, member=self.member)
        self.assertEqual(signup.status, RefereeSignup.Status.INVITED)

    def test_decline_marks_the_signup_declined(self):
        self.client.force_login(self.user)

        response = self._post("decline")

        self.assertRedirects(response, reverse("mobile:calendar"), fetch_redirect_response=False)
        signup = RefereeSignup.objects.get(event=self.game, member=self.member)
        self.assertEqual(signup.status, RefereeSignup.Status.DECLINED)

    def test_next_event_detail_redirects_back_to_the_event_instead_of_calendar(self):
        self.client.force_login(self.user)

        response = self._post("accept", next="event_detail")

        self.assertRedirects(response, reverse("mobile:event_detail", kwargs={"pk": self.game.pk}), fetch_redirect_response=False)

    def test_cannot_respond_to_someone_elses_signup(self):
        stranger_user = User.objects.create_user(email="stranger@example.com", password="pw-secret-123")
        Member.objects.create(first_name="Not", last_name="You", email="stranger@example.com", user=stranger_user)
        self.client.force_login(stranger_user)

        response = self._post("accept")

        self.assertEqual(response.status_code, 404)

    def test_a_parent_can_respond_on_behalf_of_a_managed_child(self):
        family = Family.objects.create(name="Eree")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Kid", last_name="Eree")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        RefereeProfile.objects.create(member=child, level=self.level, valid_until=timezone.localdate() + datetime.timedelta(days=30))
        child_signup = RefereeSignup.objects.create(event=self.game, member=child, status=RefereeSignup.Status.INVITED)
        self.client.force_login(self.user)

        response = self.client.post(reverse("mobile:referee_signup_respond", kwargs={"signup_id": child_signup.pk}), {"response": "accept"}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:calendar"), fetch_redirect_response=False)
        self.assertTrue(EventReferee.objects.filter(event=self.game, member=child).exists())


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class EventDetailScreenTests(TestCase):
    """M2 -- design_handoff_rosterchief_platform/README.md's M2 section,
    "answer for several" (the GET side; POST is covered by
    EventDetailRsvpTests above)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Forward", short_name="F")
        cls.event = Event.objects.create(club=cls.club, title="Home game", start=timezone.now() + datetime.timedelta(days=7))
        cls.event.teams.add(cls.team)

    def _get(self, event=None):
        event = event or self.event
        return self.client.get(reverse("mobile:event_detail", kwargs={"pk": event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

    def test_renders_event_details(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Home game")

    def test_hero_shows_the_clubs_event_background_in_grayscale_when_set(self):
        self.club.event_background = make_image_file()
        self.club.save(update_fields=["event_background"])
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, 'class="absolute inset-0 h-full w-full object-cover grayscale"')
        self.assertContains(response, self.club.event_background.url)

    def test_hero_has_no_background_image_when_the_club_has_not_set_one(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "grayscale")

    def test_rsvp_buttons_are_replaced_by_a_readonly_pill_once_the_deadline_has_passed(self):
        closed_event = Event.objects.create(club=self.club, title="Closed game", start=timezone.now() + datetime.timedelta(days=3), deadline=timezone.now() - datetime.timedelta(hours=1))
        Attendance.objects.create(event=closed_event, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._get(closed_event)

        self.assertTrue(response.context["rsvp_closed"])
        self.assertNotContains(response, 'name="status" value="present"')

    def test_your_answers_only_lists_managed_people_invited_to_this_event(self):
        # TeamMembership -> Event.teams (both already set up in setUpTestData) auto-creates
        # a NO_RESPONSE Attendance row via events/signals.py -- no need to create one by hand.
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=self.position, jersey_number=17)

        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        not_invited_child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=not_invited_child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=not_invited_child, season=self.season)
        # Noor is a managed person but has no Attendance row for this event -- not invited.

        self.client.force_login(self.user)

        response = self._get()

        answered_members = {answer["member"] for answer in response.context["your_answers"]}
        self.assertEqual(answered_members, {self.member})
        self.assertContains(response, "#17")
        self.assertContains(response, "No reply")

    def test_your_answers_shows_your_own_absence_reason(self):
        Attendance.objects.create(event=self.event, member=self.member, status=Attendance.AttendanceStatus.ABSENT, note="Sick this week")
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Sick this week")

    def test_your_answers_is_empty_when_nobody_managed_is_invited(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(list(response.context["your_answers"]), [])
        self.assertContains(response, "No one you manage is invited")

    def test_no_lineup_card_when_none_is_published(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertIsNone(response.context["lineup"])
        self.assertNotContains(response, "Line-up")

    def test_unpublished_lineup_is_not_shown(self):
        lineup = Lineup.objects.create(event=self.event, team=self.team)
        LineupSelection.objects.create(lineup=lineup, member=self.member)
        self.client.force_login(self.user)

        response = self._get()

        self.assertIsNone(response.context["lineup"])

    def test_published_lineup_shows_its_selected_members_grouped_by_position(self):
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=self.position)
        lineup = Lineup.objects.create(event=self.event, team=self.team, published_at=timezone.now())
        LineupSelection.objects.create(lineup=lineup, member=self.member)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["lineup"], lineup)
        self.assertContains(response, "Forward")
        self.assertContains(response, self.member.get_full_name())

    def test_rsvp_buttons_are_replaced_by_a_readonly_pill_once_the_lineup_is_published(self):
        Lineup.objects.create(event=self.event, team=self.team, published_at=timezone.now())
        Attendance.objects.create(event=self.event, member=self.member, status=Attendance.AttendanceStatus.SELECTED)
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, 'name="status" value="present"')

    def test_selected_member_sees_the_cant_make_it_button(self):
        Lineup.objects.create(event=self.event, team=self.team, published_at=timezone.now())
        Attendance.objects.create(event=self.event, member=self.member, status=Attendance.AttendanceStatus.SELECTED)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Can't make it after all")
        self.assertContains(response, 'name="status" value="dropout"')

    def test_not_selected_member_does_not_see_the_cant_make_it_button(self):
        Lineup.objects.create(event=self.event, team=self.team, published_at=timezone.now())
        Attendance.objects.create(event=self.event, member=self.member, status=Attendance.AttendanceStatus.NOT_SELECTED)
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, 'name="status" value="dropout"')

    def test_blocked_signup_shows_why_instead_of_disappearing(self):
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        game = Event.objects.create(club=self.club, title="Cup game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=7))
        game.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get(game)

        self.assertEqual(len(response.context["blocked_signups"]), 1)
        self.assertContains(response, "Can't sign up yet")
        self.assertContains(response, "Medical certificate")
        self.assertFalse(Attendance.objects.filter(event=game, member=self.member).exists())

    def test_no_blocked_card_once_the_requirement_is_resolved(self):
        requirement = OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        club_membership = ClubMembership.objects.get(club=self.club, member=self.member, season=self.season)
        MemberRequirementStatus.objects.create(membership=club_membership, requirement=requirement, is_complete=True)
        game = Event.objects.create(club=self.club, title="Cup game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=7))
        game.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get(game)

        self.assertEqual(response.context["blocked_signups"], [])
        self.assertNotContains(response, "Can't sign up yet")

    def test_no_referee_card_when_not_invited(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(list(response.context["referee_signups"]), [])
        self.assertNotContains(response, "Refereeing")

    def test_pending_referee_invite_shows_accept_decline(self):
        RefereeSignup.objects.create(event=self.event, member=self.member, status=RefereeSignup.Status.INVITED)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Refereeing")
        self.assertContains(response, "I'll ref")
        self.assertNotContains(response, "Confirmed")

    def test_accepted_referee_signup_shows_a_confirmed_pill_with_no_actions(self):
        RefereeSignup.objects.create(event=self.event, member=self.member, status=RefereeSignup.Status.ACCEPTED)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Confirmed")
        self.assertNotContains(response, "I'll ref")

    def test_declined_referee_signup_is_not_shown(self):
        RefereeSignup.objects.create(event=self.event, member=self.member, status=RefereeSignup.Status.DECLINED)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(list(response.context["referee_signups"]), [])
        self.assertNotContains(response, "Refereeing")

    def test_a_managed_childs_referee_invite_shows_up_too(self):
        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        RefereeSignup.objects.create(event=self.event, member=child, status=RefereeSignup.Status.INVITED)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Refereeing")
        self.assertContains(response, "Noor Bakker")
        self.assertContains(response, "I'll ref")

    def test_squad_response_counts_are_correct(self):
        in_member = Member.objects.create(first_name="A", last_name="In")
        out_member = Member.objects.create(first_name="B", last_name="Out")
        no_reply_member = Member.objects.create(first_name="C", last_name="Silent")
        Attendance.objects.create(event=self.event, member=in_member, status=Attendance.AttendanceStatus.PRESENT)
        Attendance.objects.create(event=self.event, member=out_member, status=Attendance.AttendanceStatus.ABSENT)
        Attendance.objects.create(event=self.event, member=no_reply_member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._get()

        summary = response.context["squad_summary"]
        self.assertEqual(summary["in_count"], 1)
        self.assertEqual(summary["out_count"], 1)
        self.assertEqual(summary["no_reply_count"], 1)
        self.assertEqual(summary["total"], 3)

    def test_squad_response_never_leaks_another_members_absence_reason(self):
        # Squad response is counts-only by design -- a reason belongs to the
        # member/family who wrote it and to Coach mode, never to the rest of
        # the squad's own event page.
        out_member = Member.objects.create(first_name="B", last_name="Out")
        Attendance.objects.create(event=self.event, member=out_member, status=Attendance.AttendanceStatus.ABSENT, note="Family holiday")
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "Family holiday")

    def test_squad_response_is_absent_for_an_event_with_no_teams(self):
        club_wide_event = Event.objects.create(club=self.club, title="Club BBQ", start=timezone.now() + datetime.timedelta(days=3), club_wide=True)
        self.client.force_login(self.user)

        response = self._get(club_wide_event)

        self.assertIsNone(response.context["squad_summary"])

    def test_404_for_an_event_from_another_club(self):
        other_club = Club.objects.create(name="Other Club", slug="other-club", secondary_color="#e4002b")
        other_event = Event.objects.create(club=other_club, title="Not ours", start=timezone.now() + datetime.timedelta(days=3))
        self.client.force_login(self.user)

        response = self._get(other_event)

        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class NotificationsViewTests(TestCase):
    """M7 -- design_handoff_rosterchief_platform/README.md's M7 section
    ("Inbox"), scoped to every managed_people (see NotificationsView's own
    docstring for why, not just scope_person)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)

        cls.family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=cls.family, member=cls.member, role=FamilyMembership.FamilyRole.PARENT)
        cls.child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=cls.family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=cls.club, member=cls.child, season=cls.season)

    def _get(self):
        return self.client.get(reverse("mobile:notifications"), HTTP_HOST="ajax-united.rosterchief.app")

    def _post(self, data):
        return self.client.post(reverse("mobile:notifications"), data=data, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_lists_notifications_for_every_managed_person_not_just_scope_person(self):
        Notification.objects.create(club=self.club, member=self.member, title="For Lars", body="Body.")
        Notification.objects.create(club=self.club, member=self.child, title="For Noor", body="Body.")
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "For Lars")
        self.assertContains(response, "For Noor")

    def test_body_text_is_shown_in_full_not_truncated(self):
        long_body = "This is a long notification body. " * 10
        Notification.objects.create(club=self.club, member=self.member, title="Long one", body=long_body)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, long_body)

    def test_notification_for_someone_not_managed_is_excluded(self):
        stranger = Member.objects.create(first_name="Someone", last_name="Else")
        Notification.objects.create(club=self.club, member=stranger, title="Not yours", body="Body.")
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "Not yours")

    def test_mark_all_read_updates_every_unread_row_and_the_unread_count(self):
        first = Notification.objects.create(club=self.club, member=self.member, title="First", body="Body.")
        second = Notification.objects.create(club=self.club, member=self.child, title="Second", body="Body.")
        self.client.force_login(self.user)

        response = self._post({"action": "mark_all_read"})

        self.assertRedirects(response, reverse("mobile:notifications"), fetch_redirect_response=False)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(first.read_at)
        self.assertIsNotNone(second.read_at)

        follow_up = self._get()
        self.assertEqual(follow_up.context["unread_notification_count"], 0)

    def test_clear_all_deletes_every_notification_for_every_managed_person(self):
        Notification.objects.create(club=self.club, member=self.member, title="First", body="Body.")
        Notification.objects.create(club=self.club, member=self.child, title="Second", body="Body.")
        stranger = Member.objects.create(first_name="Someone", last_name="Else")
        untouched = Notification.objects.create(club=self.club, member=stranger, title="Not yours", body="Body.")
        self.client.force_login(self.user)

        response = self._post({"action": "clear_all"})

        self.assertRedirects(response, reverse("mobile:notifications"), fetch_redirect_response=False)
        self.assertEqual(Notification.objects.filter(member__in=[self.member, self.child]).count(), 0)
        self.assertTrue(Notification.objects.filter(pk=untouched.pk).exists())

    def test_mark_read_marks_a_single_notification_and_redirects_back(self):
        notification = Notification.objects.create(club=self.club, member=self.member, title="First", body="Body.")
        self.client.force_login(self.user)

        response = self._post({"action": "mark_read", "notification_id": str(notification.pk)})

        self.assertRedirects(response, reverse("mobile:notifications"), fetch_redirect_response=False)
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    def test_mark_read_rejects_a_notification_belonging_to_someone_not_managed(self):
        stranger = Member.objects.create(first_name="Someone", last_name="Else")
        notification = Notification.objects.create(club=self.club, member=stranger, title="Not yours", body="Body.")
        self.client.force_login(self.user)

        response = self._post({"action": "mark_read", "notification_id": str(notification.pk)})

        self.assertEqual(response.status_code, 400)
        notification.refresh_from_db()
        self.assertIsNone(notification.read_at)

    def test_notification_with_a_news_source_is_marked_out_and_mark_read_redirects_to_the_article(self):
        news_item = News.objects.create(club=self.club, title="Signed: New Player", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())
        notification = Notification.objects.create(club=self.club, member=self.member, title="New article", body="Body.", source=news_item)
        self.client.force_login(self.user)

        response = self._get()
        self.assertContains(response, "Club news")

        redirect_response = self._post({"action": "mark_read", "notification_id": str(notification.pk)})
        self.assertRedirects(redirect_response, reverse("mobile:news_detail", kwargs={"slug": news_item.slug}), fetch_redirect_response=False)

    def test_notification_with_an_event_source_is_labelled_and_mark_read_redirects_to_it(self):
        event = Event.objects.create(club=self.club, title="Practice", start=timezone.now() + datetime.timedelta(days=1))
        notification = Notification.objects.create(club=self.club, member=self.member, title="New event", body="Body.", source=event)
        self.client.force_login(self.user)

        response = self._get()
        self.assertContains(response, "New event")

        redirect_response = self._post({"action": "mark_read", "notification_id": str(notification.pk)})
        self.assertRedirects(redirect_response, reverse("mobile:event_detail", kwargs={"pk": event.pk}), fetch_redirect_response=False)

    def test_empty_account_gets_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No one to show yet")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class NewsListViewTests(TestCase):
    """"All news" -- what Home's own news card links to. Every published,
    internal-or-both news item, not filtered to any particular team."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", user=cls.user)

    def _get(self, **params):
        url = reverse("mobile:news_list")
        if params:
            url += "?" + "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get(url, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_header_shows_a_news_title_merged_into_the_shared_header(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, '<span class="font-display text-2xl font-extrabold text-white uppercase">News</span>')

    def test_lists_every_internal_or_both_item_regardless_of_team(self):
        team_item = News.objects.create(club=self.club, title="Team news", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        team_item.teams.add(team)
        club_item = News.objects.create(club=self.club, title="Club-wide news", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())
        both_item = News.objects.create(club=self.club, title="Both audiences", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now(), visibility=News.Visibility.BOTH)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(set(response.context["page"].object_list), {team_item, club_item, both_item})

    def test_excludes_external_only_draft_and_future_scheduled_items(self):
        News.objects.create(club=self.club, title="External only", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now(), visibility=News.Visibility.EXTERNAL)
        News.objects.create(club=self.club, title="Draft", body="Body.", status=News.Status.DRAFT)
        News.objects.create(club=self.club, title="Scheduled", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() + datetime.timedelta(days=3))
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(list(response.context["page"].object_list), [])

    def test_paginates_at_twenty_per_page(self):
        for day in range(25):
            News.objects.create(club=self.club, title=f"Item {day}", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(days=day))
        self.client.force_login(self.user)

        first_page = self._get()
        second_page = self._get(page=2)

        self.assertEqual(len(first_page.context["page"].object_list), 20)
        self.assertEqual(len(second_page.context["page"].object_list), 5)

    def test_empty_state_when_nothing_to_show(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "No news yet.")

    def test_news_tab_is_active(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["active_tab"], "news")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class NewsDetailScreenTests(TestCase):
    """M4 -- design_handoff_rosterchief_platform/README.md's M4 section,
    "News article". Visibility mirrors news.tasks.notify_news_published's own
    gate (see NewsDetailView's docstring); language is Django's own
    active-language detection, not a member-facing toggle."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)

    def _get(self, news_item):
        return self.client.get(reverse("mobile:news_detail", kwargs={"slug": news_item.slug}), HTTP_HOST="ajax-united.rosterchief.app")

    def test_a_published_item_renders(self):
        news_item = News.objects.create(club=self.club, title="Season Kickoff", body="We start training next week.", status=News.Status.PUBLISHED, published_at=timezone.now())
        self.client.force_login(self.user)

        response = self._get(news_item)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Season Kickoff")
        self.assertContains(response, "We start training next week.")

    def test_a_draft_item_404s(self):
        news_item = News.objects.create(club=self.club, title="Draft item", body="Not live yet.", status=News.Status.DRAFT)
        self.client.force_login(self.user)

        response = self._get(news_item)

        self.assertEqual(response.status_code, 404)

    def test_a_future_scheduled_item_404s(self):
        news_item = News.objects.create(
            club=self.club,
            title="Scheduled item",
            body="Not live yet.",
            status=News.Status.PUBLISHED,
            published_at=timezone.now() + datetime.timedelta(days=1),
        )
        self.client.force_login(self.user)

        response = self._get(news_item)

        self.assertEqual(response.status_code, 404)

    def test_english_fallback_is_not_used_when_no_translation_exists_and_the_active_language_is_dutch(self):
        news_item = News.objects.create(club=self.club, title="Seizoensstart", body="We beginnen volgende week.", status=News.Status.PUBLISHED, published_at=timezone.now())
        self.client.force_login(self.user)

        with translation.override("nl"):
            response = self._get(news_item)

        self.assertContains(response, "Seizoensstart")
        self.assertContains(response, "We beginnen volgende week.")

    def test_english_translation_shows_when_the_active_language_is_english(self):
        news_item = News.objects.create(
            club=self.club,
            title="Seizoensstart",
            body="We beginnen volgende week.",
            title_en="Season kickoff",
            body_en="We start next week.",
            status=News.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        self.client.force_login(self.user)

        with translation.override("en"):
            response = self._get(news_item)

        self.assertContains(response, "Season kickoff")
        self.assertContains(response, "We start next week.")

    def test_teams_show_as_tags_when_present(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        news_item = News.objects.create(club=self.club, title="Team news", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())
        news_item.teams.add(team)
        self.client.force_login(self.user)

        response = self._get(news_item)

        self.assertContains(response, "U16")
        self.assertNotContains(response, "Club news")

    def test_club_wide_item_shows_club_news_when_no_teams(self):
        news_item = News.objects.create(club=self.club, title="Club-wide news", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())
        self.client.force_login(self.user)

        response = self._get(news_item)

        self.assertContains(response, "Club news")

    def test_posted_by_shows_when_created_by_is_set(self):
        news_item = News.objects.create(club=self.club, title="Byline test", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now(), created_by=self.member)
        self.client.force_login(self.user)

        response = self._get(news_item)

        self.assertContains(response, "Posted by Lars Bakker")

    def test_requires_login(self):
        news_item = News.objects.create(club=self.club, title="Season Kickoff", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())

        response = self._get(news_item)

        self.assertEqual(response.status_code, 302)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class MeViewTests(TestCase):
    """M5 -- design_handoff_rosterchief_platform/README.md's M5 section, "Me
    & my people". See MeView's own docstring for the judgment calls: no
    licence/eligibility field backing "licence OK" (real roster data used
    instead), "Household & contacts"/"Coach mode" omitted since neither has
    anywhere to lead in this build; "Payments & dues" does (PaymentsView,
    tested separately below).
    """

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)

        cls.family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=cls.family, member=cls.member, role=FamilyMembership.FamilyRole.PARENT)
        cls.child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=cls.family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=cls.club, member=cls.child, season=cls.season)

    def _get(self):
        return self.client.get(reverse("mobile:me"), HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_own_record_shows_the_me_suffix(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lars Bakker")
        self.assertContains(response, "(me)")

    def test_header_is_merged_into_the_shared_navy_header_not_a_separate_block(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "bg-ink")

    def test_every_managed_child_appears_in_the_list(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Noor Bakker")

    def test_a_persons_row_links_to_their_edit_profile_url(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, reverse("mobile:edit_profile", kwargs={"member_id": self.member.pk}))
        self.assertContains(response, reverse("mobile:edit_profile", kwargs={"member_id": self.child.pk}))

    def test_a_managed_persons_current_season_team_and_number_show_as_the_meta_line(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        TeamMembership.objects.create(team=team, member=self.child, season=self.season, jersey_number=9)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "U16")
        self.assertContains(response, "#9")

    def test_coach_mode_promo_is_never_rendered(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "Coach mode")

    def test_team_manager_label_shows_for_a_current_season_management_staff_assignment(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        position = Position.objects.create(club=self.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Team manager U16")

    def test_no_teams_card_without_a_staff_assignment(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "Teams I coach/manage")

    def test_teams_card_lists_each_current_season_staff_assignment(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Teams I coach/manage")
        self.assertContains(response, "U16")
        self.assertContains(response, "Physio")
        self.assertContains(response, reverse("mobile:coach_today") + "?team=" + str(team.pk))

    def test_empty_account_gets_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No one to show yet")
        self.assertNotContains(response, "Coach mode")

    def test_payments_row_links_to_the_payments_screen(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, reverse("mobile:payments"))

    def test_open_dues_pill_is_hidden_with_nothing_owed(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "OPEN")

    def test_open_dues_pill_shows_the_open_count(self):
        membership = ClubMembership.objects.get(club=self.club, member=self.child, season=self.season)
        membership.fee_amount = Decimal("150.00")
        membership.save()
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["open_dues_count"], 1)
        self.assertContains(response, "1 OPEN")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class PaymentsViewTests(TestCase):
    """M5's "Payments & dues" row -- every open season-dues balance across
    every managed person, reusing club.services.fees.open_dues_rows (see
    club.tests.OpenDuesRowsTests for the exclusion rules themselves)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        cls.membership = ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)

    def _get(self):
        return self.client.get(reverse("mobile:payments"), HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_back_link_returns_to_me(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, reverse("mobile:me"))

    def test_nothing_owed_shows_a_settled_up_empty_state(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "All settled up")

    def test_an_open_balance_shows_the_amount_and_a_pay_button(self):
        self.membership.fee_amount = Decimal("150.00")
        self.membership.save()
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(len(response.context["dues_rows"]), 1)
        self.assertContains(response, "150.00")
        self.assertContains(response, "Pay")
        self.assertNotContains(response, "All settled up")

    def test_a_fully_paid_balance_does_not_show(self):
        self.membership.fee_amount = Decimal("150.00")
        self.membership.fee_status = ClubMembership.FeeStatus.PAID
        self.membership.amount_paid = Decimal("150.00")
        self.membership.save()
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["dues_rows"], [])

    def test_only_shows_balances_for_managed_people(self):
        other_member = Member.objects.create(first_name="Tom", last_name="Roe")
        ClubMembership.objects.create(club=self.club, member=other_member, season=self.season, fee_amount=Decimal("200.00"))
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["dues_rows"], [])


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class EditProfileViewTests(TestCase):
    """M6 -- design_handoff_rosterchief_platform/README.md's M6 section,
    "Edit personal info". See EditProfileView's own docstring for the
    judgment calls: no schema fields for national register no./address/
    allergies/consent (all omitted), guardians and open onboarding
    requirements are shown read-only, and an unmanaged member 404s rather
    than mirroring EventDetailView.post's 400."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="lars@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)

        cls.family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=cls.family, member=cls.member, role=FamilyMembership.FamilyRole.PARENT)
        cls.child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=cls.family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)
        cls.child_membership = ClubMembership.objects.create(club=cls.club, member=cls.child, season=cls.season)

    def _get(self, member):
        return self.client.get(reverse("mobile:edit_profile", kwargs={"member_id": member.pk}), HTTP_HOST="ajax-united.rosterchief.app")

    def _post(self, member, data):
        return self.client.post(reverse("mobile:edit_profile", kwargs={"member_id": member.pk}), data=data, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get(self.member)

        self.assertEqual(response.status_code, 302)

    def test_get_renders_the_form_prefilled_with_the_persons_current_data(self):
        self.client.force_login(self.user)

        response = self._get(self.member)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lars")
        self.assertContains(response, "Bakker")
        self.assertContains(response, "lars@example.com")

    def test_post_with_valid_data_updates_the_member_and_redirects_to_me(self):
        self.client.force_login(self.user)

        response = self._post(
            self.member,
            {"first_name": "Larsen", "last_name": "Bakker", "email": "larsen@example.com", "phone": "", "emergency_phone": ""},
        )

        self.assertRedirects(response, reverse("mobile:me"), fetch_redirect_response=False)
        self.member.refresh_from_db()
        self.assertEqual(self.member.first_name, "Larsen")
        self.assertEqual(self.member.email, "larsen@example.com")

    def test_post_with_invalid_data_rerenders_with_errors_and_does_not_save(self):
        self.client.force_login(self.user)

        response = self._post(
            self.member,
            {"first_name": "", "last_name": "Bakker", "email": "lars@example.com", "phone": "", "emergency_phone": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.member.refresh_from_db()
        self.assertEqual(self.member.first_name, "Lars")

    def test_cannot_edit_a_member_who_isnt_managed(self):
        stranger = Member.objects.create(first_name="Someone", last_name="Else")
        self.client.force_login(self.user)

        get_response = self._get(stranger)
        post_response = self._post(stranger, {"first_name": "Hacked", "last_name": "Else", "email": "", "phone": "", "emergency_phone": ""})

        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)
        stranger.refresh_from_db()
        self.assertEqual(stranger.first_name, "Someone")

    def test_a_managed_child_can_be_edited_too(self):
        self.client.force_login(self.user)

        response = self._post(
            self.child,
            {"first_name": "Noor", "last_name": "Bakker", "email": "noor@example.com", "phone": "", "emergency_phone": ""},
        )

        self.assertRedirects(response, reverse("mobile:me"), fetch_redirect_response=False)
        self.child.refresh_from_db()
        self.assertEqual(self.child.email, "noor@example.com")

    def test_guardian_shows_as_a_readonly_emergency_contact_line(self):
        self.client.force_login(self.user)

        response = self._get(self.child)

        self.assertContains(response, "Lars Bakker")
        self.assertContains(response, "parent")

    def test_no_emergency_contact_line_when_the_person_has_no_guardians(self):
        self.client.force_login(self.user)

        response = self._get(self.member)

        self.assertNotContains(response, "Emergency contact")

    def test_open_onboarding_requirement_shows_as_a_banner(self):
        OnboardingRequirement.objects.create(club=self.club, name="Medical form")
        self.client.force_login(self.user)

        response = self._get(self.child)

        self.assertContains(response, "Medical form")
        self.assertContains(response, "still open")

    def test_resolved_requirement_does_not_show_in_the_banner(self):
        requirement = OnboardingRequirement.objects.create(club=self.club, name="Medical form")
        MemberRequirementStatus.objects.create(membership=self.child_membership, requirement=requirement, is_complete=True)
        self.client.force_login(self.user)

        response = self._get(self.child)

        self.assertNotContains(response, "still open")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "other-club.rosterchief.app", "testserver"])
class CalendarFeedViewTests(TestCase):
    """The .ics subscription feed -- URL-token authenticated, combined across
    every managed person, regardless of RSVP status."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.season = Season.objects.create(club=cls.club, start_date=timezone.localdate() - datetime.timedelta(days=30), end_date=timezone.localdate() + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)
        cls.token = CalendarFeedToken.objects.create(user=cls.user)

    def _get(self, token=None, host="ajax-united.rosterchief.app"):
        return self.client.get(f"/app/calendar/{token or self.token.token}.ics", HTTP_HOST=host)

    def test_no_login_required(self):
        event = Event.objects.create(club=self.club, title="Practice", start=timezone.now() + datetime.timedelta(days=1))
        Attendance.objects.create(event=event, member=self.member)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")

    def test_404_for_an_unknown_token(self):
        response = self._get(token="not-a-real-token")

        self.assertEqual(response.status_code, 404)

    def test_includes_events_regardless_of_rsvp_status(self):
        in_event = Event.objects.create(club=self.club, title="Confirmed", start=timezone.now() + datetime.timedelta(days=1))
        out_event = Event.objects.create(club=self.club, title="Declined", start=timezone.now() + datetime.timedelta(days=2))
        no_reply_event = Event.objects.create(club=self.club, title="No reply yet", start=timezone.now() + datetime.timedelta(days=3))
        Attendance.objects.create(event=in_event, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        Attendance.objects.create(event=out_event, member=self.member, status=Attendance.AttendanceStatus.ABSENT)
        Attendance.objects.create(event=no_reply_event, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)

        response = self._get()

        calendar = ICalCalendar.from_ical(response.content)
        summaries = {str(component.get("summary")) for component in calendar.walk("VEVENT")}
        self.assertEqual(summaries, {"Confirmed", "Declined", "No reply yet"})

    def test_includes_events_for_managed_children_with_their_name_in_the_summary(self):
        family = Family.objects.create(name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        event = Event.objects.create(club=self.club, title="Practice", start=timezone.now() + datetime.timedelta(days=1))
        Attendance.objects.create(event=event, member=child)

        response = self._get()

        calendar = ICalCalendar.from_ical(response.content)
        summaries = {str(component.get("summary")) for component in calendar.walk("VEVENT")}
        self.assertIn("Practice — Noor", summaries)

    def test_cancelled_events_stay_in_the_feed_marked_cancelled(self):
        event = Event.objects.create(club=self.club, title="Rained out", start=timezone.now() + datetime.timedelta(days=1), cancelled=True)
        Attendance.objects.create(event=event, member=self.member)

        response = self._get()

        calendar = ICalCalendar.from_ical(response.content)
        component = next(iter(calendar.walk("VEVENT")))
        self.assertEqual(str(component.get("status")), "CANCELLED")

    def test_excludes_events_that_already_ended(self):
        Event.objects.create(club=self.club, title="Yesterday", start=timezone.now() - datetime.timedelta(days=2))
        Attendance.objects.create(event=Event.objects.get(title="Yesterday"), member=self.member)

        response = self._get()

        calendar = ICalCalendar.from_ical(response.content)
        self.assertEqual(list(calendar.walk("VEVENT")), [])

    def test_keeps_a_still_in_progress_event(self):
        event = Event.objects.create(club=self.club, title="Right now", start=timezone.now() - datetime.timedelta(minutes=30), end=timezone.now() + datetime.timedelta(minutes=30))
        Attendance.objects.create(event=event, member=self.member)

        response = self._get()

        calendar = ICalCalendar.from_ical(response.content)
        summaries = {str(component.get("summary")) for component in calendar.walk("VEVENT")}
        self.assertIn("Right now", summaries)

    def test_scoped_to_the_club_the_url_is_fetched_on(self):
        # The same token/account can be a member of more than one club -- the
        # feed must only ever show whichever club's subdomain it was fetched
        # on, the same tenant scoping every other page in this app relies on.
        other_club = Club.objects.create(name="Other Club", slug="other-club", secondary_color="#e4002b")
        other_season = Season.objects.create(club=other_club, start_date=timezone.localdate() - datetime.timedelta(days=30), end_date=timezone.localdate() + datetime.timedelta(days=300))
        ClubMembership.objects.create(club=other_club, member=self.member, season=other_season)
        this_event = Event.objects.create(club=self.club, title="This club", start=timezone.now() + datetime.timedelta(days=1))
        other_event = Event.objects.create(club=other_club, title="Not this club", start=timezone.now() + datetime.timedelta(days=1))
        Attendance.objects.create(event=this_event, member=self.member)
        Attendance.objects.create(event=other_event, member=self.member)

        response = self._get(host="other-club.rosterchief.app")

        calendar = ICalCalendar.from_ical(response.content)
        summaries = {str(component.get("summary")) for component in calendar.walk("VEVENT")}
        self.assertEqual(summaries, {"Not this club"})


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CalendarFeedSettingsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", user=cls.user)

    def _get(self):
        return self.client.get(reverse("mobile:calendar_feed_settings"), HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_creates_a_token_on_first_visit_and_shows_its_url(self):
        self.client.force_login(self.user)

        response = self._get()

        token = CalendarFeedToken.objects.get(user=self.user)
        self.assertContains(response, f"{token.token}.ics")
        self.assertContains(response, "webcal://")

    def test_reset_issues_a_new_token_invalidating_the_old_one(self):
        self.client.force_login(self.user)
        old_token = CalendarFeedToken.objects.create(user=self.user)
        old_value = old_token.token

        response = self.client.post(reverse("mobile:calendar_feed_settings"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:calendar_feed_settings"), fetch_redirect_response=False)
        old_token.refresh_from_db()
        self.assertNotEqual(old_token.token, old_value)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachTodayViewTests(TestCase):
    """C1 -- design_handoff_rosterchief_platform/README.md's C1 section, plus
    mobile/coach_mixins.py's CoachScopeMixin (team resolution, session
    persistence, can_manage_active_team) exercised through this, the first
    Coach-mode screen."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

    def _get(self, **params):
        url = reverse("mobile:coach_today")
        if params:
            url += "?" + "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get(url, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_no_staff_assignment_shows_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not staffing a team yet")

    def test_shows_the_active_team_and_squad_count(self):
        other_member = Member.objects.create(first_name="Anna", last_name="Player")
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season)
        TeamMembership.objects.create(team=self.team, member=other_member, season=self.season)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "U16")
        self.assertEqual(response.context["squad_count"], 2)

    def test_defaults_to_the_first_staffed_team_with_no_prior_selection(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["active_team"], self.team)

    def test_team_query_param_switches_and_persists_the_active_team(self):
        second_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        StaffAssignment.objects.create(team=second_team, member=self.member, season=self.season, position=self.position)
        self.client.force_login(self.user)

        response = self._get(team=second_team.pk)
        self.assertEqual(response.context["active_team"], second_team)

        # No ?team= this time -- the session should keep it on the second team.
        response = self._get()
        self.assertEqual(response.context["active_team"], second_team)

    def test_tonights_session_card_shows_for_an_event_starting_today(self):
        # A small offset, not "+2 hours" -- late enough in the evening (this
        # environment is UTC+2), a couple hours out would roll into tomorrow
        # and silently break the "starts today" premise this test is about.
        event = Event.objects.create(club=self.club, title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(minutes=5))
        event.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["tonight_event"], event)
        self.assertContains(response, "Practice")

    def test_no_session_today_still_shows_the_next_upcoming_one(self):
        event = Event.objects.create(club=self.club, title="Next week", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=5))
        event.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertIsNone(response.context["tonight_event"])
        self.assertEqual(response.context["session_event"], event)
        self.assertNotContains(response, "Tonight")
        self.assertContains(response, "Next up")
        self.assertContains(response, "Next week")

    def test_a_session_with_an_end_time_stays_current_until_30_minutes_past_it(self):
        ongoing = Event.objects.create(
            club=self.club,
            title="Ongoing game",
            kind=Event.EventKind.GAME,
            start=timezone.now() - datetime.timedelta(hours=2),
            end=timezone.now() - datetime.timedelta(minutes=20),
        )
        ongoing.teams.add(self.team)
        later = Event.objects.create(club=self.club, title="Later practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=1))
        later.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["session_event"], ongoing)

    def test_a_session_with_an_end_time_switches_30_minutes_past_it(self):
        finished = Event.objects.create(
            club=self.club,
            title="Finished game",
            kind=Event.EventKind.GAME,
            start=timezone.now() - datetime.timedelta(hours=2),
            end=timezone.now() - datetime.timedelta(minutes=40),
        )
        finished.teams.add(self.team)
        later = Event.objects.create(club=self.club, title="Later practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=1))
        later.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["session_event"], later)

    def test_a_session_with_no_end_time_stays_current_until_90_minutes_past_start(self):
        ongoing = Event.objects.create(club=self.club, title="Ongoing practice", kind=Event.EventKind.TRAINING, start=timezone.now() - datetime.timedelta(minutes=80))
        ongoing.teams.add(self.team)
        later = Event.objects.create(club=self.club, title="Later practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=1))
        later.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["session_event"], ongoing)

    def test_a_session_with_no_end_time_switches_90_minutes_past_start(self):
        finished = Event.objects.create(club=self.club, title="Finished practice", kind=Event.EventKind.TRAINING, start=timezone.now() - datetime.timedelta(minutes=100))
        finished.teams.add(self.team)
        later = Event.objects.create(club=self.club, title="Later practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=1))
        later.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["session_event"], later)

    def test_silent_players_are_counted_and_listed_in_needs_you(self):
        other_member = Member.objects.create(first_name="Anna", last_name="Player")
        TeamMembership.objects.create(team=self.team, member=other_member, season=self.season)
        event = Event.objects.create(club=self.club, title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(minutes=5))
        event.teams.add(self.team)
        Attendance.objects.update_or_create(event=event, member=other_member, defaults={"status": Attendance.AttendanceStatus.NO_RESPONSE})
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["silent_count"], 1)
        self.assertContains(response, "Silent players")

    def test_out_players_are_counted(self):
        other_member = Member.objects.create(first_name="Anna", last_name="Player")
        TeamMembership.objects.create(team=self.team, member=other_member, season=self.season)
        event = Event.objects.create(club=self.club, title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(minutes=5))
        event.teams.add(self.team)
        Attendance.objects.update_or_create(event=event, member=other_member, defaults={"status": Attendance.AttendanceStatus.ABSENT})
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["out_count"], 1)
        self.assertContains(response, "Out")

    def test_check_attendance_cta_hidden_for_non_managing_staff(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        event = Event.objects.create(club=self.club, title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(minutes=5))
        event.teams.add(self.team)
        self.client.force_login(physio_user)

        response = self._get()

        self.assertFalse(response.context["can_manage_active_team"])
        self.assertNotContains(response, "Check attendance")

    def test_also_yours_card_shows_the_coachs_own_rsvp(self):
        event = Event.objects.create(club=self.club, title="My own game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=1))
        Attendance.objects.create(event=event, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Also yours")
        self.assertContains(response, "My own game")

    def test_unpublished_lineup_shows_in_needs_you_for_a_game_session(self):
        event = Event.objects.create(club=self.club, title="Big game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(minutes=5))
        event.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Line-up not published")

    def test_published_lineup_does_not_show_in_needs_you(self):
        event = Event.objects.create(club=self.club, title="Big game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(minutes=5))
        event.teams.add(self.team)
        Lineup.objects.create(event=event, team=self.team, published_at=timezone.now())
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "Line-up not published")

    def test_a_later_games_missing_lineup_shows_even_when_the_next_session_is_a_practice(self):
        # session_event (the very next thing on the calendar) is a practice --
        # the game further out still needs flagging, not just whatever's soonest.
        practice = Event.objects.create(club=self.club, title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(minutes=5))
        practice.teams.add(self.team)
        game = Event.objects.create(club=self.club, title="Saturday's game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=3))
        game.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["session_event"], practice)
        self.assertContains(response, "Saturday&#x27;s game")
        self.assertContains(response, "Line-up not published")

    def test_several_upcoming_games_each_missing_a_lineup_are_all_listed(self):
        first = Event.objects.create(club=self.club, title="Game one", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=1))
        first.teams.add(self.team)
        second = Event.objects.create(club=self.club, title="Game two", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=3))
        second.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Game one")
        self.assertContains(response, "Game two")
        self.assertContains(response, "Line-up not published", count=2)

    def test_missing_lineup_build_link_points_at_the_specific_game(self):
        game = Event.objects.create(club=self.club, title="Big game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=1))
        game.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, reverse("mobile:coach_lineup", kwargs={"event_id": game.pk}))

    def test_missing_lineup_check_is_capped(self):
        cap = CoachTodayView.UPCOMING_GAMES_CHECKED
        for day in range(cap + 1):
            game = Event.objects.create(club=self.club, title=f"Game {day}", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=day + 1))
            game.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Line-up not published", count=cap)

    def test_header_shows_the_persons_actual_role_not_a_hardcoded_label(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Head coach")
        self.assertEqual(response.context["active_team_role"], self.position)

    def test_header_role_reflects_a_non_coaching_staff_position(self):
        physio_position = Position.objects.create(club=self.club, name="Team manager", short_name="TM", staff_position=True, management_position=True)
        physio_user = User.objects.create_user(email="tm@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Tom", last_name="Manager", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._get()

        self.assertContains(response, "Team manager")
        self.assertNotContains(response, "Head coach")

    def test_tab_bar_has_no_me_tab(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, reverse("mobile:me"))

    def test_tab_bar_links_to_squad_and_schedule(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, reverse("mobile:coach_squad"))
        self.assertContains(response, reverse("mobile:coach_schedule"))

    def test_add_menu_hidden_for_non_managing_staff(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._get()

        self.assertNotContains(response, reverse("mobile:coach_create_event"))
        self.assertNotContains(response, reverse("mobile:coach_create_news"))
        self.assertNotContains(response, reverse("mobile:coach_add_player"))


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachSquadViewTests(TestCase):
    """Bottom-tab "Squad" -- roster + staff for the active team."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

    def _get(self):
        return self.client.get(reverse("mobile:coach_squad"), HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_lists_roster_and_staff(self):
        player_position = Position.objects.create(club=self.club, name="Forward", short_name="FW", staff_position=False)
        player = Member.objects.create(first_name="Anna", last_name="Player")
        membership = TeamMembership.objects.create(team=self.team, member=player, season=self.season, position=player_position, jersey_number=9)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Anna Player")
        self.assertContains(response, "Forward")
        self.assertContains(response, "Sam Coach")
        self.assertContains(response, "Head coach")
        self.assertContains(response, reverse("mobile:coach_roster_member", kwargs={"membership_pk": membership.pk}))

    def test_add_links_hidden_for_non_managing_staff(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._get()

        self.assertNotContains(response, reverse("mobile:coach_add_player"))
        self.assertNotContains(response, reverse("mobile:coach_add_staff"))

    def test_add_staff_link_shown_for_managing_staff(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, reverse("mobile:coach_add_staff"))

    def test_no_staff_assignment_shows_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not staffing a team yet")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachRosterMemberViewTests(TestCase):
    """Squad screen's per-player detail sheet -- stats, tap-to-call, edit,
    remove. See CoachRosterMemberView's own docstring."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.coach_position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.coach_position)

        cls.player_position = Position.objects.create(club=cls.club, name="Forward", short_name="FW", staff_position=False)
        cls.player = Member.objects.create(first_name="Anna", last_name="Player", phone="+3247" "1234567", emergency_phone="+3247" "7654321")
        cls.membership = TeamMembership.objects.create(team=cls.team, member=cls.player, season=cls.season, position=cls.player_position, jersey_number=9)

    def _get(self, membership=None):
        membership = membership or self.membership
        return self.client.get(reverse("mobile:coach_roster_member", kwargs={"membership_pk": membership.pk}), HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_shows_the_players_own_call_buttons(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, f"tel:{self.player.phone.as_international}")
        self.assertContains(response, f"tel:{self.player.emergency_phone.as_international}")

    def test_shows_a_guardians_call_buttons_for_a_child(self):
        family = Family.objects.create(name="Player family")
        guardian = Member.objects.create(first_name="Gail", last_name="Guardian", phone="+3247" "1112222")
        FamilyMembership.objects.create(family=family, member=guardian, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=self.player, role=FamilyMembership.FamilyRole.CHILD)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, f"tel:{guardian.phone.as_international}")
        self.assertContains(response, "Gail Guardian")

    def test_shows_attendance_counts(self):
        past_event = Event.objects.create(club=self.club, title="Past practice", kind=Event.EventKind.TRAINING, start=timezone.now() - datetime.timedelta(days=2), season=self.season)
        past_event.teams.add(self.team)
        Attendance.objects.update_or_create(event=past_event, member=self.player, defaults={"status": Attendance.AttendanceStatus.PRESENT})
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["attendance_counts"]["present"], 1)

    def test_shows_no_shows(self):
        past_event = Event.objects.create(club=self.club, title="Past practice", kind=Event.EventKind.TRAINING, start=timezone.now() - datetime.timedelta(days=2), season=self.season)
        past_event.teams.add(self.team)
        attendance, _created = Attendance.objects.update_or_create(event=past_event, member=self.player, defaults={"status": Attendance.AttendanceStatus.PRESENT})
        record_check_in(attendance, showed_up=False)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["attendance_counts"]["no_shows"], 1)
        self.assertContains(response, "No-shows")

    def test_managing_staff_sees_the_edit_form_and_remove_button(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertIsNotNone(response.context["form"])
        self.assertContains(response, "Remove from roster")

    def test_non_managing_staff_sees_a_read_only_view(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._get()

        self.assertIsNone(response.context["form"])
        self.assertNotContains(response, "Remove from roster")

    def test_a_membership_from_another_team_is_not_reachable(self):
        other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        other_membership = TeamMembership.objects.create(team=other_team, member=Member.objects.create(first_name="Other", last_name="Team"), season=self.season)
        self.client.force_login(self.user)

        response = self._get(other_membership)

        self.assertEqual(response.status_code, 404)

    def test_post_updates_position_jersey_and_captaincy(self):
        new_position = Position.objects.create(club=self.club, name="Midfielder", short_name="MF", staff_position=False)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mobile:coach_roster_member", kwargs={"membership_pk": self.membership.pk}),
            {"position": new_position.pk, "jersey_number": "11", "is_captain": "on"},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertRedirects(response, reverse("mobile:coach_roster_member", kwargs={"membership_pk": self.membership.pk}), fetch_redirect_response=False)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.position, new_position)
        self.assertEqual(self.membership.jersey_number, 11)
        self.assertTrue(self.membership.is_captain)

    def test_post_rejects_a_clashing_jersey_number(self):
        TeamMembership.objects.create(team=self.team, member=Member.objects.create(first_name="Other", last_name="Player"), season=self.season, jersey_number=7)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mobile:coach_roster_member", kwargs={"membership_pk": self.membership.pk}),
            {"position": self.player_position.pk, "jersey_number": "7"},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertEqual(response.status_code, 200)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.jersey_number, 9)

    def test_non_managing_staff_cannot_post(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio2@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self.client.post(
            reverse("mobile:coach_roster_member", kwargs={"membership_pk": self.membership.pk}),
            {"position": self.player_position.pk, "jersey_number": "99"},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertEqual(response.status_code, 403)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.jersey_number, 9)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachRosterRemoveViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.coach_position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.coach_position)
        cls.player = Member.objects.create(first_name="Anna", last_name="Player")
        cls.membership = TeamMembership.objects.create(team=cls.team, member=cls.player, season=cls.season)

    def test_removes_the_membership(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("mobile:coach_roster_remove", kwargs={"membership_pk": self.membership.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_squad"), fetch_redirect_response=False)
        self.assertFalse(TeamMembership.objects.filter(pk=self.membership.pk).exists())

    def test_non_managing_staff_cannot_remove(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self.client.post(reverse("mobile:coach_roster_remove", kwargs={"membership_pk": self.membership.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(TeamMembership.objects.filter(pk=self.membership.pk).exists())


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachStaffRemoveViewTests(TestCase):
    """Squad screen's per-staff-row remove action -- see
    CoachStaffRemoveView's own docstring for why self-removal is refused."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.coach_position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        cls.own_assignment = StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.coach_position)

        cls.assistant_position = Position.objects.create(club=cls.club, name="Assistant coach", short_name="AC", staff_position=True, management_position=False)
        cls.assistant = Member.objects.create(first_name="Ali", last_name="Assistant")
        cls.assignment = StaffAssignment.objects.create(team=cls.team, member=cls.assistant, season=cls.season, position=cls.assistant_position)

    def _post(self, assignment):
        return self.client.post(reverse("mobile:coach_staff_remove", kwargs={"assignment_pk": assignment.pk}), HTTP_HOST="ajax-united.rosterchief.app")

    def test_removes_another_staff_member(self):
        self.client.force_login(self.user)

        response = self._post(self.assignment)

        self.assertRedirects(response, reverse("mobile:coach_squad"), fetch_redirect_response=False)
        self.assertFalse(StaffAssignment.objects.filter(pk=self.assignment.pk).exists())

    def test_cannot_remove_yourself(self):
        self.client.force_login(self.user)

        response = self._post(self.own_assignment)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(StaffAssignment.objects.filter(pk=self.own_assignment.pk).exists())

    def test_non_managing_staff_cannot_remove_anyone(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._post(self.assignment)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(StaffAssignment.objects.filter(pk=self.assignment.pk).exists())

    def test_the_remove_button_is_hidden_on_your_own_row(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_squad"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, reverse("mobile:coach_staff_remove", kwargs={"assignment_pk": self.own_assignment.pk}))
        self.assertContains(response, reverse("mobile:coach_staff_remove", kwargs={"assignment_pk": self.assignment.pk}))

    def test_an_assignment_from_another_team_is_not_reachable(self):
        other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        other_position = Position.objects.create(club=self.club, name="Coach", short_name="C", staff_position=True, management_position=True)
        other_assignment = StaffAssignment.objects.create(team=other_team, member=Member.objects.create(first_name="Other", last_name="Team"), season=self.season, position=other_position)
        self.client.force_login(self.user)

        response = self._post(other_assignment)

        self.assertEqual(response.status_code, 404)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachAddStaffViewTests(TestCase):
    """Squad screen's staff "Add" entry point -- see CoachAddStaffView's own
    docstring for the shared-position-picker shape."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.coach_position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.coach_position)
        cls.assistant_position = Position.objects.create(club=cls.club, name="Assistant coach", short_name="AC", staff_position=True, management_position=False)

    def make_eligible_member(self, first_name="Anna", last_name="Player"):
        member = Member.objects.create(first_name=first_name, last_name=last_name)
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE, kind=ClubMembership.Kind.MEMBER)
        return member

    def test_requires_login(self):
        response = self.client.get(reverse("mobile:coach_add_staff"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 302)

    def test_the_squad_tab_is_highlighted(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_staff"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.context["active_tab"], "coach_squad")

    def test_get_redirects_a_non_managing_staffer(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self.client.get(reverse("mobile:coach_add_staff"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_squad"), fetch_redirect_response=False)

    def test_lists_eligible_members_not_already_staffing_this_team(self):
        eligible = self.make_eligible_member()
        already_staffing = self.make_eligible_member(first_name="Already", last_name="Staffing")
        StaffAssignment.objects.create(team=self.team, member=already_staffing, season=self.season, position=self.assistant_position)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_staff"), HTTP_HOST="ajax-united.rosterchief.app")

        candidates = response.context["candidates"]
        self.assertIn(eligible, candidates)
        self.assertNotIn(already_staffing, candidates)
        self.assertIn(self.assistant_position, response.context["positions"])

    def test_post_assigns_selected_members_to_the_chosen_position(self):
        first = self.make_eligible_member(first_name="First", last_name="Pick")
        second = self.make_eligible_member(first_name="Second", last_name="Pick")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mobile:coach_add_staff"),
            {"position": str(self.assistant_position.pk), "member": [str(first.pk), str(second.pk)]},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertRedirects(response, reverse("mobile:coach_squad"), fetch_redirect_response=False)
        self.assertTrue(StaffAssignment.objects.filter(team=self.team, season=self.season, member=first, position=self.assistant_position).exists())
        self.assertTrue(StaffAssignment.objects.filter(team=self.team, season=self.season, member=second, position=self.assistant_position).exists())

    def test_post_without_a_position_is_rejected(self):
        candidate = self.make_eligible_member()
        self.client.force_login(self.user)

        response = self.client.post(reverse("mobile:coach_add_staff"), {"member": [str(candidate.pk)]}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_add_staff"), fetch_redirect_response=False)
        self.assertFalse(StaffAssignment.objects.filter(team=self.team, member=candidate).exists())

    def test_post_ignores_a_member_id_outside_the_eligible_pool(self):
        ineligible = Member.objects.create(first_name="Not", last_name="Eligible")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mobile:coach_add_staff"),
            {"position": str(self.assistant_position.pk), "member": [str(ineligible.pk)]},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertRedirects(response, reverse("mobile:coach_squad"), fetch_redirect_response=False)
        self.assertFalse(StaffAssignment.objects.filter(team=self.team, member=ineligible).exists())

    def test_non_managing_staff_cannot_post(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio2@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        candidate = self.make_eligible_member()
        self.client.force_login(physio_user)

        response = self.client.post(
            reverse("mobile:coach_add_staff"),
            {"position": str(self.assistant_position.pk), "member": [str(candidate.pk)]},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(StaffAssignment.objects.filter(team=self.team, member=candidate).exists())


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachScheduleViewTests(TestCase):
    """Bottom-tab "Schedule" -- every upcoming event for the active team,
    each row routed to the coach-relevant action for its kind."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

    def _get(self):
        return self.client.get(reverse("mobile:coach_schedule"), HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_game_row_links_to_lineup(self):
        game = Event.objects.create(club=self.club, title="Big game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=2))
        game.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, reverse("mobile:coach_lineup", kwargs={"event_id": game.pk}))

    def test_training_row_links_to_attendance(self):
        practice = Event.objects.create(club=self.club, title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=2))
        practice.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, reverse("mobile:coach_attendance", kwargs={"event_id": practice.pk}))

    def test_past_and_cancelled_events_are_excluded(self):
        past = Event.objects.create(club=self.club, title="Old practice", kind=Event.EventKind.TRAINING, start=timezone.now() - datetime.timedelta(days=2))
        past.teams.add(self.team)
        cancelled = Event.objects.create(club=self.club, title="Cancelled game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=2), cancelled=True)
        cancelled.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["this_week"], [])
        self.assertEqual(response.context["next_week"], [])
        self.assertEqual(response.context["later_months"], [])

    def test_no_staff_assignment_shows_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not staffing a team yet")

    def test_events_are_grouped_into_this_week_next_week_and_month_dividers(self):
        _this_week_start, this_week_end = week_bounds(timezone.localdate())
        this_week_event = Event.objects.create(club=self.club, title="This week practice", kind=Event.EventKind.TRAINING, start=timezone.make_aware(datetime.datetime.combine(this_week_end, datetime.time(18, 0))))
        this_week_event.teams.add(self.team)
        far_future = Event.objects.create(club=self.club, title="Far future practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=60))
        far_future.teams.add(self.team)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual([event.title for event in response.context["this_week"]], ["This week practice"])
        later_months = response.context["later_months"]
        self.assertEqual(len(later_months), 1)
        self.assertEqual([event.title for event in later_months[0]["items"]], ["Far future practice"])
        self.assertContains(response, "This week")
        self.assertContains(response, far_future.start.strftime("%B %Y"))


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachAttendanceViewTests(TestCase):
    """C2 -- bench attendance: events.services.attendance.record_check_in
    written through for the first time anywhere in the codebase."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

        cls.player = Member.objects.create(first_name="Anna", last_name="Player")
        cls.player_membership = TeamMembership.objects.create(team=cls.team, member=cls.player, season=cls.season, jersey_number=9)
        cls.event = Event.objects.create(club=cls.club, title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(hours=2))
        cls.event.teams.add(cls.team)
        cls.attendance, _created = Attendance.objects.update_or_create(event=cls.event, member=cls.player, defaults={"status": Attendance.AttendanceStatus.PRESENT})

    def _get(self, **params):
        url = reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk})
        if params:
            url += "?" + "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get(url, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_shows_the_roster_with_jersey_numbers(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Anna Player")
        self.assertContains(response, "9")

    def test_the_schedule_tab_is_highlighted(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.context["active_tab"], "coach_schedule")

    def test_shows_a_maybe_reason_alongside_an_absent_one(self):
        self.attendance.status = Attendance.AttendanceStatus.MAYBE
        self.attendance.note = "Might be a few minutes late"
        self.attendance.save()
        self.client.force_login(self.user)

        response = self._get()

        self.assertContains(response, "Might be a few minutes late")

    def test_save_records_check_ins_via_record_check_in(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk}),
            {f"showed_up_{self.attendance.pk}": "true"},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)
        self.attendance.refresh_from_db()
        self.assertTrue(self.attendance.showed_up)

    def test_non_managing_staff_cannot_save(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self.client.post(
            reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk}),
            {f"showed_up_{self.attendance.pk}": "true"},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertEqual(response.status_code, 403)
        self.attendance.refresh_from_db()
        self.assertIsNone(self.attendance.showed_up)

    def test_non_managing_staff_sees_a_read_only_view(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio2@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._get()

        self.assertNotContains(response, "Save attendance")

    def test_silent_filter_narrows_to_no_response_rows(self):
        silent_member = Member.objects.create(first_name="Ben", last_name="Silent")
        TeamMembership.objects.create(team=self.team, member=silent_member, season=self.season)
        Attendance.objects.update_or_create(event=self.event, member=silent_member, defaults={"status": Attendance.AttendanceStatus.NO_RESPONSE})
        self.client.force_login(self.user)

        response = self._get(filter="silent")

        rows = response.context["rows"]
        self.assertEqual([row.member for row in rows], [silent_member])

    def test_default_view_shows_only_those_expected_to_attend(self):
        # cls.attendance (Anna) is PRESENT -- shown. A silent and a declined
        # member are both left out of the default, since neither is expected
        # to show up -- see CoachAttendanceView's own docstring.
        silent_member = Member.objects.create(first_name="Ben", last_name="Silent")
        TeamMembership.objects.create(team=self.team, member=silent_member, season=self.season)
        Attendance.objects.update_or_create(event=self.event, member=silent_member, defaults={"status": Attendance.AttendanceStatus.NO_RESPONSE})
        declined_member = Member.objects.create(first_name="Cara", last_name="Declined")
        TeamMembership.objects.create(team=self.team, member=declined_member, season=self.season)
        Attendance.objects.update_or_create(event=self.event, member=declined_member, defaults={"status": Attendance.AttendanceStatus.ABSENT})
        self.client.force_login(self.user)

        response = self._get()

        rows = response.context["rows"]
        self.assertEqual([row.member for row in rows], [self.player])
        self.assertEqual(response.context["responded_count"], 1)

    def test_declined_filter_narrows_to_declined_rows(self):
        declined_member = Member.objects.create(first_name="Cara", last_name="Declined")
        TeamMembership.objects.create(team=self.team, member=declined_member, season=self.season)
        Attendance.objects.update_or_create(event=self.event, member=declined_member, defaults={"status": Attendance.AttendanceStatus.ABSENT})
        self.client.force_login(self.user)

        response = self._get(filter="declined")

        rows = response.context["rows"]
        self.assertEqual([row.member for row in rows], [declined_member])
        self.assertEqual(response.context["declined_count"], 1)

    def test_the_goalies_chip_is_gone(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotContains(response, "Goalies")
        self.assertContains(response, "Responded")
        self.assertContains(response, "Declined")

    def test_silent_rows_have_no_special_background(self):
        silent_member = Member.objects.create(first_name="Ben", last_name="Silent")
        TeamMembership.objects.create(team=self.team, member=silent_member, season=self.season)
        Attendance.objects.update_or_create(event=self.event, member=silent_member, defaults={"status": Attendance.AttendanceStatus.NO_RESPONSE})
        self.client.force_login(self.user)

        response = self._get(filter="silent")

        self.assertNotContains(response, "bg-warn-bg")

    def test_event_from_another_team_is_not_reachable(self):
        other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        other_event = Event.objects.create(club=self.club, title="Other practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(hours=2))
        other_event.teams.add(other_team)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_attendance", kwargs={"event_id": other_event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachAttendanceRemindSilentViewTests(TestCase):
    """Attendance sheet's "Remind silent" button -- see
    CoachAttendanceRemindSilentView's own docstring."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

        cls.silent_member = Member.objects.create(first_name="Ben", last_name="Silent")
        TeamMembership.objects.create(team=cls.team, member=cls.silent_member, season=cls.season)
        cls.event = Event.objects.create(club=cls.club, title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(hours=2))
        cls.event.teams.add(cls.team)
        Attendance.objects.update_or_create(event=cls.event, member=cls.silent_member, defaults={"status": Attendance.AttendanceStatus.NO_RESPONSE})

    def _post(self, event=None):
        event = event or self.event
        return self.client.post(reverse("mobile:coach_attendance_remind_silent", kwargs={"event_id": event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._post()

        self.assertEqual(response.status_code, 302)

    def test_notifies_every_silent_member(self):
        self.client.force_login(self.user)

        response = self._post()

        self.assertRedirects(response, reverse("mobile:coach_attendance", kwargs={"event_id": self.event.pk}), fetch_redirect_response=False)
        self.assertTrue(Notification.objects.filter(club=self.club, member=self.silent_member, title=self.event.title).exists())

    def test_does_not_notify_someone_who_already_responded(self):
        responded_member = Member.objects.create(first_name="Anna", last_name="Player")
        TeamMembership.objects.create(team=self.team, member=responded_member, season=self.season)
        Attendance.objects.update_or_create(event=self.event, member=responded_member, defaults={"status": Attendance.AttendanceStatus.PRESENT})
        self.client.force_login(self.user)

        self._post()

        # Filtered by title (the reminder's own, matching self.event.title) rather
        # than just member= -- creating the TeamMembership above also fires
        # notify_newly_invited's own, differently-titled "new events" push, which
        # a plain member= filter would otherwise conflate with this one.
        self.assertFalse(Notification.objects.filter(member=responded_member, title=self.event.title).exists())

    def test_no_silent_members_is_a_no_op(self):
        Attendance.objects.filter(event=self.event).update(status=Attendance.AttendanceStatus.PRESENT)
        self.client.force_login(self.user)

        self._post()

        self.assertFalse(Notification.objects.filter(club=self.club).exists())

    def test_non_managing_staff_cannot_send_a_reminder(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._post()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Notification.objects.filter(member=self.silent_member).exists())

    def test_event_from_another_team_is_not_reachable(self):
        other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        other_event = Event.objects.create(club=self.club, title="Other practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(hours=2))
        other_event.teams.add(other_team)
        self.client.force_login(self.user)

        response = self._post(other_event)

        self.assertEqual(response.status_code, 404)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachLocationCreateViewTests(TestCase):
    """New event's "+ New location" popup -- see CoachLocationCreateView's
    own docstring for the primary-target/out-of-band-swap shape."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

    def _post(self, **overrides):
        data = {"name": "Sportoase", "address": "1 Main St", "city": "Antwerp", "zip_code": "2000", "country": "BE"}
        data.update(overrides)
        return self.client.post(reverse("mobile:coach_location_create"), data, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._post()

        self.assertEqual(response.status_code, 302)

    def test_non_managing_staff_cannot_create_a_location(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._post()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Location.objects.filter(name="Sportoase").exists())

    def test_creates_a_location_scoped_to_the_club(self):
        self.client.force_login(self.user)

        response = self._post()

        location = Location.objects.get(name="Sportoase")
        self.assertEqual(location.club, self.club)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "location-created")

    def test_success_response_carries_an_out_of_band_picker_with_it_selected(self):
        self.client.force_login(self.user)

        response = self._post()

        location = Location.objects.get(name="Sportoase")
        body = response.content.decode()
        self.assertIn('id="location-picker"', body)
        self.assertIn("hx-swap-oob", body)
        self.assertIn(f'value="{location.pk}" selected', body)

    def test_invalid_submission_reshows_the_modal_fields_with_errors(self):
        self.client.force_login(self.user)

        response = self._post(name="")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Trigger", response)
        self.assertFalse(Location.objects.filter(city="Antwerp").exists())
        self.assertContains(response, "This field is required")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachOpponentCreateViewTests(TestCase):
    """New event's "+ New opponent" popup -- same shape as
    CoachLocationCreateViewTests, for Opponent instead."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

    def _post(self, **overrides):
        data = {"name": "Rival FC"}
        data.update(overrides)
        return self.client.post(reverse("mobile:coach_opponent_create"), data, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self._post()

        self.assertEqual(response.status_code, 302)

    def test_non_managing_staff_cannot_create_an_opponent(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._post()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Opponent.objects.filter(name="Rival FC").exists())

    def test_creates_an_opponent_scoped_to_the_club_without_a_logo_field(self):
        self.client.force_login(self.user)

        response = self._post()

        opponent = Opponent.objects.get(name="Rival FC")
        self.assertEqual(opponent.club, self.club)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "opponent-created")
        self.assertNotContains(response, 'name="logo"')

    def test_success_response_carries_an_out_of_band_picker_with_it_selected(self):
        self.client.force_login(self.user)

        response = self._post()

        opponent = Opponent.objects.get(name="Rival FC")
        body = response.content.decode()
        self.assertIn('id="opponent-picker"', body)
        self.assertIn("hx-swap-oob", body)
        self.assertIn(f'value="{opponent.pk}" selected', body)

    def test_invalid_submission_reshows_the_modal_fields_with_errors(self):
        self.client.force_login(self.user)

        response = self._post(name="")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Trigger", response)
        self.assertFalse(Opponent.objects.exists())
        self.assertContains(response, "This field is required")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachCreateEventViewTests(TestCase):
    """C4 -- reuses management.forms.EventForm as-is; see CoachCreateEventView's
    own docstring for what's scoped down from the design mock."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

    def _post(self, **overrides):
        start = timezone.localtime(timezone.now() + datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M")
        data = {"kind": "training", "title": "Extra practice", "start": start, "teams": [str(self.team.pk)]}
        data.update(overrides)
        return self.client.post(reverse("mobile:coach_create_event"), data, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 302)

    def test_get_redirects_a_non_managing_staffer(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)

    def test_teams_field_is_scoped_to_managed_teams(self):
        other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        team_choices = list(response.context["form"].fields["teams"].queryset)
        self.assertEqual(team_choices, [self.team])
        self.assertNotIn(other_team, team_choices)

    def test_valid_post_creates_the_event_and_redirects(self):
        self.client.force_login(self.user)

        response = self._post(title="Extra practice")

        event = Event.objects.get(title="Extra practice")
        self.assertEqual(event.club, self.club)
        self.assertEqual(event.kind, Event.EventKind.TRAINING)
        self.assertEqual(event.created_by, self.member)
        self.assertIn(self.team, event.teams.all())
        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)

    def test_non_managing_staff_cannot_post(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._post(title="Blocked practice")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Event.objects.filter(title="Blocked practice").exists())

    def test_kind_choices_are_limited_to_the_tile_picker(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        kind_choices = {value for value, _label in response.context["form"].fields["kind"].choices}
        self.assertEqual(kind_choices, {"training", "game", "tournament", "meeting"})

    def test_can_create_a_tournament(self):
        self.client.force_login(self.user)

        self._post(kind="tournament", title="Regional tournament")

        event = Event.objects.get(title="Regional tournament")
        self.assertEqual(event.kind, Event.EventKind.TOURNAMENT)

    def test_can_create_a_meeting(self):
        self.client.force_login(self.user)

        self._post(kind="meeting", title="Team meeting")

        event = Event.objects.get(title="Team meeting")
        self.assertEqual(event.kind, Event.EventKind.MEETING)

    def test_other_and_social_are_rejected(self):
        self.client.force_login(self.user)

        for kind in ("other", "social"):
            with self.subTest(kind=kind):
                response = self._post(kind=kind, title=f"Not a {kind} event")

                self.assertEqual(response.status_code, 200)
                self.assertFalse(Event.objects.filter(title=f"Not a {kind} event").exists())

    def test_teams_is_hidden_and_locked_to_the_active_team(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertIsInstance(response.context["form"].fields["teams"].widget, forms.MultipleHiddenInput)
        self.assertEqual(response.context["form"].initial["teams"], [self.team.pk])
        self.assertContains(response, f'<input type="hidden" name="teams" value="{self.team.pk}"')
        self.assertNotContains(response, 'name="teams" type="checkbox"')

    def test_kind_defaults_to_training_not_the_model_default(self):
        # Event.kind's own model default is "other", which isn't even one of
        # the four tiles this screen offers -- regression check for the same
        # "unsaved instance already set form.initial" trap teams hits above.
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.context["form"].initial.get("kind"), "training")
        self.assertContains(response, "kind: 'training'")

    def test_groups_and_club_wide_are_not_offered(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotIn("groups", response.context["form"].fields)
        self.assertNotIn("club_wide", response.context["form"].fields)

    def test_can_set_opponent_and_competition_for_a_game(self):
        opponent = Opponent.objects.create(club=self.club, name="Rival FC")
        Competition.objects.create(name="Regional League", module="none")
        self.client.force_login(self.user)

        self._post(kind="game", title="Away game", opponent=str(opponent.pk), competition="Regional League")

        event = Event.objects.get(title="Away game")
        self.assertEqual(event.opponent, opponent)
        self.assertEqual(event.competition, "Regional League")

    def test_can_set_a_competition_id_for_a_game(self):
        self.client.force_login(self.user)

        self._post(kind="game", title="Away game", external_game_id="4460")

        event = Event.objects.get(title="Away game")
        self.assertEqual(event.external_game_id, "4460")

    def test_competition_id_is_optional(self):
        self.client.force_login(self.user)

        self._post(title="Plain practice")

        event = Event.objects.get(title="Plain practice")
        self.assertEqual(event.external_game_id, "")

    def test_can_set_a_gathering_time(self):
        self.client.force_login(self.user)
        gathering = timezone.localtime(timezone.now() + datetime.timedelta(days=5, hours=-1)).strftime("%Y-%m-%dT%H:%M")

        self._post(title="Early gather practice", gathering=gathering)

        event = Event.objects.get(title="Early gather practice")
        self.assertIsNotNone(event.gathering)

    def test_gathering_time_is_optional(self):
        self.client.force_login(self.user)

        self._post(title="No gather practice")

        event = Event.objects.get(title="No gather practice")
        self.assertIsNone(event.gathering)

    def test_recurring_can_set_a_gathering_offset(self):
        self.client.force_login(self.user)
        dtstart = timezone.localtime(timezone.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

        self.client.post(
            reverse("mobile:coach_create_event"),
            {
                "is_recurring": "on",
                "kind": "training",
                "title": "Gathering series",
                "teams": [str(self.team.pk)],
                "dtstart": dtstart,
                "frequency": "weekly",
                "interval": "1",
                "weekdays": ["MO"],
                "gathering_minutes_before": "30",
            },
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        series = EventSeries.objects.get(title="Gathering series")
        self.assertEqual(series.gathering_offset, datetime.timedelta(minutes=30))
        occurrence = series.occurrences.first()
        self.assertIsNotNone(occurrence)
        self.assertEqual(occurrence.gathering, occurrence.start - datetime.timedelta(minutes=30))

    def test_invited_members_pool_excludes_the_current_roster(self):
        on_roster = Member.objects.create(first_name="On", last_name="Roster")
        TeamMembership.objects.create(team=self.team, member=on_roster, season=self.season)
        off_roster = Member.objects.create(first_name="Off", last_name="Roster")
        ClubMembership.objects.create(club=self.club, member=off_roster, season=self.season, status=ClubMembership.StatusChoices.ACTIVE, kind=ClubMembership.Kind.MEMBER)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        pool = response.context["form"].fields["invited_members"].queryset
        self.assertIn(off_roster, pool)
        self.assertNotIn(on_roster, pool)

    def test_excluded_members_pool_is_the_current_roster(self):
        on_roster = Member.objects.create(first_name="On", last_name="Roster")
        TeamMembership.objects.create(team=self.team, member=on_roster, season=self.season)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        pool = response.context["form"].fields["excluded_members"].queryset
        self.assertIn(on_roster, pool)

    def test_invited_and_excluded_members_actually_render_as_checkboxes(self):
        # Regression: swapping a ModelMultipleChoiceField's widget *after*
        # setting its queryset silently drops the choices the queryset-setter
        # already pushed onto the old widget -- the checkbox list would
        # render completely empty despite the queryset (and POST handling)
        # being correct, so a plain queryset-only assertion (like the two
        # tests above) can't catch this. See _scope_shared_fields' own
        # comment for the mechanism.
        on_roster = Member.objects.create(first_name="On", last_name="Roster")
        TeamMembership.objects.create(team=self.team, member=on_roster, season=self.season)
        off_roster = Member.objects.create(first_name="Off", last_name="Roster")
        ClubMembership.objects.create(club=self.club, member=off_roster, season=self.season, status=ClubMembership.StatusChoices.ACTIVE, kind=ClubMembership.Kind.MEMBER)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "On Roster")
        self.assertContains(response, "Off Roster")

    def test_can_invite_an_extra_member_and_exclude_a_roster_member(self):
        rostered = Member.objects.create(first_name="Rostered", last_name="Player")
        TeamMembership.objects.create(team=self.team, member=rostered, season=self.season)
        guest = Member.objects.create(first_name="Guest", last_name="Player")
        ClubMembership.objects.create(club=self.club, member=guest, season=self.season, status=ClubMembership.StatusChoices.ACTIVE, kind=ClubMembership.Kind.MEMBER)
        self.client.force_login(self.user)

        self._post(title="Call-up practice", excluded_members=[str(rostered.pk)], invited_members=[str(guest.pk)])

        event = Event.objects.get(title="Call-up practice")
        self.assertFalse(Attendance.objects.filter(event=event, member=rostered).exists())
        self.assertTrue(Attendance.objects.filter(event=event, member=guest).exists())

    def test_valid_post_notifies_the_invited_roster(self):
        rostered = Member.objects.create(first_name="Rostered", last_name="Player", email="rostered@example.com")
        TeamMembership.objects.create(team=self.team, member=rostered, season=self.season)
        self.client.force_login(self.user)

        response = self._post(title="Notify practice")

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)
        event = Event.objects.get(title="Notify practice")
        self.assertTrue(Notification.objects.filter(member=rostered, title=event.title).exists())

    def test_weekday_checkboxes_actually_render(self):
        # Same widget-swap-after-choices trap as invited/excluded_members
        # above, for the plain MultipleChoiceField shape of it -- see
        # build_series_form's own comment.
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_event"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, 'value="MO"')
        self.assertContains(response, 'value="SU"')

    def test_recurring_post_creates_a_series_with_occurrences(self):
        self.client.force_login(self.user)
        dtstart = timezone.localtime(timezone.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

        response = self.client.post(
            reverse("mobile:coach_create_event"),
            {
                "is_recurring": "on",
                "kind": "training",
                "title": "Weekly practice",
                "teams": [str(self.team.pk)],
                "dtstart": dtstart,
                "frequency": "weekly",
                "interval": "1",
                "weekdays": ["MO", "WE"],
            },
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)
        series = EventSeries.objects.get(title="Weekly practice")
        self.assertEqual(series.club, self.club)
        self.assertIn(self.team, series.teams.all())
        self.assertTrue(series.occurrences.exists())

    def test_recurring_series_does_not_send_a_new_event_notification(self):
        rostered = Member.objects.create(first_name="Rostered", last_name="Player", email="rostered@example.com")
        TeamMembership.objects.create(team=self.team, member=rostered, season=self.season)
        self.client.force_login(self.user)
        dtstart = timezone.localtime(timezone.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

        self.client.post(
            reverse("mobile:coach_create_event"),
            {
                "is_recurring": "on",
                "kind": "training",
                "title": "Silent series",
                "teams": [str(self.team.pk)],
                "dtstart": dtstart,
                "frequency": "weekly",
                "interval": "1",
                "weekdays": ["MO"],
            },
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        # Matches management.views.EventSeriesCreateView's own behaviour --
        # not a mobile-specific gap. Occurrences still get their own
        # attendance rows (so there's something to notify about later via
        # send_deadline_reminders), just no immediate per-occurrence push.
        self.assertFalse(Notification.objects.filter(member=rostered).exists())
        series = EventSeries.objects.get(title="Silent series")
        occurrence = series.occurrences.first()
        self.assertIsNotNone(occurrence)
        self.assertTrue(Attendance.objects.filter(event=occurrence, member=rostered).exists())

    def test_recurring_weekly_requires_a_weekday(self):
        self.client.force_login(self.user)
        dtstart = timezone.localtime(timezone.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

        response = self.client.post(
            reverse("mobile:coach_create_event"),
            {"is_recurring": "on", "kind": "training", "title": "No weekday", "teams": [str(self.team.pk)], "dtstart": dtstart, "frequency": "weekly", "interval": "1"},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(EventSeries.objects.filter(title="No weekday").exists())

    def test_missing_title_reshows_the_form_with_errors(self):
        self.client.force_login(self.user)

        response = self._post(title="")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.filter(kind=Event.EventKind.TRAINING).exists())
        self.assertTrue(response.context["form"].errors)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachCreateNewsViewTests(TestCase):
    """C5 -- reuses management.forms.NewsForm, re-scoped to the coach's own
    managed team(s); see CoachCreateNewsView's own docstring for what's
    scoped down from the design mock."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

    def _post(self, **overrides):
        data = {"title": "Big win Saturday", "body": "Great game everyone.", "teams": [str(self.team.pk)]}
        data.update(overrides)
        return self.client.post(reverse("mobile:coach_create_news"), data, HTTP_HOST="ajax-united.rosterchief.app")

    def test_requires_login(self):
        response = self.client.get(reverse("mobile:coach_create_news"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 302)

    def test_get_redirects_a_non_managing_staffer(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self.client.get(reverse("mobile:coach_create_news"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)

    def test_teams_field_is_scoped_to_managed_teams(self):
        other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_news"), HTTP_HOST="ajax-united.rosterchief.app")

        team_choices = list(response.context["form"].fields["teams"].queryset)
        self.assertEqual(team_choices, [self.team])
        self.assertNotIn(other_team, team_choices)

    def test_teams_is_hidden_and_locked_to_the_active_team(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_create_news"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertIsInstance(response.context["form"].fields["teams"].widget, forms.MultipleHiddenInput)
        self.assertEqual(response.context["form"].initial.get("teams"), [self.team.pk])
        self.assertContains(response, f'<input type="hidden" name="teams" value="{self.team.pk}"')
        self.assertNotContains(response, 'name="teams" type="checkbox"')

    def test_valid_post_creates_a_pending_review_post_scoped_to_the_team(self):
        self.client.force_login(self.user)

        response = self._post(title="Big win Saturday")

        news_item = News.objects.get(title="Big win Saturday")
        self.assertEqual(news_item.club, self.club)
        self.assertEqual(news_item.created_by, self.member)
        self.assertEqual(news_item.status, News.Status.PENDING_REVIEW)
        self.assertEqual(news_item.visibility, News.Visibility.INTERNAL)
        self.assertIn(self.team, news_item.teams.all())
        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)

    def test_a_team_is_required_never_defaults_to_club_wide(self):
        self.client.force_login(self.user)

        response = self._post(title="No team picked", teams=[])

        self.assertEqual(response.status_code, 200)
        self.assertFalse(News.objects.filter(title="No team picked").exists())
        self.assertTrue(response.context["form"].errors)

    def test_photos_are_optional(self):
        self.client.force_login(self.user)

        response = self._post(title="No photos here")

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)
        news_item = News.objects.get(title="No photos here")
        self.assertEqual(news_item.photos.count(), 0)

    def test_uploaded_photos_are_attached_first_one_is_main(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mobile:coach_create_news"),
            {"title": "Photo finish", "body": "Great game everyone.", "teams": [str(self.team.pk)], "images": [make_image_file("one.png"), make_image_file("two.png")]},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)
        news_item = News.objects.get(title="Photo finish")
        photos = list(news_item.photos.order_by("ordering"))
        self.assertEqual(len(photos), 2)
        self.assertTrue(photos[0].is_main)
        self.assertFalse(photos[1].is_main)

    def test_non_managing_staff_cannot_post(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self._post(title="Blocked post")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(News.objects.filter(title="Blocked post").exists())


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachAddPlayerViewTests(TestCase):
    """C6 -- bulk-add players to the active team's roster; see
    CoachAddPlayerView's own docstring for what's scoped down from the
    design mock."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

    def make_eligible_member(self, first_name="Anna", last_name="Player"):
        member = Member.objects.create(first_name=first_name, last_name=last_name)
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE, kind=ClubMembership.Kind.MEMBER)
        return member

    def test_requires_login(self):
        response = self.client.get(reverse("mobile:coach_add_player"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 302)

    def test_the_squad_tab_is_highlighted(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.context["active_tab"], "coach_squad")

    def test_get_redirects_a_non_managing_staffer(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        self.client.force_login(physio_user)

        response = self.client.get(reverse("mobile:coach_add_player"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)

    def test_lists_eligible_members_not_already_on_the_roster(self):
        eligible = self.make_eligible_member()
        already_on_roster = self.make_eligible_member(first_name="On", last_name="Roster")
        TeamMembership.objects.create(team=self.team, member=already_on_roster, season=self.season)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player"), HTTP_HOST="ajax-united.rosterchief.app")

        candidates = response.context["candidates"]
        self.assertIn(eligible, candidates)
        self.assertNotIn(already_on_roster, candidates)

    def test_no_team_filter_excludes_members_on_any_team_this_season(self):
        on_other_team = self.make_eligible_member(first_name="Other", last_name="Team")
        other_team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        TeamMembership.objects.create(team=other_team, member=on_other_team, season=self.season)
        unrostered = self.make_eligible_member(first_name="No", last_name="Team")
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?filter=no_team", HTTP_HOST="ajax-united.rosterchief.app")

        candidates = response.context["candidates"]
        self.assertIn(unrostered, candidates)
        self.assertNotIn(on_other_team, candidates)

    def test_suggested_filter_matches_last_seasons_roster(self):
        previous_season = Season.objects.create(club=self.club, start_date=self.season.start_date - datetime.timedelta(days=365), end_date=self.season.start_date - datetime.timedelta(days=1))
        returning = self.make_eligible_member(first_name="Returning", last_name="Player")
        TeamMembership.objects.create(team=self.team, member=returning, season=previous_season)
        new_signup = self.make_eligible_member(first_name="New", last_name="Signup")
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?filter=suggested", HTTP_HOST="ajax-united.rosterchief.app")

        candidates = response.context["candidates"]
        self.assertEqual(list(candidates), [returning])
        self.assertNotIn(new_signup, candidates)

    def test_suggested_filter_includes_players_from_the_closest_younger_team(self):
        # self.team is "U16" (see setUpTestData) -- a "U14" sibling is the
        # closest smaller age-group number, so its *current*-season roster
        # counts as "coming up" candidates.
        u14 = Team.objects.create(club=self.club, name="U14", short_name="U14")
        coming_up = self.make_eligible_member(first_name="Coming", last_name="Up")
        TeamMembership.objects.create(team=u14, member=coming_up, season=self.season)
        not_a_candidate = self.make_eligible_member(first_name="Not", last_name="Candidate")
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?filter=suggested", HTTP_HOST="ajax-united.rosterchief.app")

        candidates = response.context["candidates"]
        self.assertIn(coming_up, candidates)
        self.assertNotIn(not_a_candidate, candidates)

    def test_suggested_filter_picks_the_closest_younger_team_not_any_smaller_one(self):
        u14 = Team.objects.create(club=self.club, name="U14", short_name="U14")
        u12 = Team.objects.create(club=self.club, name="U12", short_name="U12")
        from_u14 = self.make_eligible_member(first_name="From", last_name="U14")
        TeamMembership.objects.create(team=u14, member=from_u14, season=self.season)
        from_u12 = self.make_eligible_member(first_name="From", last_name="U12")
        TeamMembership.objects.create(team=u12, member=from_u12, season=self.season)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?filter=suggested", HTTP_HOST="ajax-united.rosterchief.app")

        candidates = response.context["candidates"]
        self.assertIn(from_u14, candidates)
        self.assertNotIn(from_u12, candidates)

    def test_suggested_filter_matches_nothing_extra_without_a_u_number(self):
        self.team.name = "First Team"
        self.team.short_name = "1st"
        self.team.save()
        sibling = Team.objects.create(club=self.club, name="Reserves", short_name="Res")
        member = self.make_eligible_member(first_name="Reserve", last_name="Player")
        TeamMembership.objects.create(team=sibling, member=member, season=self.season)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?filter=suggested", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotIn(member, response.context["candidates"])

    def test_suggested_filter_does_not_duplicate_a_player_matching_both_sources(self):
        previous_season = Season.objects.create(club=self.club, start_date=self.season.start_date - datetime.timedelta(days=365), end_date=self.season.start_date - datetime.timedelta(days=1))
        u14 = Team.objects.create(club=self.club, name="U14", short_name="U14")
        both = self.make_eligible_member(first_name="Both", last_name="Sources")
        TeamMembership.objects.create(team=self.team, member=both, season=previous_season)
        TeamMembership.objects.create(team=u14, member=both, season=self.season)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?filter=suggested", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(list(response.context["candidates"]).count(both), 1)

    def test_search_matches_first_or_last_name(self):
        match = self.make_eligible_member(first_name="Zara", last_name="Zenith")
        other = self.make_eligible_member(first_name="Not", last_name="Matching")
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?q=zar", HTTP_HOST="ajax-united.rosterchief.app")

        candidates = response.context["candidates"]
        self.assertIn(match, candidates)
        self.assertNotIn(other, candidates)

    def test_search_combines_with_the_active_filter(self):
        previous_season = Season.objects.create(club=self.club, start_date=self.season.start_date - datetime.timedelta(days=365), end_date=self.season.start_date - datetime.timedelta(days=1))
        returning_match = self.make_eligible_member(first_name="Zara", last_name="Returning")
        TeamMembership.objects.create(team=self.team, member=returning_match, season=previous_season)
        returning_no_match = self.make_eligible_member(first_name="Other", last_name="Returning")
        TeamMembership.objects.create(team=self.team, member=returning_no_match, season=previous_season)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_add_player") + "?filter=suggested&q=zar", HTTP_HOST="ajax-united.rosterchief.app")

        candidates = response.context["candidates"]
        self.assertEqual(list(candidates), [returning_match])

    def test_post_adds_selected_members_to_the_roster(self):
        first = self.make_eligible_member(first_name="First", last_name="Pick")
        second = self.make_eligible_member(first_name="Second", last_name="Pick")
        self.client.force_login(self.user)

        response = self.client.post(reverse("mobile:coach_add_player"), {"member": [str(first.pk), str(second.pk)]}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)
        self.assertTrue(TeamMembership.objects.filter(team=self.team, season=self.season, member=first).exists())
        self.assertTrue(TeamMembership.objects.filter(team=self.team, season=self.season, member=second).exists())

    def test_post_ignores_a_member_id_outside_the_eligible_pool(self):
        ineligible = Member.objects.create(first_name="Not", last_name="Eligible")
        self.client.force_login(self.user)

        response = self.client.post(reverse("mobile:coach_add_player"), {"member": [str(ineligible.pk)]}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)
        self.assertFalse(TeamMembership.objects.filter(team=self.team, member=ineligible).exists())

    def test_non_managing_staff_cannot_post(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        candidate = self.make_eligible_member()
        self.client.force_login(physio_user)

        response = self.client.post(reverse("mobile:coach_add_player"), {"member": [str(candidate.pk)]}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(TeamMembership.objects.filter(team=self.team, member=candidate).exists())


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CoachLineupViewTests(TestCase):
    """C3 -- design_handoff_rosterchief_platform/README.md's C3 section; see
    CoachLineupView's own docstring for the plain yes/no-per-player design
    (no lines/slots)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", email="coach@example.com", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

        cls.player = Member.objects.create(first_name="Anna", last_name="Player")
        TeamMembership.objects.create(team=cls.team, member=cls.player, season=cls.season)
        cls.event = Event.objects.create(club=cls.club, title="Big game", kind=Event.EventKind.GAME, start=timezone.now() + datetime.timedelta(days=2))
        cls.event.teams.add(cls.team)
        Attendance.objects.update_or_create(event=cls.event, member=cls.player, defaults={"status": Attendance.AttendanceStatus.PRESENT})

    def make_physio(self):
        physio_position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        physio_user = User.objects.create_user(email="physio@example.com", password="pw-secret-123")
        physio_member = Member.objects.create(first_name="Pat", last_name="Physio", user=physio_user)
        StaffAssignment.objects.create(team=self.team, member=physio_member, season=self.season, position=physio_position)
        return physio_user

    def test_requires_login(self):
        response = self.client.get(reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 302)

    def test_the_schedule_tab_is_highlighted(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.context["active_tab"], "coach_schedule")

    def test_get_creates_a_lineup_lazily(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Lineup.objects.filter(event=self.event).exists())

    def test_a_non_game_event_is_not_reachable(self):
        practice = Event.objects.create(club=self.club, title="Practice", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=2))
        practice.teams.add(self.team)
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_lineup", kwargs={"event_id": practice.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_get_groups_available_players_by_position(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        categories = response.context["categories"]
        self.assertEqual(len(categories), 1)
        self.assertEqual(categories[0]["label"], "No position set")
        self.assertEqual([row["member"] for row in categories[0]["rows"]], [self.player])
        self.assertFalse(categories[0]["rows"][0]["selected"])

    def test_save_selects_the_submitted_players(self):
        self.client.force_login(self.user)
        lineup = Lineup.objects.create(event=self.event, team=self.team)

        response = self.client.post(
            reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}),
            {f"selected_{self.player.pk}": "true"},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertRedirects(response, reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), fetch_redirect_response=False)
        self.assertTrue(LineupSelection.objects.filter(lineup=lineup, member=self.player).exists())

    def test_save_deselects_a_player_when_not_submitted(self):
        self.client.force_login(self.user)
        lineup = Lineup.objects.create(event=self.event, team=self.team)
        LineupSelection.objects.create(lineup=lineup, member=self.player)

        response = self.client.post(reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), {}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), fetch_redirect_response=False)
        self.assertFalse(LineupSelection.objects.filter(lineup=lineup, member=self.player).exists())

    def test_save_ignores_a_member_id_outside_the_available_pool(self):
        outsider = Member.objects.create(first_name="Not", last_name="Available")
        self.client.force_login(self.user)
        lineup = Lineup.objects.create(event=self.event, team=self.team)

        self.client.post(
            reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}),
            {f"selected_{outsider.pk}": "true"},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertFalse(LineupSelection.objects.filter(lineup=lineup, member=outsider).exists())

    def test_publish_marks_the_lineup_published(self):
        self.client.force_login(self.user)
        lineup = Lineup.objects.create(event=self.event, team=self.team)

        response = self.client.post(reverse("mobile:coach_lineup_publish", kwargs={"event_id": self.event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), fetch_redirect_response=False)
        lineup.refresh_from_db()
        self.assertIsNotNone(lineup.published_at)

    def test_schedule_sets_scheduled_publish_at_without_publishing(self):
        self.client.force_login(self.user)
        lineup = Lineup.objects.create(event=self.event, team=self.team)
        publish_at = timezone.now() + datetime.timedelta(days=1)

        response = self.client.post(
            reverse("mobile:coach_lineup_publish", kwargs={"event_id": self.event.pk}),
            {"action": "schedule", "publish_at": publish_at.strftime("%Y-%m-%dT%H:%M")},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertRedirects(response, reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), fetch_redirect_response=False)
        lineup.refresh_from_db()
        self.assertIsNone(lineup.published_at)
        self.assertIsNotNone(lineup.scheduled_publish_at)

    def test_schedule_rejects_a_time_in_the_past(self):
        self.client.force_login(self.user)
        lineup = Lineup.objects.create(event=self.event, team=self.team)
        publish_at = timezone.now() - datetime.timedelta(days=1)

        self.client.post(
            reverse("mobile:coach_lineup_publish", kwargs={"event_id": self.event.pk}),
            {"action": "schedule", "publish_at": publish_at.strftime("%Y-%m-%dT%H:%M")},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        lineup.refresh_from_db()
        self.assertIsNone(lineup.scheduled_publish_at)

    def test_cancel_schedule_clears_the_scheduled_time(self):
        self.client.force_login(self.user)
        lineup = Lineup.objects.create(event=self.event, team=self.team, scheduled_publish_at=timezone.now() + datetime.timedelta(days=1))

        response = self.client.post(
            reverse("mobile:coach_lineup_publish", kwargs={"event_id": self.event.pk}),
            {"action": "cancel_schedule"},
            HTTP_HOST="ajax-united.rosterchief.app",
        )

        self.assertRedirects(response, reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), fetch_redirect_response=False)
        lineup.refresh_from_db()
        self.assertIsNone(lineup.scheduled_publish_at)

    def test_non_managing_staff_sees_a_read_only_view(self):
        physio_user = self.make_physio()
        self.client.force_login(physio_user)

        response = self.client.get(reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Save line-up")

    def test_non_managing_staff_cannot_save(self):
        physio_user = self.make_physio()
        self.client.force_login(physio_user)

        response = self.client.post(reverse("mobile:coach_lineup", kwargs={"event_id": self.event.pk}), {}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 403)

    def test_non_managing_staff_cannot_publish(self):
        physio_user = self.make_physio()
        lineup = Lineup.objects.create(event=self.event, team=self.team)
        self.client.force_login(physio_user)

        response = self.client.post(reverse("mobile:coach_lineup_publish", kwargs={"event_id": self.event.pk}), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 403)
        lineup.refresh_from_db()
        self.assertIsNone(lineup.published_at)
