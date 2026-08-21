import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from icalendar import Calendar as ICalCalendar

from club.models import Club, ClubMembership, DuesInvoice, MemberRequirementStatus, OnboardingRequirement, Season, Sponsor
from events.models import Attendance, Event
from members.models import Family, FamilyMembership, Member
from news.models import News
from notifications.models import Notification
from teams.models import Position, StaffAssignment, Team, TeamMembership

from .models import CalendarFeedToken, PushSubscription
from .services.icons import render_fallback_icon

User = get_user_model()


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

        self._post(other_event, {"status": "absent"})

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
        return {row["event"] for row in response.context["this_week"] + response.context["next_week"]}

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

    def test_no_managed_people_shows_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No one to show yet")

    def test_events_outside_the_two_week_window_are_excluded(self):
        far_future = self.make_event(title="Far future game", start=timezone.now() + datetime.timedelta(days=30))
        past = self.make_event(title="Past practice", start=timezone.now() - datetime.timedelta(days=1))
        Attendance.objects.create(event=far_future, member=self.member)
        Attendance.objects.create(event=past, member=self.member)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(self._events_in_context(response), set())


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

    def test_your_answers_is_empty_when_nobody_managed_is_invited(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(list(response.context["your_answers"]), [])
        self.assertContains(response, "No one you manage is invited")

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
    instead), "Household & contacts"/"Payments & dues"/"Coach mode" all
    omitted since none has anywhere to lead in this build.
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

    def test_empty_account_gets_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No one to show yet")
        self.assertNotContains(response, "Coach mode")


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
