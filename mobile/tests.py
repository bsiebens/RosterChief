import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from club.models import Club, ClubMembership, DuesInvoice, Season
from events.models import Attendance, Event
from members.models import Family, FamilyMembership, Member
from news.models import News
from notifications.models import Notification
from teams.models import Position, StaffAssignment, Team, TeamMembership

from .models import PushSubscription
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

        self.assertEqual(response.context["dues_balance"], Decimal("420.00"))
        self.assertContains(response, "420")

    def test_dues_card_is_absent_when_nothing_is_owed(self):
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertIsNone(response.context["dues_membership"])

    def test_news_teaser_shows_the_latest_published_item(self):
        News.objects.create(club=self.club, title="Old news", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(days=5))
        latest = News.objects.create(club=self.club, title="Signed: New Player", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(days=1))
        News.objects.create(club=self.club, title="Still a draft", body="Body.", status=News.Status.DRAFT)
        self.client.force_login(self.user)

        response = self._get("home")

        self.assertEqual(response.context["news_item"], latest)
        self.assertContains(response, "Signed: New Player")

    def test_empty_account_gets_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get("home")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["scope_person"])
        self.assertContains(response, "No one to show yet")


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
    "This week"/"Next week" agenda, scoped to scope_person's own invites by
    default or, with ?scope=all, every club event."""

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

    def test_requires_login(self):
        response = self._get()

        self.assertEqual(response.status_code, 302)

    def test_per_person_scope_only_shows_that_persons_invited_events(self):
        invited = self.make_event(title="Lars's practice")
        not_invited = self.make_event(title="Someone else's practice")
        Attendance.objects.create(event=invited, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(self._events_in_context(response), {invited})
        self.assertNotContains(response, not_invited.title)

    def test_all_scope_shows_every_club_event_regardless_of_invitation(self):
        invited = self.make_event(title="Lars's practice")
        not_invited = self.make_event(title="Whole squad practice")
        Attendance.objects.create(event=invited, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        self.client.force_login(self.user)

        response = self._get(scope="all")

        self.assertEqual(self._events_in_context(response), {invited, not_invited})

    def test_all_scope_excludes_cancelled_events(self):
        cancelled = self.make_event(title="Cancelled practice", cancelled=True)
        self.client.force_login(self.user)

        response = self._get(scope="all")

        self.assertNotIn(cancelled, self._events_in_context(response))

    def test_per_person_scope_excludes_cancelled_events(self):
        cancelled = self.make_event(title="Cancelled practice", cancelled=True)
        Attendance.objects.create(event=cancelled, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        self.client.force_login(self.user)

        response = self._get()

        self.assertNotIn(cancelled, self._events_in_context(response))

    def test_events_outside_the_two_week_window_are_excluded(self):
        far_future = self.make_event(title="Far future game", start=timezone.now() + datetime.timedelta(days=30))
        past = self.make_event(title="Past practice", start=timezone.now() - datetime.timedelta(days=1))
        Attendance.objects.create(event=far_future, member=self.member)
        Attendance.objects.create(event=past, member=self.member)
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(self._events_in_context(response), set())

    def test_empty_scope_person_shows_a_graceful_empty_state(self):
        bare_user = User.objects.create_user(email="new@example.com", password="pw-secret-123")
        self.client.force_login(bare_user)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No one to show yet")


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
