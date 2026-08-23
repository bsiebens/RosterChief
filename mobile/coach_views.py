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
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.generic import TemplateView, View

from club.models import Season
from club.services.access import can_add_news, current_season
from controlpanel.messages import notify
from events.models import Attendance, Event, Lineup, LineupSelection
from events.services.attendance import member_attendance_counts, record_check_in
from events.services.lineup import UNAVAILABLE_STATUSES, cancel_scheduled_publish, publish_lineup, schedule_lineup_publish, toggle_selection
from events.tasks import notify_new_event
from management.forms import EventForm, NewsForm
from news.models import News
from news.services import notify_editors_of_pending_review
from teams.models import Position, StaffAssignment, Team, TeamMembership
from teams.services import eligible_roster_members

from .coach_mixins import CoachScopeMixin
from .forms import _INPUT_CLASSES, CoachRosterEditForm

#: RSVP states that count as "in" for the stat tile -- present/selected are an
#: explicit yes, maybe is still a lean-in rather than silence.
IN_STATUSES = [Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED, Attendance.AttendanceStatus.MAYBE]

#: An explicit no -- declined or, for a published line-up, not selected.
#: Distinct from NO_RESPONSE ("silent"), which is a non-answer rather than a no.
OUT_STATUSES = [Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.EXCUSED, Attendance.AttendanceStatus.NOT_SELECTED]


class CoachTodayView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C1 -- three stat tiles (Squad/In/Silent) for the active team's next
    upcoming session, a session card for it (today's, if there's one on the
    calendar today, otherwise whichever is soonest), a "needs you" list, and
    an "Also yours" card surfacing the coach's own member-side RSVP
    obligation (the same hero_attendance/rsvp_closed pattern mobile.views.
    HomeView already computes, scoped to self.me only -- a coach's own
    obligations, not the whole roster's).

    "Needs you" is scoped down from the design mock to what has real backing
    data today: a silent-players count for the next session, plus an
    unpublished-line-up flag for *every* upcoming game within
    UPCOMING_GAMES_CHECKED (not just whichever happens to be the very next
    session -- a practice landing before Saturday's game shouldn't hide that
    the game's own line-up still needs building). The mock's member-blocker
    row stays deferred -- no coach-facing member-edit screen exists yet to
    link to.
    """

    template_name = "mobile/coach/today.html"
    screen_title = _("Today")
    active_tab = "coach_today"

    #: How many of the team's soonest upcoming games to check for a missing
    #: line-up -- unbounded would mean querying arbitrarily far into a full
    #: season; this many is already more advance notice than useful.
    UPCOMING_GAMES_CHECKED = 5

    def get_context_data(self, **kwargs):
        now = timezone.now()
        today = timezone.localdate()
        season = current_season(self.request.club)
        team = self.active_team

        squad_count = 0
        session_event = None
        tonight_event = None
        in_count = 0
        out_count = 0
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
                out_count = attendances.filter(status__in=OUT_STATUSES).count()
                silent_count = attendances.filter(status=Attendance.AttendanceStatus.NO_RESPONSE).count()
                if silent_count > 0:
                    needs_you.append(
                        {
                            "severity": "warn",
                            "title": _("Silent players"),
                            "detail": _("%(count)d haven't answered yet") % {"count": silent_count},
                            "action_label": _("Review"),
                            "action_url": reverse("mobile:coach_attendance", kwargs={"event_id": session_event.pk}) if self.can_manage_active_team else "",
                        }
                    )

            upcoming_games = list(upcoming.filter(kind=Event.EventKind.GAME)[: self.UPCOMING_GAMES_CHECKED])
            published_event_ids = set(Lineup.objects.filter(event__in=upcoming_games, published_at__isnull=False).values_list("event_id", flat=True))
            for game in upcoming_games:
                if game.pk in published_event_ids:
                    continue
                needs_you.append(
                    {
                        "severity": "club",
                        "title": _("Line-up not published"),
                        "detail": _("%(title)s · %(date)s") % {"title": game.title, "date": timezone.localtime(game.start).strftime("%a %d %b, %H:%M")},
                        "action_label": _("Build"),
                        "action_url": reverse("mobile:coach_lineup", kwargs={"event_id": game.pk}) if self.can_manage_active_team else "",
                    }
                )

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
            out_count=out_count,
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
    #: param) means "Responded" (IN_STATUSES: present/selected/maybe), the
    #: default view. A coach doesn't need to check in someone silent or
    #: declined -- neither is expected to show up -- so those are left out of
    #: the default rather than needing to be filtered away each time; each
    #: still gets its own chip to review who's in either bucket.
    FILTERS = {"silent", "declined"}

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
        elif filter_param == "declined":
            rows = [row for row in attendances if row.status in OUT_STATUSES]
        else:
            rows = [row for row in attendances if row.status in IN_STATUSES]

        return super().get_context_data(
            event=event,
            rows=rows,
            total_count=len(attendances),
            responded_count=sum(1 for row in attendances if row.status in IN_STATUSES),
            silent_count=sum(1 for row in attendances if row.is_silent),
            declined_count=sum(1 for row in attendances if row.status in OUT_STATUSES),
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


class CoachAddStaffView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """Squad screen's staff "Add" entry point -- the same bulk-checkbox shape
    as CoachAddPlayerView/C6, with one addition: StaffAssignment.position is
    required (unlike a roster spot's optional one), so there's a single
    position picker shared by however many candidates get checked, rather
    than a per-row picker that wouldn't fit this screen. Good enough for the
    common case (adding one or more assistants to the same role at once);
    assigning several people to different positions in one visit still means
    visiting this screen more than once.
    """

    template_name = "mobile/coach/add_staff.html"
    screen_title = _("Add staff")
    active_tab = "coach_today"

    def get(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseRedirect(reverse("mobile:coach_squad"))
        return super().get(request, *args, **kwargs)

    def _candidate_pool(self, season):
        taken = StaffAssignment.objects.filter(team=self.active_team, season=season).values_list("member_id", flat=True)
        return eligible_roster_members(self.request.club).exclude(pk__in=taken)

    def get_context_data(self, **kwargs):
        season = current_season(self.request.club)
        candidates = []
        positions = Position.objects.none()

        if self.active_team is not None and season is not None:
            candidates = list(self._candidate_pool(season).order_by("last_name", "first_name"))
            positions = Position.objects.filter(club=self.request.club, staff_position=True)

        return super().get_context_data(candidates=candidates, positions=positions, **kwargs)

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        season = current_season(request.club)
        if self.active_team is None or season is None:
            return HttpResponseForbidden()

        position = Position.objects.filter(club=request.club, staff_position=True, pk=request.POST.get("position")).first()
        if position is None:
            notify(request, f"e|{_('Could not add staff')}|{_('Pick a position first.')}")
            return HttpResponseRedirect(reverse("mobile:coach_add_staff"))

        pool_ids = {str(pk) for pk in self._candidate_pool(season).values_list("pk", flat=True)}
        added = 0
        for member_id in request.POST.getlist("member"):
            if member_id not in pool_ids:
                continue
            StaffAssignment.objects.get_or_create(team=self.active_team, season=season, member_id=member_id, defaults={"position": position})
            added += 1

        if added:
            body = ngettext("%(count)d staff member added.", "%(count)d staff members added.", added) % {"count": added}
            notify(request, f"s|{_('Staff updated')}|{body}")
        return HttpResponseRedirect(reverse("mobile:coach_squad"))


class CoachLineupView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C3 -- a game's line-up, kept deliberately simple: a plain yes/no pick
    per available roster player, grouped by their roster position ("category")
    so the coach reads it the same way the roster itself is grouped -- no
    lines, no slots, no drag-and-drop (an earlier build had units/slots with
    tap-to-place; replaced because it was harder to read than it needed to
    be for what's really just a selection call). One batch "Save line-up"
    submit, not a live per-tap POST -- same reasoning as coach/attendance.html.

    A Lineup is created lazily on first visit (get_or_create) -- there's no
    separate "start a line-up" step. Viewing is open to anyone staffing the
    team; saving/publishing is gated on can_manage_active_team, hidden in the
    template and 403'd here regardless.
    """

    template_name = "mobile/coach/lineup.html"
    screen_title = _("Line-up")
    active_tab = "coach_today"

    def get_event(self):
        if self.active_team is None:
            raise Http404
        return get_object_or_404(Event, pk=self.kwargs["event_id"], club=self.request.club, teams=self.active_team, kind=Event.EventKind.GAME)

    def _categories(self, event, lineup):
        """Available roster players, grouped by position -- same "category"
        the member-side published view groups by (mobile/views.py's
        EventDetailView). A player with no TeamMembership for this team/
        season (a guest call-up) lands in a catch-all "No position set"
        bucket rather than being dropped."""
        season = current_season(self.request.club)
        memberships_by_member = {}
        if season is not None:
            memberships_by_member = {tm.member_id: tm for tm in TeamMembership.objects.filter(team=self.active_team, season=season).select_related("position")}

        selected_ids = set(LineupSelection.objects.filter(lineup=lineup).values_list("member_id", flat=True))
        available = Attendance.objects.filter(event=event).exclude(status__in=UNAVAILABLE_STATUSES).select_related("member").order_by("member__last_name", "member__first_name")

        buckets = {}
        for attendance in available:
            membership = memberships_by_member.get(attendance.member_id)
            position = membership.position if membership else None
            key = position.pk if position else None
            bucket = buckets.setdefault(key, {"label": position.name if position else _("No position set"), "ordering": position.ordering if position else 9999, "rows": []})
            bucket["rows"].append({"member": attendance.member, "membership": membership, "selected": attendance.member_id in selected_ids})

        return sorted(buckets.values(), key=lambda bucket: (bucket["ordering"], bucket["label"]))

    def get_context_data(self, **kwargs):
        event = self.get_event()
        lineup, _created = Lineup.objects.get_or_create(event=event, defaults={"team": self.active_team, "created_by": self.me})
        unavailable = list(Attendance.objects.filter(event=event, status__in=UNAVAILABLE_STATUSES).select_related("member"))

        return super().get_context_data(
            event=event,
            lineup=lineup,
            categories=self._categories(event, lineup),
            unavailable=unavailable,
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = self.get_event()
        lineup, _created = Lineup.objects.get_or_create(event=event, defaults={"team": self.active_team, "created_by": self.me})
        selected_ids = set(LineupSelection.objects.filter(lineup=lineup).values_list("member_id", flat=True))
        available = Attendance.objects.filter(event=event).exclude(status__in=UNAVAILABLE_STATUSES).select_related("member")

        selected_count = 0
        for attendance in available:
            wants_selected = request.POST.get(f"selected_{attendance.member_id}") == "true"
            if wants_selected != (attendance.member_id in selected_ids):
                toggle_selection(lineup, attendance.member)
            if wants_selected:
                selected_count += 1

        title = _("Line-up saved")
        body = _("%(count)d player(s) selected.") % {"count": selected_count}
        notify(request, f"s|{title}|{body}")
        return HttpResponseRedirect(reverse("mobile:coach_lineup", kwargs={"event_id": event.pk}))


class CoachLineupPublishView(CoachScopeMixin, LoginRequiredMixin, View):
    """Publish now, schedule for a later time, or cancel a pending schedule --
    events.services.lineup.publish_lineup/schedule_lineup_publish/
    cancel_scheduled_publish do the actual work; events.tasks.
    publish_scheduled_lineups is the periodic sweep that catches a schedule
    once its time arrives. ``action`` picks which (default "publish_now",
    so the plain "Publish" button posts with no extra fields)."""

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = get_object_or_404(Event, pk=kwargs["event_id"], club=request.club, teams=self.active_team, kind=Event.EventKind.GAME)
        lineup = get_object_or_404(Lineup, event=event)
        action = request.POST.get("action", "publish_now")

        if action == "schedule":
            when = parse_datetime(request.POST.get("publish_at", ""))
            if when is not None and timezone.is_naive(when):
                when = timezone.make_aware(when)
            if when is None or when <= timezone.now():
                notify(request, f"e|{_('Could not schedule')}|{_('Pick a date and time in the future.')}")
            else:
                schedule_lineup_publish(lineup, when)
                notify(request, f"s|{_('Publish scheduled')}|{_('The line-up will publish itself automatically at that time.')}")
        elif action == "cancel_schedule":
            cancel_scheduled_publish(lineup)
            notify(request, f"s|{_('Schedule cancelled')}|{_('Publish it manually whenever you are ready.')}")
        else:
            publish_lineup(lineup)
            notify(request, f"s|{_('Line-up published')}|{_('Selected players have been notified.')}")

        return HttpResponseRedirect(reverse("mobile:coach_lineup", kwargs={"event_id": event.pk}))


class CoachSquadView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """Bottom-tab "Squad" -- the active team's roster and staff for the
    current season. Each roster row links through to CoachRosterMemberView
    for stats/contact/edit/remove; staff rows stay plain (no per-row action
    yet beyond the "Add" entry point below, which reuses CoachAddStaffView).
    """

    template_name = "mobile/coach/squad.html"
    screen_title = _("Squad")
    active_tab = "coach_squad"

    def get_context_data(self, **kwargs):
        season = current_season(self.request.club)
        roster, staff = [], []
        if self.active_team is not None and season is not None:
            roster = list(TeamMembership.objects.filter(team=self.active_team, season=season).select_related("member", "position").order_by("position__ordering", "member__last_name"))
            staff = list(StaffAssignment.objects.filter(team=self.active_team, season=season).select_related("member", "position").order_by("position__ordering", "member__last_name"))

        return super().get_context_data(roster=roster, staff=staff, **kwargs)


class CoachRosterMemberView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """Squad screen's per-player detail sheet: attendance stats for the
    season, tap-to-call buttons (the player's own phone/emergency phone, plus
    each guardian's if they're a child -- Member.guardians is only ever
    non-empty for one), and -- for whoever manages this team -- the same
    position/jersey/captaincy edit TeamMembershipForm exposes on desktop,
    plus a remove-from-roster action. Read-only (no edit form, no remove
    button) for staff without a management position, same hide-don't-disable
    rule as everywhere else in coach mode.
    """

    template_name = "mobile/coach/roster_member.html"
    screen_title = _("Player")
    active_tab = "coach_squad"

    def get_membership(self):
        if self.active_team is None:
            raise Http404
        return get_object_or_404(TeamMembership.objects.filter(team=self.active_team).select_related("member", "position"), pk=self.kwargs["membership_pk"])

    def build_form(self, membership, data=None):
        return CoachRosterEditForm(data, instance=membership, club=self.request.club, team=self.active_team, season=membership.season)

    def get_context_data(self, **kwargs):
        membership = self.get_membership()
        member = membership.member

        kwargs.setdefault("form", self.build_form(membership) if self.can_manage_active_team else None)
        return super().get_context_data(
            membership=membership,
            member=member,
            guardians=member.guardians,
            attendance_counts=member_attendance_counts(member, membership.season),
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        membership = self.get_membership()
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        form = self.build_form(membership, request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        form.save()
        notify(request, f"s|{_('Player updated')}|" + _("“%(member)s” has been updated.") % {"member": membership.member})
        return HttpResponseRedirect(reverse("mobile:coach_roster_member", kwargs={"membership_pk": membership.pk}))


class CoachRosterRemoveView(CoachScopeMixin, LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if self.active_team is None:
            raise Http404
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        membership = get_object_or_404(TeamMembership.objects.filter(team=self.active_team), pk=kwargs["membership_pk"])
        member = membership.member
        membership.delete()

        notify(request, f"w|{_('Player removed')}|" + _("“%(member)s” removed from the roster.") % {"member": member})
        return HttpResponseRedirect(reverse("mobile:coach_squad"))


class CoachScheduleView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """Bottom-tab "Schedule" -- every upcoming event for the active team, full
    stop (not Today's own "just the next session" scope). Each row jumps
    straight into the coach-relevant action -- Bench attendance for a
    practice, the Line-up for a game -- rather than mobile:event_detail (the
    Member-shell RSVP page a coach browsing their own team's schedule has no
    use for)."""

    template_name = "mobile/coach/schedule.html"
    screen_title = _("Schedule")
    active_tab = "coach_schedule"

    def get_context_data(self, **kwargs):
        events = []
        if self.active_team is not None:
            events = list(Event.objects.filter(teams=self.active_team, cancelled=False, start__gte=timezone.now()).order_by("start"))

        return super().get_context_data(events=events, **kwargs)
