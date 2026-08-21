import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from club.models import Club, ClubMembership, DuesInvoice, Season
from events.models import Attendance, Event
from members.models import Family, FamilyMembership, Member
from news.models import News

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

    def test_rejects_an_unknown_status_value(self):
        self.client.force_login(self.user)

        response = self._post(self.event, {"status": "maybe"})

        self.assertEqual(response.status_code, 400)

    def test_cannot_rsvp_for_an_event_from_another_club(self):
        other_club = Club.objects.create(name="Other Club", slug="other-club", secondary_color="#e4002b")
        other_event = Event.objects.create(club=other_club, title="Not ours", start=timezone.now() + datetime.timedelta(days=3))
        self.client.force_login(self.user)

        response = self._post(other_event, {"status": "present"})

        self.assertEqual(response.status_code, 404)
