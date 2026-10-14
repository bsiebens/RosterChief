"""Manager mode as an in-shell tab set (Today / Events / Squad / News), the Events kind chips, and
the event detail bottom sheet with its delete / detach / edit-scope flows. The older per-screen
tests (today, schedule, detail, editor) stay in mobile/tests.py."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from waffle import get_waffle_flag_model

from club.models import Season
from events.models import Competition, Event, EventSeries
from members.models import Member
from rosterchief.test_support import make_club as _make_club
from rosterchief.test_support import start_auto_mfa_enrolment, stop_auto_mfa_enrolment
from teams.models import Position, StaffAssignment, Team

User = get_user_model()
HOST = "ajax-united.rosterchief.app"


def setUpModule():
    # Staff accounts must be MFA-enrolled (authentication.middleware); the fixtures model staff who are.
    start_auto_mfa_enrolment()


def tearDownModule():
    stop_auto_mfa_enrolment()


def make_club(**kwargs):
    return _make_club(secondary_color="#e4002b", **kwargs)


class CoachFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="coach-shell@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Sam", last_name="Coach", user=cls.user)
        cls.team = Team.objects.create(club=cls.club, name="U16", short_name="U16")
        cls.position = Position.objects.create(club=cls.club, name="Head coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=cls.team, member=cls.member, season=cls.season, position=cls.position)

    def make_event(self, title="Practice", kind=Event.EventKind.TRAINING, days=2, **kwargs):
        event = Event.objects.create(club=self.club, title=title, kind=kind, start=timezone.now() + datetime.timedelta(days=days), **kwargs)
        event.teams.add(self.team)
        return event

    def physio_client_login(self):
        position = Position.objects.create(club=self.club, name="Physio", short_name="PHY", staff_position=True, management_position=False)
        user = User.objects.create_user(email="physio-shell@example.com", password="pw-secret-123")
        member = Member.objects.create(first_name="Pat", last_name="Physio", user=user)
        StaffAssignment.objects.create(team=self.team, member=member, season=self.season, position=position)
        self.client.force_login(user)

    def get(self, url, **headers):
        return self.client.get(url, HTTP_HOST=HOST, **headers)

    def post(self, url, data=None, **headers):
        return self.client.post(url, data or {}, HTTP_HOST=HOST, **headers)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class CoachShellTests(CoachFixtureMixin, TestCase):
    def test_manager_mode_uses_the_member_shell_with_four_tabs(self):
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_today"))

        self.assertContains(response, 'class="tab-bar sticky bottom-0 z-20"')
        self.assertNotContains(response, "coach-tab-bar")
        self.assertNotContains(response, "coach-header")
        for url_name in ("coach_today", "coach_schedule", "coach_squad"):
            self.assertContains(response, f'href="{reverse(f"mobile:{url_name}")}"')
        self.assertContains(response, "app-header")

    def test_the_member_manager_switcher_crosses_shells_without_a_forced_reload(self):
        self.client.force_login(self.user)

        coach = self.get(reverse("mobile:coach_today"))
        member = self.get(reverse("mobile:home"))

        self.assertContains(coach, "role-switcher-item-active-ice")
        self.assertNotContains(member, 'hx-boost="false">Manager')
        self.assertNotContains(coach, 'hx-boost="false">Member')

    def test_pull_to_refresh_is_enabled_on_today_and_events(self):
        self.client.force_login(self.user)

        for url_name in ("coach_today", "coach_schedule"):
            response = self.get(reverse(f"mobile:{url_name}"))
            self.assertContains(response, "<main", msg_prefix=url_name)
            self.assertRegex(response.content.decode(), r"<main[^>]*data-refreshable", msg=url_name)

    def test_the_sheet_host_is_present_for_the_event_sheet(self):
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_today"))

        self.assertContains(response, 'id="sheet-host"')

    def test_team_chips_switch_team_on_the_tab_you_are_on(self):
        other = Team.objects.create(club=self.club, name="U14", short_name="U14")
        StaffAssignment.objects.create(team=other, member=self.member, season=self.season, position=self.position)
        self.client.force_login(self.user)

        events_tab = self.get(reverse("mobile:coach_schedule"))
        deeper = self.get(reverse("mobile:coach_add_staff"))

        self.assertContains(events_tab, f'href="{reverse("mobile:coach_schedule")}?team={other.pk}"')
        self.assertContains(deeper, f'href="{reverse("mobile:coach_today")}?team={other.pk}"')

    def test_header_shows_the_active_team_and_role(self):
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_today"))

        self.assertContains(response, "U16 &middot; Head coach")

    def test_a_single_team_has_no_chip_row(self):
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_today"))

        self.assertNotContains(response, 'aria-label="Team"')


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class CoachTodayCardTests(CoachFixtureMixin, TestCase):
    def test_empty_state_when_nothing_is_planned(self):
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_today"))

        self.assertContains(response, "Nothing planned for this team yet.")

    def test_session_card_has_in_out_no_reply_counters_and_opens_the_sheet(self):
        event = self.make_event(days=3, deadline=timezone.now() + datetime.timedelta(days=1))
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_today"))

        self.assertContains(response, "No reply")
        self.assertContains(response, "Next up")
        self.assertContains(response, "Replies close")
        self.assertContains(response, f'hx-get="{reverse("mobile:coach_event_detail", args=[event.pk])}"')
        self.assertTrue(response.context["replies_open"])

    def test_a_past_deadline_reads_replies_closed(self):
        self.make_event(days=3, deadline=timezone.now() - datetime.timedelta(hours=1))
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_today"))

        self.assertContains(response, "Replies closed")
        self.assertFalse(response.context["replies_open"])


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class CoachEventsTabTests(CoachFixtureMixin, TestCase):
    def test_kind_chips_filter_the_list(self):
        self.make_event("Tuesday practice")
        self.make_event("Saturday game", Event.EventKind.GAME, days=4)
        self.client.force_login(self.user)

        everything = self.get(reverse("mobile:coach_schedule"))
        games = self.get(reverse("mobile:coach_schedule") + "?kind=game")

        self.assertContains(everything, "Tuesday practice")
        self.assertContains(everything, "Saturday game")
        self.assertContains(games, "Saturday game")
        self.assertNotContains(games, "Tuesday practice")
        self.assertEqual(games.context["kind_filter"], "game")
        self.assertContains(everything, "?kind=tournament")

    def test_an_unknown_kind_falls_back_to_all(self):
        self.make_event("Tuesday practice")
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_schedule") + "?kind=social")

        self.assertEqual(response.context["kind_filter"], "")
        self.assertContains(response, "Tuesday practice")

    def test_empty_state(self):
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_schedule"))

        self.assertContains(response, "No events")
        self.assertContains(response, "Nothing is planned for this team.")

    def test_rows_open_the_sheet_and_show_series_and_score_marks(self):
        series = EventSeries.objects.create(club=self.club, title="Weekly", kind=Event.EventKind.GAME, rrule="FREQ=WEEKLY;BYDAY=SA", dtstart=timezone.now())
        game = self.make_event("Big game", Event.EventKind.GAME, series=series, is_live=True)
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_schedule"))

        self.assertContains(response, f'hx-get="{reverse("mobile:coach_event_detail", args=[game.pk])}"')
        self.assertContains(response, 'hx-target="#sheet-host"')
        self.assertContains(response, "LIVE")
        self.assertContains(response, 'title="Repeats"')

    def test_add_button_is_for_managers_only(self):
        self.client.force_login(self.user)
        self.assertContains(self.get(reverse("mobile:coach_schedule")), reverse("mobile:coach_create_event"))

        self.client.logout()
        self.physio_client_login()
        self.assertNotContains(self.get(reverse("mobile:coach_schedule")), reverse("mobile:coach_create_event"))


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class CoachEventSheetTests(CoachFixtureMixin, TestCase):
    def test_an_htmx_fetch_returns_just_the_sheet(self):
        event = self.make_event("Big game", Event.EventKind.GAME, score_for=3, score_against=1)
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_event_detail", args=[event.pk]), HTTP_HX_REQUEST="true")

        self.assertContains(response, '<dialog class="sheet sheet-large" id="event-sheet"')
        self.assertNotContains(response, "<html")
        self.assertContains(response, "sheet-handle")
        self.assertIn("HX-Request", response["Vary"])
        self.assertTemplateUsed(response, "mobile/coach/_event_sheet.html")

    def test_a_boosted_navigation_or_plain_visit_still_gets_the_full_page(self):
        event = self.make_event()
        self.client.force_login(self.user)
        url = reverse("mobile:coach_event_detail", args=[event.pk])

        plain = self.get(url)
        boosted = self.get(url, HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")

        for response in (plain, boosted):
            self.assertContains(response, "<html")
            self.assertNotContains(response, 'id="event-sheet"')
            self.assertTemplateUsed(response, "mobile/coach/event_detail.html")

    def test_a_game_shows_its_score_row_and_kind_chip(self):
        event = self.make_event("Big game", Event.EventKind.GAME, score_for=3, score_against=1, is_friendly=True)
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_event_detail", args=[event.pk]), HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Result"')
        self.assertContains(response, "pill-danger")
        self.assertContains(response, "Friendly")
        self.assertContains(response, reverse("mobile:coach_lineup", args=[event.pk]))

    def test_a_practice_has_no_score_row_or_lineup_link(self):
        event = self.make_event()
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_event_detail", args=[event.pk]), HTTP_HX_REQUEST="true")

        self.assertNotContains(response, 'aria-label="Result"')
        self.assertNotContains(response, reverse("mobile:coach_lineup", args=[event.pk]))

    def test_manager_actions_for_a_one_off_event(self):
        event = self.make_event()
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_event_detail", args=[event.pk]), HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="http://x/app/coach/schedule/?kind=training")

        self.assertContains(response, 'id="event-delete-sheet"')
        self.assertContains(response, "Delete this event")
        self.assertNotContains(response, 'id="event-detach-sheet"')
        self.assertNotContains(response, "Cancel it but keep the record")
        # The editor link remembers which tab the sheet was opened over.
        self.assertContains(response, "next=%2Fapp%2Fcoach%2Fschedule%2F%3Fkind%3Dtraining")

    def test_series_occurrence_offers_pattern_detach_and_the_three_way_delete(self):
        series = EventSeries.objects.create(club=self.club, title="Weekly Practice", kind=Event.EventKind.TRAINING, rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=MO", dtstart=timezone.now())
        event = self.make_event(series=series)
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_event_detail", args=[event.pk]), HTTP_HX_REQUEST="true")

        self.assertContains(response, reverse("mobile:coach_edit_event_series", args=[series.pk]))
        self.assertContains(response, 'id="event-detach-sheet"')
        self.assertContains(response, "Stop following the series?")
        self.assertContains(response, "Cancel it but keep the record")
        self.assertContains(response, "Remove this event")
        self.assertContains(response, "Delete the whole series")
        self.assertContains(response, 'id="event-series-sheet"')
        self.assertContains(response, "Weekly Practice")

    def test_a_detached_occurrence_has_no_stop_following_action(self):
        series = EventSeries.objects.create(club=self.club, title="Weekly Practice", kind=Event.EventKind.TRAINING, rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=MO", dtstart=timezone.now())
        event = self.make_event(series=series, detached=True)
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_event_detail", args=[event.pk]), HTTP_HX_REQUEST="true")

        self.assertNotContains(response, 'id="event-detach-sheet"')
        self.assertContains(response, "edited on its own")

    def test_a_non_manager_gets_no_actions(self):
        event = self.make_event()
        self.physio_client_login()

        response = self.get(reverse("mobile:coach_event_detail", args=[event.pk]), HTTP_HX_REQUEST="true")

        self.assertNotContains(response, reverse("mobile:coach_event_delete", args=[event.pk]))
        self.assertNotContains(response, reverse("mobile:coach_edit_event", args=[event.pk]))
        self.assertNotContains(response, "event-delete-sheet")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class CoachEventDeleteTests(CoachFixtureMixin, TestCase):
    def make_series_event(self):
        series = EventSeries.objects.create(club=self.club, title="Weekly Practice", kind=Event.EventKind.TRAINING, rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=MO", dtstart=timezone.now())
        series.teams.add(self.team)
        return series, self.make_event(series=series)

    def delete(self, event, **data):
        return self.post(reverse("mobile:coach_event_delete", args=[event.pk]), data)

    def test_a_one_off_event_is_deleted_and_lands_on_the_events_list(self):
        event = self.make_event()
        self.client.force_login(self.user)

        response = self.delete(event, mode="delete")

        self.assertRedirects(response, reverse("mobile:coach_schedule"), fetch_redirect_response=False)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_telling_the_players_notifies_before_deleting(self):
        event = self.make_event()
        self.client.force_login(self.user)

        with patch("mobile.coach_views.notify_event_cancelled") as notify_cancelled:
            self.delete(event, mode="delete", notify="on")

        notify_cancelled.assert_called_once()
        self.assertFalse(notify_cancelled.call_args.kwargs["keep_link"])

    def test_not_telling_the_players_stays_silent(self):
        event = self.make_event()
        self.client.force_login(self.user)

        with patch("mobile.coach_views.notify_event_cancelled") as notify_cancelled:
            self.delete(event, mode="delete")

        notify_cancelled.assert_not_called()

    def test_an_occurrence_can_be_cancelled_but_kept(self):
        series, event = self.make_series_event()
        self.client.force_login(self.user)

        self.delete(event, mode="cancel")

        event.refresh_from_db()
        self.assertTrue(event.cancelled)
        series.refresh_from_db()
        self.assertIn(event.start.isoformat(), series.excluded_dates)

    def test_an_occurrence_can_be_removed_without_regenerating(self):
        series, event = self.make_series_event()
        self.client.force_login(self.user)

        self.delete(event, mode="remove")

        self.assertFalse(Event.objects.filter(pk=event.pk).exists())
        series.refresh_from_db()
        self.assertIn(event.start.isoformat(), series.excluded_dates)

    def test_a_plain_delete_on_an_occurrence_behaves_like_remove(self):
        series, event = self.make_series_event()
        self.client.force_login(self.user)

        self.delete(event, mode="delete")

        self.assertFalse(Event.objects.filter(pk=event.pk).exists())
        self.assertTrue(EventSeries.objects.filter(pk=series.pk).exists())

    def test_the_whole_series_can_be_deleted(self):
        series, event = self.make_series_event()
        sibling = self.make_event("Next week", series=series, days=9)
        self.client.force_login(self.user)

        self.delete(event, mode="series")

        self.assertFalse(EventSeries.objects.filter(pk=series.pk).exists())
        self.assertFalse(Event.objects.filter(pk__in=[event.pk, sibling.pk]).exists())

    def test_series_mode_on_a_one_off_event_is_a_bad_request(self):
        event = self.make_event()
        self.client.force_login(self.user)

        response = self.delete(event, mode="series")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_an_unknown_mode_is_a_bad_request(self):
        event = self.make_event()
        self.client.force_login(self.user)

        self.assertEqual(self.delete(event, mode="nuke").status_code, 400)

    def test_a_non_manager_cannot_delete(self):
        event = self.make_event()
        self.physio_client_login()

        response = self.delete(event, mode="delete")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_another_teams_event_404s(self):
        other = Team.objects.create(club=self.club, name="U14", short_name="U14")
        event = Event.objects.create(club=self.club, title="Other", kind=Event.EventKind.TRAINING, start=timezone.now() + datetime.timedelta(days=1))
        event.teams.add(other)
        self.client.force_login(self.user)

        self.assertEqual(self.delete(event, mode="delete").status_code, 404)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_get_is_not_allowed(self):
        event = self.make_event()
        self.client.force_login(self.user)

        self.assertEqual(self.get(reverse("mobile:coach_event_delete", args=[event.pk])).status_code, 405)

    def test_next_returns_to_the_tab_the_sheet_was_opened_over(self):
        event = self.make_event()
        self.client.force_login(self.user)

        response = self.delete(event, mode="delete", next=reverse("mobile:coach_today"))

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)

    def test_next_never_redirects_off_site_or_to_the_deleted_event(self):
        event = self.make_event()
        other = self.make_event("Other", days=5)
        self.client.force_login(self.user)

        offsite = self.delete(event, mode="delete", next="https://evil.example/phish")
        to_deleted = self.delete(other, mode="delete", next=reverse("mobile:coach_event_detail", args=[other.pk]))

        self.assertRedirects(offsite, reverse("mobile:coach_schedule"), fetch_redirect_response=False)
        self.assertRedirects(to_deleted, reverse("mobile:coach_schedule"), fetch_redirect_response=False)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class CoachDetachTests(CoachFixtureMixin, TestCase):
    def test_detach_returns_to_next_when_given(self):
        series = EventSeries.objects.create(club=self.club, title="Weekly", kind=Event.EventKind.TRAINING, rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=MO", dtstart=timezone.now())
        event = self.make_event(series=series)
        self.client.force_login(self.user)

        response = self.post(reverse("mobile:coach_event_detach", args=[event.pk]), {"next": reverse("mobile:coach_schedule")})

        self.assertRedirects(response, reverse("mobile:coach_schedule"), fetch_redirect_response=False)
        event.refresh_from_db()
        self.assertTrue(event.detached)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class CoachEventEditorTests(CoachFixtureMixin, TestCase):
    def make_series(self):
        series = EventSeries.objects.create(club=self.club, title="Weekly Practice", kind=Event.EventKind.TRAINING, rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=MO", dtstart=timezone.now())
        series.teams.add(self.team)
        first = self.make_event("Weekly Practice", series=series, days=2)
        second = self.make_event("Weekly Practice", series=series, days=9)
        third = self.make_event("Weekly Practice", series=series, days=16)
        return series, first, second, third

    def edit(self, event, **overrides):
        start = timezone.localtime(event.start).strftime("%Y-%m-%dT%H:%M")
        data = {"kind": "training", "title": "Weekly Practice", "start": start, "teams": [str(self.team.pk)], "max_referees": "2"}
        data.update(overrides)
        return self.post(reverse("mobile:coach_edit_event", args=[event.pk]), data)

    def test_scope_choices_only_show_for_an_occurrence_that_follows_its_series(self):
        _series, first, second, _third = self.make_series()
        second.detached = True
        second.save()
        one_off = self.make_event("One-off", days=4)
        self.client.force_login(self.user)

        attached = self.get(reverse("mobile:coach_edit_event", args=[first.pk]))
        detached = self.get(reverse("mobile:coach_edit_event", args=[second.pk]))
        plain = self.get(reverse("mobile:coach_edit_event", args=[one_off.pk]))

        self.assertContains(attached, 'value="this_and_following"')
        self.assertContains(attached, "All upcoming events")
        self.assertNotContains(detached, 'value="this_and_following"')
        self.assertNotContains(plain, 'value="this_and_following"')

    def test_only_this_event_detaches_the_occurrence(self):
        _series, first, second, _third = self.make_series()
        self.client.force_login(self.user)

        self.edit(first, title="Special practice", scope="this")

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.title, "Special practice")
        self.assertTrue(first.detached)
        self.assertEqual(second.title, "Weekly Practice")

    def test_all_upcoming_events_updates_the_series_and_its_occurrences(self):
        series, first, second, third = self.make_series()
        self.client.force_login(self.user)

        self.edit(first, title="Renamed practice", scope="all")

        series.refresh_from_db()
        self.assertEqual(series.title, "Renamed practice")
        for event in (first, second, third):
            event.refresh_from_db()
            self.assertEqual(event.title, "Renamed practice")
            self.assertFalse(event.detached)

    def test_this_and_following_splits_the_series(self):
        series, first, second, third = self.make_series()
        self.client.force_login(self.user)

        self.edit(second, title="From now on", scope="this_and_following")

        for event in (first, second, third):
            event.refresh_from_db()
        self.assertEqual(first.title, "Weekly Practice")
        self.assertEqual(second.title, "From now on")
        self.assertEqual(third.title, "From now on")
        self.assertEqual(first.series_id, series.pk)
        self.assertNotEqual(second.series_id, series.pk)
        self.assertEqual(second.series_id, third.series_id)

    def test_a_change_the_series_does_not_carry_leaves_the_occurrence_attached(self):
        _series, first, _second, _third = self.make_series()
        self.client.force_login(self.user)

        self.edit(first, scope="this", max_referees="3")

        first.refresh_from_db()
        self.assertFalse(first.detached)
        self.assertEqual(first.max_referees, 3)

    def test_an_unknown_scope_means_only_this_event(self):
        _series, first, second, _third = self.make_series()
        self.client.force_login(self.user)

        self.edit(first, title="Odd", scope="everything")

        second.refresh_from_db()
        self.assertEqual(second.title, "Weekly Practice")

    def test_saving_keeps_an_end_time_the_screen_has_no_input_for(self):
        event = self.make_event(end=(timezone.now() + datetime.timedelta(days=2, hours=2)).replace(second=0, microsecond=0))
        end = event.end
        self.client.force_login(self.user)

        self.edit(event, title="Longer practice")

        event.refresh_from_db()
        self.assertEqual(event.end, end)

    def test_telling_the_players_about_a_change_is_opt_in(self):
        event = self.make_event()
        self.client.force_login(self.user)

        with patch("mobile.coach_views.notify_event_changed") as changed:
            self.edit(event, title="Quiet change")
            changed.assert_not_called()
            self.edit(event, title="Loud change", notify="on")
            changed.assert_called_once()

    def test_next_sends_the_editor_back_to_the_tab_it_was_opened_over(self):
        event = self.make_event()
        self.client.force_login(self.user)

        response = self.edit(event, title="Back to list", next=reverse("mobile:coach_schedule"))

        self.assertRedirects(response, reverse("mobile:coach_schedule"), fetch_redirect_response=False)

    def test_the_editor_cancel_link_follows_next(self):
        event = self.make_event()
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_edit_event", args=[event.pk]) + "?next=" + reverse("mobile:coach_schedule"))

        self.assertContains(response, f'href="{reverse("mobile:coach_schedule")}"')
        self.assertContains(response, 'name="next"')

    def test_the_series_editor_explains_that_only_future_dates_change(self):
        series, *_rest = self.make_series()
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_edit_event_series", args=[series.pk]))

        self.assertContains(response, "only decides future dates")
        self.assertNotContains(response, 'name="notify"')


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class CoachCreateEventTests(CoachFixtureMixin, TestCase):
    def create(self, **overrides):
        start = timezone.localtime(timezone.now() + datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M")
        data = {"kind": "training", "title": "New practice", "start": start, "teams": [str(self.team.pk)], "max_referees": "2", "notify_present": "1"}
        data.update(overrides)
        return self.post(reverse("mobile:coach_create_event"), data)

    def test_the_tell_the_players_box_is_checked_by_default(self):
        self.client.force_login(self.user)

        response = self.get(reverse("mobile:coach_create_event"))

        self.assertRegex(response.content.decode(), r'name="notify"\s+checked')
        self.assertContains(response, 'name="notify_present"')

    def test_unticking_it_skips_the_notification(self):
        self.client.force_login(self.user)

        with patch("mobile.coach_views.dispatch_notify_new_event") as dispatch:
            self.create()

        dispatch.assert_not_called()
        self.assertTrue(Event.objects.filter(title="New practice").exists())

    def test_ticking_it_notifies(self):
        self.client.force_login(self.user)

        with patch("mobile.coach_views.dispatch_notify_new_event") as dispatch:
            self.create(notify="on")

        dispatch.assert_called_once()

    def test_a_form_without_the_box_at_all_keeps_notifying(self):
        self.client.force_login(self.user)

        with patch("mobile.coach_views.dispatch_notify_new_event") as dispatch:
            self.create(notify_present="")
            data_without = {"kind": "training", "title": "Old client", "start": timezone.localtime(timezone.now() + datetime.timedelta(days=6)).strftime("%Y-%m-%dT%H:%M"), "teams": [str(self.team.pk)], "max_referees": "2"}
            self.post(reverse("mobile:coach_create_event"), data_without)

        self.assertEqual(dispatch.call_count, 1)

    def test_a_tournament_can_be_created_with_a_competition_and_friendly_flag(self):
        flag = get_waffle_flag_model().objects.create(name="regional-league")
        flag.clubs.add(self.club)
        Competition.objects.create(name="Regional", module="none", flag=flag)
        self.client.force_login(self.user)

        with patch("mobile.coach_views.dispatch_notify_new_event"):
            response = self.create(kind="tournament", title="Spring Cup", competition="Regional", is_friendly="on")

        self.assertRedirects(response, reverse("mobile:coach_today"), fetch_redirect_response=False)
        cup = Event.objects.get(title="Spring Cup")
        self.assertEqual(cup.kind, Event.EventKind.TOURNAMENT)
        self.assertEqual(cup.competition, "Regional")
        self.assertTrue(cup.is_friendly)

    def test_the_competition_card_applies_to_tournaments_but_the_opponent_only_to_games(self):
        self.client.force_login(self.user)

        content = self.get(reverse("mobile:coach_create_event")).content.decode()

        self.assertIn("(kind === 'game' || kind === 'tournament')", content)
        self.assertIn("""x-show="kind === 'game'" x-cloak class="m-card p-4">""", content)
