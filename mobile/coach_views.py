"""Coach-mode screens (C1-C6) -- design_handoff_rosterchief_platform/README.md's
"Coach mode (mobile, dark chrome)" section. Kept separate from mobile/views.py
(Member mode, M1-M7) since the two modes share almost no view logic beyond the
club/season plumbing already factored into club.services.access -- see
mobile/coach_mixins.py's CoachScopeMixin for the shared scaffolding.
"""

from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.generic import TemplateView

from club.models import Season
from club.services.access import can_add_news, current_season
from controlpanel.messages import notify
from events.models import Attendance, Event
from events.services.attendance import record_check_in
from events.tasks import notify_new_event
from management.forms import EventForm, NewsForm
from news.models import News
from news.services import notify_editors_of_pending_review
from teams.models import Team, TeamMembership
from teams.services import eligible_roster_members

from .coach_mixins import CoachScopeMixin
from .forms import _INPUT_CLASSES

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


class CoachCreateEventView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C4 -- reuses management.forms.EventForm as-is: its own __init__ already
    scopes ``teams`` to teams_managed_by(user, club) via EventAudienceFormMixin,
    exactly the restriction a coach needs, so there's nothing to re-scope.

    Only a subset of the mock's fields is rendered in the template (Title,
    Kind, Teams, Location, Start, Answers close) -- everything else EventForm
    carries (groups/club_wide/invited & excluded members/opponent/
    competition/external id) stays unrendered and simply unset; all of it is
    optional on the model, so an unrendered field validates cleanly empty.
    "Repeat weekly" from the mock isn't built this stage -- the recurring-
    series machinery (EventSeriesForm) is a separate form with its own
    fields; wiring it in is later work, not something to fake with an inert
    toggle here.

    After a successful save: the same notify_new_event.delay(...) call
    management.views.EventCreateView.form_valid makes -- attendance sync is
    automatic via events/signals.py, only the notification dispatch needs
    replicating by hand for a view that isn't a CreateView.
    """

    template_name = "mobile/coach/event_form.html"
    screen_title = _("New event")
    active_tab = "coach_today"

    def get(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseRedirect(reverse("mobile:coach_today"))
        return super().get(request, *args, **kwargs)

    def build_form(self, data=None):
        instance = Event(club=self.request.club, created_by=self.me)
        form = EventForm(data, club=self.request.club, user=self.request.user, editing=False, instance=instance)
        # max_referees has a model default (2) but no blank=True, so the form
        # field is required despite it -- delete it rather than render a
        # referee-count control this screen has no use for; construct_instance
        # skips deleted fields entirely, leaving the instance's own default.
        del form.fields["max_referees"]
        # The desktop searchable multi-select relies on management's own JS
        # widget, not loaded here -- plain checkboxes work without it and
        # read better on a phone regardless.
        form.fields["teams"].widget = forms.CheckboxSelectMultiple()
        if self.active_team is not None and data is None:
            form.fields["teams"].initial = [self.active_team.pk]
        # Same input styling as mobile.forms.MemberProfileForm (M6) -- one
        # visual language for every text/date field across the app, not a
        # diverging one for this screen.
        for field_name in ("title", "start", "location", "deadline"):
            form.fields[field_name].widget.attrs["class"] = _INPUT_CLASSES
        return form

    def get_context_data(self, **kwargs):
        kwargs.setdefault("form", self.build_form())
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        form = self.build_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        event = form.save()
        body = _("“%(event)s” created.") % {"event": event}
        notify(request, f"s|{_('Event created')}|{body}")
        # A deliberately-planned single event, same as the desktop create
        # flow -- see notify_new_event's own docstring for why a recurring
        # series' occurrences aren't wired to this.
        notify_new_event.delay(str(event.pk))
        return HttpResponseRedirect(reverse("mobile:coach_today"))


class CoachCreateNewsView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C5 -- reuses management.forms.NewsForm, re-scoped to the coach's own
    managed team(s). NewsForm.__init__ defaults ``teams`` to every club team
    -- fine for an editor/admin, but a real gap for a coach, who should only
    ever be able to post as their own team, never "on behalf of" one they
    don't run.

    Gated with club.services.access.can_add_news, which already includes
    is_coach_manager -- no new authorization logic needed. On submit, the
    post is handed straight to News.submit_for_review() (plus the same
    notify_editors_of_pending_review call the desktop's own
    NewsSubmitForReviewView makes) rather than left a silent draft, so it
    actually reaches an editor's queue -- can_publish_news stays editor/
    admin-only, so "Send for review" is the honest label here, not "Publish"
    the way the design mock has it.

    title_en/body_en (the optional English fallback) and a cover photo
    aren't part of this screen -- both text fields are blank=True on the
    model (a coach posting from their phone isn't expected to also draft an
    English translation), and News has no image field for a cover at all to
    begin with. ``visibility`` is left at the model's own default
    (INTERNAL -- team families, in-app only) rather than building the mock's
    "also on club website" toggle: a coach's post always lands as
    PENDING_REVIEW first, and an editor reviewing it can widen visibility
    before publishing if a public-site placement is actually warranted --
    that's a real gate, not a decorative row, so it isn't reproduced here as
    one.
    """

    template_name = "mobile/coach/news_form.html"
    screen_title = _("New post")
    active_tab = "coach_today"

    def get(self, request, *args, **kwargs):
        if not can_add_news(request.user, request.club):
            return HttpResponseRedirect(reverse("mobile:coach_today"))
        return super().get(request, *args, **kwargs)

    def build_form(self, data=None):
        instance = News(club=self.request.club, created_by=self.me)
        form = NewsForm(data, club=self.request.club, instance=instance)
        del form.fields["title_en"]
        del form.fields["body_en"]
        del form.fields["visibility"]
        # A coach's post is always about their own team(s) -- never empty
        # (which News.teams's own help_text defines as "club-wide", an
        # editor/admin-only claim), and never any other team in the club.
        form.fields["teams"].queryset = Team.objects.filter(pk__in=[team.pk for team in self.managed_teams])
        form.fields["teams"].required = True
        form.fields["teams"].widget = forms.CheckboxSelectMultiple()
        if self.active_team is not None and data is None:
            form.fields["teams"].initial = [self.active_team.pk]
        for field_name in ("title", "body"):
            form.fields[field_name].widget.attrs["class"] = _INPUT_CLASSES
        return form

    def get_context_data(self, **kwargs):
        kwargs.setdefault("form", self.build_form())
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if not can_add_news(request.user, request.club):
            return HttpResponseForbidden()

        form = self.build_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        news_item = form.save()
        news_item.submit_for_review()
        notify_editors_of_pending_review(news_item)

        body = _("“%(news)s” is ready for review.") % {"news": news_item}
        notify(request, f"s|{_('Sent for review')}|{body}")
        return HttpResponseRedirect(reverse("mobile:coach_today"))


class CoachAddPlayerView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C6 -- bulk-add players to the active team's roster: a checkbox per
    candidate rather than management.forms.TeamMembershipForm's one-member-
    at-a-time shape, which doesn't fit a "tap a few names, add them" flow
    anyway (the mock itself shows plain checkboxes, no inline position
    picker). A coach sets jersey number/position afterward on the desktop --
    same as any roster spot added blank via the Sign-up page today
    (TeamMembership.position's own help_text already documents this as a
    normal, expected state, not a shortcut this screen invents).

    The pool is teams.services.eligible_roster_members(club) minus whoever's
    already on this team+season -- the same two rules TeamMembershipForm
    applies internally, just reused directly rather than through the form.
    "Suggested" (on this team last season) is real, computed data. "Age
    eligible" from the mock isn't built -- neither Club nor Team carries an
    age-group field to compare a birth date against, so faking that filter
    would just mean it silently matched nothing.
    """

    template_name = "mobile/coach/add_player.html"
    screen_title = _("Add players")
    active_tab = "coach_today"

    #: ?filter= values this screen understands -- anything else (including no
    #: param at all) means "All".
    FILTERS = {"suggested", "no_team"}

    def get(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseRedirect(reverse("mobile:coach_today"))
        return super().get(request, *args, **kwargs)

    def _candidate_pool(self, season):
        taken = TeamMembership.objects.filter(team=self.active_team, season=season).values_list("member_id", flat=True)
        return eligible_roster_members(self.request.club).exclude(pk__in=taken)

    def get_context_data(self, **kwargs):
        season = current_season(self.request.club)
        candidates = []
        squad_count = 0

        filter_param = self.request.GET.get("filter")
        if filter_param not in self.FILTERS:
            filter_param = ""

        if self.active_team is not None and season is not None:
            squad_count = TeamMembership.objects.filter(team=self.active_team, season=season).count()
            pool = self._candidate_pool(season)

            if filter_param == "no_team":
                pool = pool.exclude(team_memberships__season=season)
            elif filter_param == "suggested":
                previous_season = Season.before(self.request.club, season)
                pool = pool.filter(team_memberships__team=self.active_team, team_memberships__season=previous_season) if previous_season is not None else pool.none()

            candidates = list(pool.distinct().order_by("last_name", "first_name"))

        return super().get_context_data(
            candidates=candidates,
            squad_count=squad_count,
            filter_param=filter_param,
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        season = current_season(request.club)
        if self.active_team is None or season is None:
            return HttpResponseForbidden()

        pool_ids = {str(pk) for pk in self._candidate_pool(season).values_list("pk", flat=True)}
        added = 0
        for member_id in request.POST.getlist("member"):
            if member_id not in pool_ids:
                continue
            TeamMembership.objects.get_or_create(team=self.active_team, season=season, member_id=member_id)
            added += 1

        if added:
            body = ngettext("%(count)d player added to the roster.", "%(count)d players added to the roster.", added) % {"count": added}
            notify(request, f"s|{_('Roster updated')}|{body}")
        return HttpResponseRedirect(reverse("mobile:coach_today"))
