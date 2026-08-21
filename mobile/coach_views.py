"""Coach-mode screens (C1-C6) -- design_handoff_rosterchief_platform/README.md's
"Coach mode (mobile, dark chrome)" section. Kept separate from mobile/views.py
(Member mode, M1-M7) since the two modes share almost no view logic beyond the
club/season plumbing already factored into club.services.access -- see
mobile/coach_mixins.py's CoachScopeMixin for the shared scaffolding.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from club.services.access import current_season
from controlpanel.messages import notify
from events.models import Attendance, Event
from events.services.attendance import record_check_in
from teams.models import TeamMembership

from .coach_mixins import CoachScopeMixin

#: RSVP states that count as "in" for the stat tile -- present/selected are an
#: explicit yes, maybe is still a lean-in rather than silence.
IN_STATUSES = [Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED, Attendance.AttendanceStatus.MAYBE]


class CoachTodayView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C1 -- three stat tiles (Squad/In/Silent) for the active team's next
    upcoming session, a "tonight's session" card when one is scheduled today,
    a "needs you" list, and an "Also yours" card surfacing the coach's own
    member-side RSVP obligation (the same hero_attendance/rsvp_closed pattern
    mobile.views.HomeView already computes, scoped to self.me only -- a
    coach's own obligations, not the whole roster's).

    "Needs you" is scoped down from the design mock to what has real backing
    data today: a silent-players count for the next session. The mock's
    line-up-not-published row and member-blocker row are deferred -- no
    Lineup model or coach-facing member-edit screen exists yet for either to
    link to (see the coach-mode implementation plan's later stages).
    """

    template_name = "mobile/coach/today.html"
    screen_title = _("Today")
    active_tab = "coach_today"

    def get_context_data(self, **kwargs):
        now = timezone.now()
        today = timezone.localdate()
        season = current_season(self.request.club)
        team = self.active_team

        squad_count = 0
        session_event = None
        tonight_event = None
        in_count = 0
        silent_count = 0
        needs_you = []

        if team is not None:
            squad_count = TeamMembership.objects.filter(team=team, season=season).count() if season is not None else 0

            upcoming = Event.objects.filter(teams=team, cancelled=False, start__gte=now).order_by("start")
            tonight_event = upcoming.filter(start__date=today).first()
            session_event = tonight_event or upcoming.first()

            if session_event is not None:
                attendances = Attendance.objects.filter(event=session_event)
                in_count = attendances.filter(status__in=IN_STATUSES).count()
                silent_count = attendances.filter(status=Attendance.AttendanceStatus.NO_RESPONSE).count()
                if silent_count > 0:
                    needs_you.append({"severity": "warn", "title": _("Silent players"), "detail": _("%(count)d haven't answered yet") % {"count": silent_count}})

        hero_attendance = None
        rsvp_closed = False
        if self.me is not None:
            my_upcoming = Attendance.objects.filter(
                member=self.me,
                event__club=self.request.club,
                event__cancelled=False,
                event__start__gte=now,
            ).select_related("event", "event__location").order_by("event__start")
            hero_attendance = my_upcoming.first()
            if hero_attendance is not None:
                deadline = hero_attendance.event.deadline
                rsvp_closed = deadline is not None and deadline < now

        return super().get_context_data(
            squad_count=squad_count,
            session_event=session_event,
            tonight_event=tonight_event,
            in_count=in_count,
            silent_count=silent_count,
            needs_you=needs_you,
            hero_attendance=hero_attendance,
            rsvp_closed=rsvp_closed,
            **kwargs,
        )


class CoachAttendanceView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C2 -- bench attendance: check off who actually showed up to one event,
    separate from their RSVP. Writes through
    events.services.attendance.record_check_in, built for exactly this and
    previously uncalled anywhere in the codebase.

    Read-only for staff without a management position on the active team --
    can_manage_active_team hides the two-state control and the Save button in
    the template (hide, don't disable), and ``post`` 403s regardless, since a
    hidden control is still a client-side fact, not a real permission check.
    """

    template_name = "mobile/coach/attendance.html"
    screen_title = _("Attendance")
    active_tab = "coach_today"

    #: ?filter= values this screen understands -- anything else (including no
    #: param) means "All". "Goalies" matches on position name rather than a
    #: dedicated flag -- Position has no goalie-specific field to key off.
    FILTERS = {"silent", "goalies"}

    def get_event(self):
        if self.active_team is None:
            raise Http404
        return get_object_or_404(Event, pk=self.kwargs["event_id"], club=self.request.club, teams=self.active_team)

    def get_context_data(self, **kwargs):
        event = self.get_event()
        season = current_season(self.request.club)

        memberships_by_member = {}
        if season is not None:
            memberships_by_member = {tm.member_id: tm for tm in TeamMembership.objects.filter(team=self.active_team, season=season).select_related("position")}

        attendances = list(Attendance.objects.filter(event=event).select_related("member").order_by("member__last_name", "member__first_name"))
        for attendance in attendances:
            attendance.membership = memberships_by_member.get(attendance.member_id)
            attendance.is_silent = attendance.status == Attendance.AttendanceStatus.NO_RESPONSE

        filter_param = self.request.GET.get("filter")
        if filter_param not in self.FILTERS:
            filter_param = ""
        if filter_param == "silent":
            rows = [row for row in attendances if row.is_silent]
        elif filter_param == "goalies":
            rows = [row for row in attendances if row.membership and row.membership.position and "goal" in row.membership.position.name.lower()]
        else:
            rows = attendances

        return super().get_context_data(
            event=event,
            rows=rows,
            total_count=len(attendances),
            silent_count=sum(1 for row in attendances if row.is_silent),
            checked_in_count=sum(1 for row in attendances if row.showed_up is not None),
            filter_param=filter_param,
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        event = self.get_event()
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        checked_in = 0
        for attendance in Attendance.objects.filter(event=event):
            value = request.POST.get(f"showed_up_{attendance.pk}")
            if value in ("true", "false"):
                record_check_in(attendance, showed_up=value == "true")
                checked_in += 1

        title = _("Attendance saved")
        body = _("%(count)d players checked in.") % {"count": checked_in}
        notify(request, f"s|{title}|{body}")
        return HttpResponseRedirect(reverse("mobile:coach_today"))
