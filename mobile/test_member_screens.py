"""Member Home, Calendar and the event sheet (iOS parity): one card/row per event, inline answers,
the month grid, the filter menu, "jump to today" and the event as a bottom-sheet fragment.

The older behaviour of these screens is covered by mobile/tests.py; this file is only what the
iOS-parity pass added."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from club.models import ClubMembership, Season
from events.models import Attendance, Event, EventTask, EventTaskClaim, Location, Opponent, RefereeSignup
from members.models import Family, FamilyMembership, Member
from rosterchief.test_support import make_club as _make_club
from rosterchief.test_support import start_auto_mfa_enrolment, stop_auto_mfa_enrolment
from teams.models import RefereeLevel, RefereeProfile, Team

from .services import event_ui

User = get_user_model()
HOST = "ajax-united.rosterchief.app"
IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
Status = Attendance.AttendanceStatus


def setUpModule():
    start_auto_mfa_enrolment()


def tearDownModule():
    stop_auto_mfa_enrolment()


def make_club(**kwargs):
    return _make_club(secondary_color="#e4002b", **kwargs)


def at(day_offset, hour=12, minute=0):
    """An aware datetime ``day_offset`` days from today at ``hour``:``minute`` local time."""
    day = timezone.localdate() + datetime.timedelta(days=day_offset)
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


class MemberScreenFixture:
    """A parent (Lars) with one child (Noor) in one club."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=60), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)
        cls.team = Team.objects.create(club=cls.club, name="U12 Girls", short_name="U12")

    def add_child(self, first_name="Noor"):
        family = Family.objects.create(club=self.club, name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name=first_name, last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        return child

    def make_event(self, **kwargs):
        kwargs.setdefault("club", self.club)
        kwargs.setdefault("title", "Practice")
        kwargs.setdefault("kind", Event.EventKind.TRAINING)
        kwargs.setdefault("start", at(3))
        return Event.objects.create(**kwargs)

    def invite(self, event, member, status=Status.NO_RESPONSE):
        return Attendance.objects.create(event=event, member=member, status=status)

    def get(self, url, **extra):
        return self.client.get(url, HTTP_HOST=HOST, **extra)

    def post(self, url, data, **extra):
        return self.client.post(url, data=data, HTTP_HOST=HOST, **extra)


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------


class EventUiHelperTests(TestCase):
    def location(self, **kwargs):
        defaults = {"name": "Home Rink", "address": "1 Ice Street", "city": "Mechelen", "zip_code": "2800"}
        return Location(**{**defaults, **kwargs})

    def test_maps_link_is_apple_maps_on_ios_and_google_maps_elsewhere(self):
        self.assertTrue(event_ui.maps_url(self.location(), IPHONE_UA).startswith("https://maps.apple.com/?q="))
        self.assertTrue(event_ui.maps_url(self.location(), "Mozilla/5.0 (Linux; Android 15)").startswith("https://www.google.com/maps/search/?api=1&query="))
        self.assertTrue(event_ui.maps_url(self.location()).startswith("https://www.google.com/maps/"))

    def test_maps_link_carries_the_address_url_encoded(self):
        url = event_ui.maps_url(self.location())

        self.assertIn("Home%20Rink", url)
        self.assertIn("1%20Ice%20Street", url)
        self.assertIn("2800%20Mechelen", url)

    def test_no_maps_link_without_location_data(self):
        self.assertIsNone(event_ui.maps_url(None))
        self.assertIsNone(event_ui.maps_url(self.location(name="", address="")))

    def test_a_game_is_finished_once_it_has_a_result_and_is_over(self):
        now = timezone.now()
        played = Event(kind=Event.EventKind.GAME, start=now - datetime.timedelta(hours=3), score_for=4, score_against=2)
        self.assertTrue(event_ui.is_finished_game(played, now))
        self.assertFalse(event_ui.is_finished_game(Event(kind=Event.EventKind.GAME, start=now - datetime.timedelta(hours=3), score_for=4, score_against=2, is_live=True), now))
        self.assertFalse(event_ui.is_finished_game(Event(kind=Event.EventKind.GAME, start=now + datetime.timedelta(hours=3), score_for=4, score_against=2), now))
        self.assertFalse(event_ui.is_finished_game(Event(kind=Event.EventKind.GAME, start=now - datetime.timedelta(hours=3)), now))
        self.assertFalse(event_ui.is_finished_game(Event(kind=Event.EventKind.TRAINING, start=now - datetime.timedelta(hours=3), score_for=1, score_against=0), now))

    def test_signup_pills_name_where_the_reply_stands(self):
        self.assertEqual(str(event_ui.signup_pill("referee", "invited")[0]), "Reply needed")
        self.assertEqual(event_ui.signup_pill("referee", "accepted")[1], "pill-referee")
        self.assertEqual(event_ui.signup_pill("official", "accepted")[1], "pill-info")
        self.assertEqual(str(event_ui.signup_pill("official", "declined")[0]), "Declined")

    def test_month_paging_urls_step_a_month(self):
        urls = []
        pane = event_ui.month_pane([], month_start=datetime.date(2026, 12, 1), selected=datetime.date(2026, 12, 1), today=datetime.date(2026, 10, 14), url_for=lambda **kw: urls.append(kw) or f"u{len(urls)}")

        self.assertEqual(len(pane["weeks"]), 5)
        self.assertTrue(all(len(week) == 7 for week in pane["weeks"]))
        self.assertIn({"month": "2026-11", "day": None}, urls)
        self.assertIn({"month": "2027-01", "day": None}, urls)

    def test_parse_helpers_ignore_garbage(self):
        self.assertEqual(event_ui.parse_month("2026-02", datetime.date(2026, 10, 14)), datetime.date(2026, 2, 1))
        self.assertEqual(event_ui.parse_month("nonsense", datetime.date(2026, 10, 14)), datetime.date(2026, 10, 1))
        self.assertIsNone(event_ui.parse_day("2026-13-40"))

    def test_window_covers_the_whole_grid_of_full_weeks(self):
        start, end = event_ui.month_window(datetime.date(2026, 10, 1))

        self.assertEqual(timezone.localtime(start).date(), datetime.date(2026, 9, 28))  # the Monday before
        self.assertEqual(timezone.localtime(end).date(), datetime.date(2026, 11, 2))  # the day after the closing Sunday


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class CalendarScreenTests(MemberScreenFixture, TestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def calendar(self, **params):
        url = reverse("mobile:calendar")
        if params:
            url += "?" + "&".join(f"{key}={value}" for key, value in params.items())
        return self.get(url)

    def groups(self, response):
        groups = response.context["this_week"] + response.context["next_week"]
        for month in response.context["later_months"]:
            groups += month["items"]
        return groups

    def test_siblings_on_one_event_share_one_card_with_a_line_each(self):
        child = self.add_child()
        practice = self.make_event(title="Ice 3")
        self.invite(practice, self.member)
        self.invite(practice, child)

        response = self.calendar()

        groups = self.groups(response)
        self.assertEqual(len(groups), 1)
        self.assertEqual({line["person"] for line in groups[0]["lines"]}, {self.member, child})
        self.assertContains(response, "Noor")
        self.assertContains(response, "You")
        self.assertEqual(response.content.decode().count('class="truncate">Ice 3'), 1)

    def test_each_line_has_in_and_out_buttons_posting_back_to_this_page(self):
        practice = self.make_event()
        self.invite(practice, self.member)

        response = self.calendar()

        self.assertContains(response, 'name="status" value="present"')
        self.assertContains(response, f'<input type="hidden" name="return" value="{reverse("mobile:calendar")}">')
        self.assertContains(response, "data-ask")

    def test_an_answered_in_is_highlighted_and_not_resendable(self):
        practice = self.make_event()
        self.invite(practice, self.member, Status.PRESENT)

        response = self.calendar()

        self.assertContains(response, 'aria-pressed="true"')

    def test_a_closed_deadline_replaces_the_buttons_with_the_status(self):
        practice = self.make_event(deadline=timezone.now() - datetime.timedelta(hours=1))
        self.invite(practice, self.member, Status.ABSENT)

        response = self.calendar()

        self.assertNotContains(response, 'name="status" value="present"')
        self.assertContains(response, "pill-danger")

    def test_a_game_row_reads_home_away_and_names_the_opponent(self):
        home = Location.objects.create(club=self.club, name="Home Rink", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        away = Location.objects.create(club=self.club, name="Their Arena", address="2 St", city="Elsewhere", zip_code="2000", country="BE")
        rivals = Opponent.objects.create(club=self.club, name="Leuven Chiefs")
        at_home = self.make_event(kind=Event.EventKind.GAME, title="Game 1", opponent=rivals, location=home, start=at(2, 18, 30))
        elsewhere = self.make_event(kind=Event.EventKind.GAME, title="Game 2", opponent=rivals, location=away, start=at(4, 18, 30))
        at_home.teams.add(self.team)
        self.invite(at_home, self.member)
        self.invite(elsewhere, self.member)

        response = self.calendar()

        by_event = {group["event"]: group for group in self.groups(response)}
        self.assertEqual(by_event[at_home]["title"], "Game · Leuven Chiefs")
        self.assertEqual(by_event[at_home]["meta"], "18:30 · Home · U12")
        self.assertEqual(by_event[elsewhere]["meta"], "18:30 · Away")

    def test_a_finished_game_shows_its_final_score_instead_of_answer_lines(self):
        game = self.make_event(kind=Event.EventKind.GAME, title="Played", start=timezone.now() - datetime.timedelta(hours=3), score_for=4, score_against=2)
        self.invite(game, self.member, Status.PRESENT)

        response = self.calendar(earlier=1)

        # Earlier today (or just before midnight) -- either way it is on the list and finished.
        everything = self.groups(response) + [group for month in response.context["earlier_months"] for group in month["items"]]
        group = next(group for group in everything if group["event"] == game)
        self.assertTrue(group["finished"])
        self.assertEqual(group["lines"], [])
        self.assertContains(response, "4 &ndash; 2")

    def test_the_list_starts_today_and_earlier_events_are_one_tap_away(self):
        yesterday = self.make_event(title="Yesterday's practice", start=at(-1))
        self.invite(yesterday, self.member, Status.PRESENT)
        upcoming = self.make_event(title="Next practice", start=at(2))
        self.invite(upcoming, self.member)

        default = self.calendar()
        earlier = self.calendar(earlier=1)

        self.assertEqual({group["event"] for group in self.groups(default)}, {upcoming})
        self.assertEqual(default.context["earlier_months"], [])
        self.assertContains(default, "Show earlier events")
        self.assertEqual([group["event"] for month in earlier.context["earlier_months"] for group in month["items"]], [yesterday])
        self.assertNotContains(earlier, "Show earlier events")

    def test_today_is_marked_on_todays_first_event(self):
        first = self.make_event(title="Morning practice", start=at(0, 6))
        self.invite(first, self.member, Status.PRESENT)
        later = self.make_event(title="Next one", start=at(2))
        self.invite(later, self.member)

        response = self.calendar()

        groups = {group["event"]: group for group in self.groups(response)}
        self.assertTrue(groups[first]["anchor_today"])
        self.assertFalse(groups[later]["anchor_today"])
        self.assertContains(response, '<div id="today"')

    def test_with_nothing_today_the_next_event_carries_the_today_marker(self):
        later = self.make_event(title="Next one", start=at(2))
        self.invite(later, self.member)

        response = self.calendar()

        self.assertTrue(self.groups(response)[0]["anchor_today"])

    def test_today_param_flags_the_page_for_the_scroll(self):
        later = self.make_event(start=at(2))
        self.invite(later, self.member)

        self.assertContains(self.calendar(today=1), "data-scroll-today")
        self.assertNotContains(self.calendar(), "data-scroll-today")

    def test_task_pill_counts_open_tasks_out_of_all(self):
        event = self.make_event()
        self.invite(event, self.member)
        EventTask.objects.create(event=event, title="Fruit", needed_quantity=1)
        taken = EventTask.objects.create(event=event, title="Water", needed_quantity=1)
        EventTaskClaim.objects.create(task=taken, member=self.member)

        response = self.calendar()

        self.assertContains(response, "1 of 2 tasks open")

    def test_no_task_pill_once_every_task_is_taken(self):
        event = self.make_event()
        self.invite(event, self.member)
        taken = EventTask.objects.create(event=event, title="Water", needed_quantity=1)
        EventTaskClaim.objects.create(task=taken, member=self.member)

        response = self.calendar()

        self.assertNotContains(response, "tasks open")
        self.assertNotContains(response, "task open")

    def test_the_scope_menu_narrows_the_calendar_to_one_person(self):
        child = self.add_child()
        mine = self.make_event(title="Lars's practice")
        theirs = self.make_event(title="Noor's practice", start=at(4))
        self.invite(mine, self.member)
        self.invite(theirs, child)

        everyone = self.calendar()
        only_child = self.calendar(**{"as": child.pk})

        self.assertEqual({group["event"] for group in self.groups(everyone)}, {mine, theirs})
        self.assertEqual({group["event"] for group in self.groups(only_child)}, {theirs})
        self.assertContains(everyone, "All members")
        self.assertContains(only_child, child.first_name)

    def test_no_scope_menu_for_a_lone_member(self):
        self.assertNotContains(self.calendar(), "All members")

    def test_the_filter_menu_hides_unticked_kinds(self):
        game = self.make_event(kind=Event.EventKind.GAME, title="vs Leuven")
        practice = self.make_event(kind=Event.EventKind.TRAINING, title="Ice 3", start=at(4))
        social = self.make_event(kind=Event.EventKind.SOCIAL, title="Pizza night", start=at(5))
        for event in (game, practice, social):
            self.invite(event, self.member)

        response = self.calendar(f=1, k="game")

        self.assertEqual({group["event"] for group in self.groups(response)}, {game})
        self.assertTrue(response.context["filter_active"])
        checked = {item["value"]: item["checked"] for item in response.context["menu_kinds"]}
        self.assertEqual(checked["game"], True)
        self.assertEqual(checked["training"], False)

    def test_no_filter_means_everything_and_no_active_dot(self):
        game = self.make_event(kind=Event.EventKind.GAME, title="vs Leuven")
        social = self.make_event(kind=Event.EventKind.SOCIAL, title="Pizza night", start=at(5))
        self.invite(game, self.member)
        self.invite(social, self.member)

        response = self.calendar()

        self.assertEqual({group["event"] for group in self.groups(response)}, {game, social})
        self.assertFalse(response.context["filter_active"])
        self.assertContains(response, "Show on calendar")

    def test_the_filter_menu_lists_the_five_kinds(self):
        self.invite(self.make_event(), self.member)

        response = self.calendar()

        for label in ("Games", "Tournaments", "Practices", "Meetings", "Social"):
            self.assertContains(response, label)

    def test_month_mode_selects_a_day_and_lists_its_events(self):
        target = at(2)
        event = self.make_event(title="Month practice", start=target)
        other = self.make_event(title="Other day", start=at(3))
        self.invite(event, self.member)
        self.invite(other, self.member)
        day = timezone.localtime(target).date()

        response = self.calendar(view="month", month=day.strftime("%Y-%m"), day=day.isoformat())

        pane = response.context["month_pane"]
        self.assertEqual(pane["selected"], day)
        self.assertEqual([group["event"] for group in pane["selected_groups"]], [event])
        self.assertContains(response, 'id="month-pane"')
        self.assertContains(response, "Month practice")
        cell = next(cell for week in pane["weeks"] for cell in week if cell["date"] == day)
        self.assertTrue(cell["has_events"])
        self.assertTrue(cell["selected"])
        self.assertEqual(response.context["this_week"], [])

    def test_month_mode_marks_today_and_defaults_to_it(self):
        response = self.calendar(view="month")

        pane = response.context["month_pane"]
        self.assertEqual(pane["selected"], timezone.localdate())
        self.assertTrue(any(cell["is_today"] for week in pane["weeks"] for cell in week))
        self.assertContains(response, "Nothing scheduled")

    def test_month_mode_includes_past_events_of_that_month(self):
        past = self.make_event(title="Last month's game", start=at(-35))
        self.invite(past, self.member, Status.PRESENT)
        day = timezone.localtime(past.start).date()

        response = self.calendar(view="month", month=day.strftime("%Y-%m"), day=day.isoformat())

        self.assertEqual([group["event"] for group in response.context["month_pane"]["selected_groups"]], [past])

    def test_month_paging_links_keep_the_view(self):
        response = self.calendar(view="month", month="2026-10")

        self.assertContains(response, "month=2026-09")
        self.assertContains(response, "month=2026-11")

    def test_the_list_and_month_switch_link_to_each_other(self):
        response = self.calendar()

        self.assertContains(response, "view=month")
        self.assertContains(response, 'aria-label="List"')
        self.assertContains(response, 'aria-label="Month"')

    def test_events_open_the_sheet_shell_is_on_the_page(self):
        self.invite(self.make_event(), self.member)

        response = self.calendar()

        self.assertContains(response, 'id="event-sheet"')
        self.assertContains(response, 'id="event-sheet-content"')

    def test_the_empty_state_is_a_state_card(self):
        response = self.calendar()

        self.assertContains(response, "state-card")
        self.assertContains(response, "Nothing scheduled")

    def test_a_referee_line_has_accept_and_decline_buttons_and_the_role_colour(self):
        home = Location.objects.create(club=self.club, name="Home Rink", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        level.teams.add(self.team)
        RefereeProfile.objects.create(club=self.club, member=self.member, level=level, valid_until=timezone.localdate() + datetime.timedelta(days=30))
        game = self.make_event(kind=Event.EventKind.GAME, title="Home game", location=home, start=at(2, 15))
        game.teams.add(self.team)

        response = self.calendar()

        group = self.groups(response)[0]
        self.assertEqual(group["bar_class"], "bg-referee")
        self.assertEqual(group["title"], "Referee · Home game")
        self.assertContains(response, 'name="response" value="accept"')
        self.assertContains(response, 'name="response" value="decline"')
        signup = RefereeSignup.objects.get(event=game, member=self.member)
        self.assertContains(response, reverse("mobile:referee_signup_respond", kwargs={"signup_id": signup.pk}))


# ---------------------------------------------------------------------------
# Inline answers: where they go back to
# ---------------------------------------------------------------------------


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class InlineAnswerTests(MemberScreenFixture, TestCase):
    def setUp(self):
        self.client.force_login(self.user)
        self.event = self.make_event()
        self.attendance = self.invite(self.event, self.member)
        self.url = reverse("mobile:event_detail", kwargs={"pk": self.event.pk})

    def test_an_answer_goes_back_to_the_page_it_was_given_on(self):
        calendar_url = reverse("mobile:calendar") + "?view=month&month=2026-10"

        response = self.post(self.url, {"status": "present", "member_id": self.member.pk, "return": calendar_url})

        self.assertRedirects(response, calendar_url, fetch_redirect_response=False)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Status.PRESENT)

    def test_a_return_to_another_site_is_ignored(self):
        for target in ("https://evil.example/phish", "//evil.example/phish", "javascript:alert(1)"):
            with self.subTest(target=target):
                response = self.post(self.url, {"status": "present", "member_id": self.member.pk, "return": target})

                self.assertRedirects(response, reverse("mobile:home"), fetch_redirect_response=False)

    def test_a_refused_inline_answer_goes_back_with_a_notice(self):
        Event.objects.filter(pk=self.event.pk).update(deadline=timezone.now() - datetime.timedelta(hours=1))

        response = self.post(self.url, {"status": "present", "member_id": self.member.pk, "return": reverse("mobile:calendar")}, follow=True)

        self.assertEqual(response.redirect_chain[-1][0], reverse("mobile:calendar"))
        self.assertContains(response, "Replies are closed")
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Status.NO_RESPONSE)

    def test_out_still_needs_a_reason(self):
        response = self.post(self.url, {"status": "absent", "member_id": self.member.pk, "note": "", "return": reverse("mobile:home")}, follow=True)

        self.assertEqual(response.redirect_chain[-1][0], reverse("mobile:home"))
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Status.NO_RESPONSE)

    def test_a_task_claim_goes_back_to_where_it_was_made(self):
        task = EventTask.objects.create(event=self.event, title="Fruit", needed_quantity=1)

        response = self.post(reverse("mobile:event_task_respond", kwargs={"task_id": task.pk}), {"response": "claim", "return": reverse("mobile:home")})

        self.assertRedirects(response, reverse("mobile:home"), fetch_redirect_response=False)
        self.assertTrue(EventTaskClaim.objects.filter(task=task, member=self.member).exists())


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class HomeCardTests(MemberScreenFixture, TestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def home(self, **params):
        url = reverse("mobile:home")
        if params:
            url += "?" + "&".join(f"{key}={value}" for key, value in params.items())
        return self.get(url)

    def test_people_with_the_same_next_event_share_one_card(self):
        child = self.add_child()
        practice = self.make_event(title="Ice 1")
        self.invite(practice, self.member)
        self.invite(practice, child)

        response = self.home()

        cards = response.context["member_cards"]
        self.assertEqual(len(cards), 1)
        self.assertEqual({line["name"] for line in cards[0]["lines"]}, {"Lars", "Noor"})
        self.assertEqual(cards[0]["kind"], "rsvp_needed")

    def test_a_pending_invitation_has_inline_in_and_out(self):
        self.invite(self.make_event(), self.member)

        response = self.home()

        self.assertContains(response, 'name="status" value="present"')
        self.assertContains(response, "data-ask")
        self.assertContains(response, f'<input type="hidden" name="return" value="{reverse("mobile:home")}">')
        self.assertNotContains(response, "Replies are closed for this event")

    def test_the_card_names_kind_date_times_and_location(self):
        rink = Location.objects.create(club=self.club, name="Home Rink", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        event = self.make_event(title="Ice 1", location=rink, start=at(3, 17, 30), gathering=at(3, 16, 15))
        self.invite(event, self.member)

        response = self.home()

        self.assertContains(response, "Practice")
        self.assertContains(response, "17:30 face-off")
        self.assertContains(response, "Meet 16:15")
        self.assertContains(response, "Home Rink")

    def test_a_closed_unanswered_invitation_says_replies_are_closed(self):
        event = self.make_event(deadline=timezone.now() - datetime.timedelta(hours=1))
        self.invite(event, self.member)

        response = self.home()

        self.assertEqual(response.context["member_cards"][0]["kind"], "rsvp_closed")
        self.assertContains(response, "Replies are closed for this event")
        self.assertNotContains(response, 'name="status" value="present"')

    def test_an_answered_event_shows_going_and_the_cant_make_it_prompt(self):
        event = self.make_event()
        self.invite(event, self.member, Status.PRESENT)

        response = self.home()

        self.assertEqual(response.context["member_cards"][0]["kind"], "next_going")
        self.assertContains(response, "Going")
        self.assertContains(response, "Can't make it?")
        # A plain PRESENT player answers Out; only someone picked in a line-up reports a dropout.
        self.assertContains(response, '<input type="hidden" name="status" value="absent">')
        self.assertNotContains(response, 'name="status" value="dropout"')

    def test_a_selected_player_reports_a_dropout(self):
        event = self.make_event()
        self.invite(event, self.member, Status.SELECTED)

        response = self.home()

        self.assertContains(response, 'name="status" value="dropout"')

    def test_your_tasks_lists_what_the_household_already_took(self):
        child = self.add_child()
        event = self.make_event()
        self.invite(event, self.member, Status.PRESENT)
        mine = EventTask.objects.create(event=event, title="Bring fruit", needed_quantity=1)
        theirs = EventTask.objects.create(event=event, title="Wash bibs", needed_quantity=1)
        EventTaskClaim.objects.create(task=mine, member=self.member)
        EventTaskClaim.objects.create(task=theirs, member=child)

        response = self.home()

        self.assertContains(response, "Your tasks")
        self.assertContains(response, "Bring fruit")
        self.assertContains(response, "Wash bibs &middot; someone in your family")

    def test_other_families_tasks_never_show_as_yours(self):
        event = self.make_event()
        self.invite(event, self.member, Status.PRESENT)
        stranger = Member.objects.create(first_name="Sam", last_name="Other")
        task = EventTask.objects.create(event=event, title="Bring fruit", needed_quantity=2)
        EventTaskClaim.objects.create(task=task, member=stranger)

        response = self.home()

        self.assertNotContains(response, "Your tasks")
        self.assertContains(response, "Open tasks")

    def test_an_open_task_has_an_inline_claim_that_returns_home(self):
        event = self.make_event()
        self.invite(event, self.member, Status.PRESENT)
        task = EventTask.objects.create(event=event, title="Bring fruit", needed_quantity=1)

        response = self.home()

        self.assertContains(response, reverse("mobile:event_task_respond", kwargs={"task_id": task.pk}))
        self.assertContains(response, 'name="response" value="claim"')
        self.assertContains(response, "Claim")

    def test_all_caught_up_when_there_is_nothing(self):
        response = self.home()

        self.assertContains(response, "All Caught Up")
        self.assertContains(response, "Nothing needs your attention right now.")

    def test_not_all_caught_up_with_a_card_on_screen(self):
        self.invite(self.make_event(), self.member)

        self.assertNotContains(self.home(), "All Caught Up")

    def test_a_cards_invitation_is_not_repeated_under_needs_your_answer(self):
        child = self.add_child()
        mine = self.make_event(title="First", start=at(2))
        later = self.make_event(title="Second", start=at(5))
        self.invite(mine, self.member)
        self.invite(mine, child)
        self.invite(later, self.member)

        response = self.home()

        # Both people's soonest invitation is the card; only the later one still "needs an answer".
        self.assertEqual({row["event"] for row in response.context["needs_answer_view"]}, {later})
        self.assertEqual(response.context["needs_answer_view_total"], 1)

    def test_the_card_links_to_the_event_so_it_can_open_as_a_sheet(self):
        event = self.make_event()
        self.invite(event, self.member)

        response = self.home()

        self.assertContains(response, f'href="{reverse("mobile:event_detail", kwargs={"pk": event.pk})}"')
        self.assertContains(response, 'id="event-sheet"')


# ---------------------------------------------------------------------------
# The event as a sheet
# ---------------------------------------------------------------------------


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class EventSheetTests(MemberScreenFixture, TestCase):
    def setUp(self):
        self.client.force_login(self.user)
        self.rink = Location.objects.create(club=self.club, name="Home Rink", address="1 Ice Street", city="Mechelen", zip_code="2800", country="BE", is_home=True)
        self.rivals = Opponent.objects.create(club=self.club, name="Leuven Chiefs")
        self.event = self.make_event(kind=Event.EventKind.GAME, title="Game", opponent=self.rivals, location=self.rink, start=at(3, 18, 30), gathering=at(3, 17, 15))
        self.event.teams.add(self.team)
        self.attendance = self.invite(self.event, self.member)
        self.url = reverse("mobile:event_detail", kwargs={"pk": self.event.pk})

    def sheet(self, **extra):
        return self.get(self.url + "?sheet=1", **extra)

    def test_the_sheet_is_a_fragment_not_a_page(self):
        response = self.sheet()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mobile/_event_sheet.html")
        self.assertNotContains(response, "<html")
        self.assertNotContains(response, "<nav")

    def test_a_direct_visit_is_still_the_full_page(self):
        response = self.get(self.url)

        self.assertTemplateUsed(response, "mobile/event_detail.html")
        self.assertContains(response, "<html")
        self.assertNotContains(response, "data-sheet-close")

    def test_the_hero_reads_team_vs_opponent_with_the_home_badge(self):
        response = self.sheet()

        self.assertContains(response, "U12 vs Leuven Chiefs")
        self.assertContains(response, "Home game")
        self.assertContains(response, "data-sheet-close")

    def test_an_away_game_puts_the_opponent_first(self):
        away = Location.objects.create(club=self.club, name="Their Arena", address="2 St", city="Elsewhere", zip_code="2000", country="BE")
        Event.objects.filter(pk=self.event.pk).update(location=away)

        response = self.sheet()

        self.assertContains(response, "Leuven Chiefs vs U12")
        self.assertContains(response, "Away game")

    def test_details_follow_the_ios_order(self):
        body = self.sheet().content.decode()

        order = [body.index(marker) for marker in (">When<", ">Meet<", ">Where<", ">Opponent<", "Your answers")]
        self.assertEqual(order, sorted(order))
        self.assertIn("1 Ice Street, 2800 Mechelen", body)

    def test_where_has_an_open_in_maps_link_out_google_by_default(self):
        response = self.sheet()

        self.assertContains(response, "Open in Maps")
        self.assertContains(response, "https://www.google.com/maps/search/?api=1&amp;query=Home%20Rink")
        self.assertNotContains(response, "maps.apple.com")

    def test_the_maps_link_is_apple_maps_on_an_iphone(self):
        response = self.sheet(HTTP_USER_AGENT=IPHONE_UA)

        self.assertContains(response, "https://maps.apple.com/?q=Home%20Rink")
        self.assertNotContains(response, "google.com/maps")

    def test_no_maps_link_without_a_location(self):
        Event.objects.filter(pk=self.event.pk).update(location=None)

        response = self.sheet()

        self.assertNotContains(response, "Open in Maps")
        self.assertNotContains(response, ">Where<")

    def test_no_embedded_map_or_third_party_script(self):
        body = self.sheet().content.decode()

        self.assertNotIn("<iframe", body)
        self.assertNotIn("<script", body)

    def test_your_answers_has_in_maybe_out_per_person_posting_into_the_sheet(self):
        response = self.sheet()

        self.assertContains(response, "Your answers")
        self.assertContains(response, 'name="status" value="present"')
        self.assertContains(response, 'data-status="maybe"')
        self.assertContains(response, 'data-status="absent"')
        self.assertContains(response, f'hx-post="{self.url}"')
        self.assertContains(response, 'hx-target="#event-sheet-content"')
        self.assertContains(response, '<input type="hidden" name="surface" value="sheet">')

    def test_the_full_page_posts_normally_and_returns_to_itself(self):
        response = self.get(self.url)

        self.assertContains(response, '<input type="hidden" name="next" value="event_detail">')
        self.assertNotContains(response, "hx-post=")

    def test_answering_in_the_sheet_returns_the_updated_sheet(self):
        response = self.post(self.url, {"status": "present", "member_id": self.member.pk, "surface": "sheet"})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mobile/_event_sheet.html")
        self.assertNotContains(response, "<html")
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Status.PRESENT)
        self.assertNotContains(response, "No reply")

    def test_a_maybe_with_a_note_is_kept_and_shown_back(self):
        response = self.post(self.url, {"status": "maybe", "member_id": self.member.pk, "note": "Depends on work", "surface": "sheet"})

        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Status.MAYBE)
        self.assertContains(response, "Depends on work")

    def test_a_refusal_is_shown_inside_the_sheet(self):
        response = self.post(self.url, {"status": "absent", "member_id": self.member.pk, "note": "", "surface": "sheet"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter a reason.")
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Status.NO_RESPONSE)

    def test_a_closed_deadline_shows_deadline_passed_without_buttons(self):
        Event.objects.filter(pk=self.event.pk).update(deadline=timezone.now() - datetime.timedelta(hours=1))

        response = self.sheet()

        self.assertContains(response, "Deadline passed")
        self.assertContains(response, "Replies are closed for this event")
        self.assertNotContains(response, 'name="status" value="present"')

    def test_answering_after_the_deadline_is_refused_in_the_sheet(self):
        Event.objects.filter(pk=self.event.pk).update(deadline=timezone.now() - datetime.timedelta(hours=1))

        response = self.post(self.url, {"status": "present", "member_id": self.member.pk, "surface": "sheet"})

        self.assertContains(response, "Replies are closed for this event.")
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, Status.NO_RESPONSE)

    def test_a_selected_player_can_drop_out_after_the_deadline(self):
        from events.models import Lineup

        Event.objects.filter(pk=self.event.pk).update(deadline=timezone.now() - datetime.timedelta(hours=1))
        self.attendance.status = Status.SELECTED
        self.attendance.save()
        Lineup.objects.create(event=self.event, team=self.team, published_at=timezone.now())

        response = self.sheet()

        self.assertContains(response, "Drop out")
        self.assertContains(response, 'name="status" value="dropout"')
        self.assertNotContains(response, "Deadline passed")

    def test_tasks_show_x_of_y_taken_and_claim_then_give_back(self):
        task = EventTask.objects.create(event=self.event, title="Bring fruit", needed_quantity=2)
        sheet = self.sheet()
        self.assertContains(sheet, "0 of 2 taken")
        self.assertContains(sheet, ">Claim<")

        claimed = self.post(reverse("mobile:event_task_respond", kwargs={"task_id": task.pk}), {"response": "claim", "surface": "sheet"})

        self.assertEqual(claimed.status_code, 200)
        self.assertTemplateUsed(claimed, "mobile/_event_sheet.html")
        self.assertContains(claimed, "1 of 2 taken")
        self.assertContains(claimed, "Give back")
        self.assertContains(claimed, "Covered by")

        returned = self.post(reverse("mobile:event_task_respond", kwargs={"task_id": task.pk}), {"response": "unclaim", "surface": "sheet"})

        self.assertContains(returned, "0 of 2 taken")
        self.assertFalse(EventTaskClaim.objects.filter(task=task).exists())

    def test_a_full_task_says_full(self):
        task = EventTask.objects.create(event=self.event, title="Bring fruit", needed_quantity=1)
        EventTaskClaim.objects.create(task=task, member=Member.objects.create(first_name="Sam", last_name="Other"))

        response = self.sheet()

        self.assertContains(response, "1 of 1 taken")
        self.assertContains(response, "Full")
        self.assertNotContains(response, ">Claim<")

    def test_a_full_task_refusal_is_shown_in_the_sheet(self):
        task = EventTask.objects.create(event=self.event, title="Bring fruit", needed_quantity=1)
        EventTaskClaim.objects.create(task=task, member=Member.objects.create(first_name="Sam", last_name="Other"))

        response = self.post(reverse("mobile:event_task_respond", kwargs={"task_id": task.pk}), {"response": "claim", "surface": "sheet"})

        self.assertContains(response, "This is already fully covered.")

    def test_the_squad_summary_stays_counts_only(self):
        other = Member.objects.create(first_name="Sam", last_name="Other")
        self.invite(self.event, other, Status.ABSENT)
        Attendance.objects.filter(event=self.event, member=other).update(note="Family holiday")

        response = self.sheet()

        self.assertContains(response, "Squad response")
        self.assertNotContains(response, "Family holiday")
        self.assertNotContains(response, "Sam")

    def test_a_blocked_person_sees_cant_reply_yet_with_the_requirements(self):
        from club.models import OnboardingRequirement
        from teams.models import Position, TeamMembership

        position = Position.objects.create(club=self.club, name="Forward", short_name="F")
        TeamMembership.objects.create(team=self.team, member=self.member, season=self.season, position=position)
        OnboardingRequirement.objects.create(club=self.club, name="Medical certificate", blocked_event_kinds=["game"])
        Attendance.objects.filter(event=self.event).delete()

        response = self.sheet()

        self.assertContains(response, "Can't reply yet")
        self.assertContains(response, "Still needed:")
        self.assertContains(response, "Medical certificate")

    def test_a_referee_signup_is_answered_inside_the_sheet(self):
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        level.teams.add(self.team)
        RefereeProfile.objects.create(club=self.club, member=self.member, level=level, valid_until=timezone.localdate() + datetime.timedelta(days=30))
        self.event.teams.remove(self.team)
        self.event.teams.add(self.team)  # re-trigger the referee invites
        signup = RefereeSignup.objects.get(event=self.event, member=self.member)

        before = self.sheet()
        self.assertContains(before, "Refereeing")
        self.assertContains(before, "I'll ref")
        self.assertNotContains(before, "Confirmed")

        after = self.post(reverse("mobile:referee_signup_respond", kwargs={"signup_id": signup.pk}), {"response": "accept", "surface": "sheet"})

        self.assertEqual(after.status_code, 200)
        self.assertTemplateUsed(after, "mobile/_event_sheet.html")
        self.assertContains(after, "Confirmed")
        signup.refresh_from_db()
        self.assertEqual(signup.status, RefereeSignup.Status.ACCEPTED)

    def test_a_practice_hero_uses_the_kind_word(self):
        practice = self.make_event(title="Ice 3", start=at(5))
        self.invite(practice, self.member)

        response = self.get(reverse("mobile:event_detail", kwargs={"pk": practice.pk}) + "?sheet=1")

        self.assertContains(response, "Practice")
        self.assertContains(response, "Ice 3")

    def test_the_sheet_404s_for_an_unknown_event(self):
        response = self.get(reverse("mobile:event_detail", kwargs={"pk": "00000000-0000-0000-0000-000000000000"}) + "?sheet=1")

        self.assertEqual(response.status_code, 404)

    def test_the_sheet_needs_a_login(self):
        self.client.logout()

        response = self.sheet()

        self.assertEqual(response.status_code, 302)
