"""The desktop Calendar's iPad-style split: month grid left, agenda / selected-day list right."""

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .test_desktop_screens import DESKTOP_UA
from .test_member_screens import MemberScreenFixture, Status, at, setUpModule, tearDownModule  # noqa: F401 (module fixtures)

PHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class DesktopCalendarSplitTests(MemberScreenFixture, TestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def test_desktop_shows_grid_and_agenda_side_by_side(self):
        event = self.make_event(title="Practice", start=at(1))
        self.invite(event, self.member, Status.NO_RESPONSE)

        response = self.get(reverse("mobile:calendar"), HTTP_USER_AGENT=DESKTOP_UA)

        self.assertContains(response, 'id="cal-grid"')
        self.assertContains(response, 'id="cal-list"')
        self.assertContains(response, "month-cell")
        self.assertContains(response, "Practice")
        self.assertContains(response, "This week")
        self.assertNotContains(response, "header-seg")

    def test_day_select_lists_only_that_day(self):
        first = self.make_event(title="Early practice", start=at(1))
        second = self.make_event(title="Late cup game", start=at(3))
        self.invite(first, self.member, Status.NO_RESPONSE)
        self.invite(second, self.member, Status.NO_RESPONSE)
        day = timezone.localtime(second.start).date()

        response = self.get(reverse("mobile:calendar") + f"?month={day:%Y-%m}&day={day.isoformat()}", HTTP_USER_AGENT=DESKTOP_UA, HTTP_HX_REQUEST="true")

        self.assertContains(response, 'id="cal-list"')
        self.assertContains(response, "Late cup game")
        self.assertNotContains(response, "Early practice")
        self.assertContains(response, 'aria-current="date"')

    def test_phone_keeps_list_and_month_modes(self):
        event = self.make_event(title="Practice", start=at(1))
        self.invite(event, self.member, Status.NO_RESPONSE)

        listing = self.get(reverse("mobile:calendar"), HTTP_USER_AGENT=PHONE_UA)
        self.assertNotContains(listing, 'id="cal-split"')
        self.assertContains(listing, "Practice")

        month = self.get(reverse("mobile:calendar") + "?view=month", HTTP_USER_AGENT=PHONE_UA)
        self.assertContains(month, 'id="month-pane"')
        self.assertNotContains(month, 'id="cal-split"')
