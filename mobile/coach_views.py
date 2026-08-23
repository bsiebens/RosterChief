"""Coach-mode screens (C1-C6) -- design_handoff_rosterchief_platform/README.md's
"Coach mode (mobile, dark chrome)" section. Kept separate from mobile/views.py
(Member mode, M1-M7) since the two modes share almost no view logic beyond the
club/season plumbing already factored into club.services.access -- see
mobile/coach_mixins.py's CoachScopeMixin for the shared scaffolding.
"""

import datetime
import re

from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Case, F, Q, When
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.generic import TemplateView, View

from club.models import Season
from club.services.access import can_add_news, current_season
from controlpanel.messages import notify
from events.models import Attendance, Event, EventSeries, Lineup, LineupSelection, Location, Opponent
from events.services import generate_occurrences
from events.services.attendance import member_attendance_counts, player_attendance_rankings, record_check_in
from events.services.calendar import agenda_groups
from events.services.lineup import UNAVAILABLE_STATUSES, cancel_scheduled_publish, publish_lineup, schedule_lineup_publish, toggle_selection
from events.tasks import notify_new_event
from management.forms import EventForm, EventSeriesForm, LocationForm, NewsForm, NewsPhotoUploadForm, OpponentForm
from members.models import Member
from news.models import News, NewsPhoto
from news.services import notify_editors_of_pending_review
from notifications.services import notify_members
from teams.models import Position, StaffAssignment, Team, TeamMembership
from teams.services import eligible_roster_members

from .coach_mixins import CoachScopeMixin
from .forms import _INPUT_CLASSES, _TEXTAREA_CLASSES, CoachRosterEditForm

#: RSVP states that count as "in" for the stat tile -- present/selected are an
#: explicit yes, maybe is still a lean-in rather than silence.
IN_STATUSES = [Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED, Attendance.AttendanceStatus.MAYBE]

#: An explicit no -- declined or, for a published line-up, not selected.
#: Distinct from NO_RESPONSE ("silent"), which is a non-answer rather than a no.
OUT_STATUSES = [Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.EXCUSED, Attendance.AttendanceStatus.NOT_SELECTED]

#: The event kinds CoachCreateEventView's tile picker offers -- social/other
#: stay desktop-only (management.forms.EventForm keeps the full list), since
#: neither has a tile here.
COACH_EVENT_KINDS = [Event.EventKind.TRAINING, Event.EventKind.GAME, Event.EventKind.TOURNAMENT, Event.EventKind.MEETING]


class _LocationPickerForm(forms.Form):
    """Just the ``location`` picker, standalone from EventForm/EventSeriesForm
    -- CoachCreateEventView's own field (same name, so it POSTs into whichever
    of those two forms actually validates the request) and CoachLocationCreateView's
    "+ New location" popup both render this same one, so a freshly created
    Location can be handed back pre-selected without reconstructing the much
    bigger surrounding form just to redraw one <select>."""

    location = forms.ModelChoiceField(queryset=Location.objects.none(), required=False, label=_("Location"), widget=forms.Select(attrs={"class": _INPUT_CLASSES}))


class _OpponentPickerForm(forms.Form):
    """Same idea as _LocationPickerForm, for ``opponent``."""

    opponent = forms.ModelChoiceField(queryset=Opponent.objects.none(), required=False, label=_("Opponent"), widget=forms.Select(attrs={"class": _INPUT_CLASSES}))


def _location_picker(club, selected=None):
    picker = _LocationPickerForm(initial={"location": selected})
    picker.fields["location"].queryset = Location.objects.filter(club=club).order_by("name")
    return picker


def _opponent_picker(club, selected=None):
    picker = _OpponentPickerForm(initial={"opponent": selected})
    picker.fields["opponent"].queryset = Opponent.objects.filter(club=club).order_by("name")
    return picker


def _styled_location_form(data=None):
    form = LocationForm(data)
    for field in form.fields.values():
        field.widget.attrs["class"] = _INPUT_CLASSES
    return form


def _styled_opponent_form(data=None):
    form = OpponentForm(data)
    # A logo is a nice-to-have on the desktop Opponents page, not something worth a
    # file-upload control on a "we just need this to exist" quick-add popup -- add
    # one later from there if it matters.
    del form.fields["logo"]
    form.fields["name"].widget.attrs["class"] = _INPUT_CLASSES
    return form

#: How long an event stays "current" (CoachTodayView's session card, and the
#: missing-line-up nudge) past the moment it starts -- events.start__gte=now
#: alone would flip to the next session the instant this one begins, while
#: the coach is still mid-practice/mid-game. Past end (when set) plus a grace
#: window; past start plus an assumed length when it isn't (most training
#: events carry no end time -- see Event.end's own help text).
STILL_CURRENT_GRACE = datetime.timedelta(minutes=30)
STILL_CURRENT_ASSUMED_DURATION = datetime.timedelta(minutes=90)


def _still_current_events(team):
    """Events for ``team`` that haven't yet reached their "still current"
    cutoff -- see STILL_CURRENT_GRACE/STILL_CURRENT_ASSUMED_DURATION above."""
    cutoff = Case(
        When(end__isnull=False, then=F("end") + STILL_CURRENT_GRACE),
        default=F("start") + STILL_CURRENT_ASSUMED_DURATION,
    )
    return Event.objects.filter(teams=team, cancelled=False).annotate(still_current_cutoff=cutoff).filter(still_current_cutoff__gte=timezone.now()).order_by("start")


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

            upcoming = _still_current_events(team)
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
    active_tab = "coach_schedule"

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


class CoachAttendanceRemindSilentView(CoachScopeMixin, LoginRequiredMixin, View):
    """Attendance sheet's "Remind silent" button -- an on-demand version of
    events.tasks.send_deadline_reminders' own NO_RESPONSE nudge, for a coach
    who doesn't want to wait for that once-a-day sweep. Same title/body shape
    as that task, so a player who gets both isn't looking at two differently
    worded pushes for the same event."""

    def post(self, request, *args, **kwargs):
        if self.active_team is None:
            raise Http404
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = get_object_or_404(Event, pk=kwargs["event_id"], club=request.club, teams=self.active_team)
        member_ids = Attendance.objects.filter(event=event, status=Attendance.AttendanceStatus.NO_RESPONSE).values_list("member_id", flat=True)
        members = list(Member.objects.filter(id__in=member_ids))

        if members:
            when = timezone.localtime(event.start).strftime("%a %d %b, %H:%M")
            body = _("Reminder: %(kind)s on %(when)s still needs your answer.") % {"kind": event.get_kind_display(), "when": when}
            notify_members(members, club=event.club, title=event.title, body=body, source=event)
            notify(request, f"s|{_('Reminder sent')}|" + ngettext("%(count)d player nudged.", "%(count)d players nudged.", len(members)) % {"count": len(members)})
        else:
            notify(request, f"w|{_('Nobody to remind')}|{_('Everyone has already responded.')}")

        return HttpResponseRedirect(reverse("mobile:coach_attendance", kwargs={"event_id": event.pk}))


class CoachCreateEventView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C4 -- reuses management.forms.EventForm/EventSeriesForm as-is (their
    own __init__ already scopes fields to the requester via
    EventAudienceFormMixin), rather than a parallel hand-built form.

    Audience is simplified from the desktop version: ``teams`` is hard-locked
    to the active team (a hidden field, not a picker -- there's no "which
    team" question on a screen that's already scoped to one), and ``groups``/
    ``club_wide`` are dropped entirely, since both widen the audience past a
    single team the same way multi-team selection would. ``invited_members``/
    ``excluded_members`` stay, re-scoped to sensible pools (add someone not on
    the roster; exclude someone who is) rather than "every club member" --
    the template spells out that a genuinely multi-team event still needs the
    desktop. Location/opponent are rendered via the standalone
    _location_picker/_opponent_picker (see their own docstrings), each with a
    "+ New" popup (CoachLocationCreateView/CoachOpponentCreateView) for when
    the one needed doesn't exist yet.

    One screen creates either a single Event or a recurring EventSeries --
    ``is_recurring`` (a plain checkbox) picks which of the two forms below
    actually validates the request; the two share every audience/location/
    opponent field (identical names), so nothing needs duplicating in the
    template, just shown/hidden. Competition has no EventSeries equivalent
    (EventSeriesForm carries no such field), so it's one-off-only.

    After a successful single-event save: the same notify_new_event.delay(...)
    call management.views.EventCreateView.form_valid makes -- attendance sync
    is automatic via events/signals.py, only the notification dispatch needs
    replicating by hand for a view that isn't a CreateView. A new series
    mirrors management.views.EventSeriesCreateView instead: generate_occurrences
    materialises its occurrences immediately (each one syncing its own
    attendance the same way), but -- matching that same desktop behaviour,
    not a mobile-specific gap -- nothing pushes a "new event" notification per
    occurrence; a bulk-created series relies on send_deadline_reminders' own
    periodic sweep instead, exactly like a rolling-horizon extension does.
    """

    template_name = "mobile/coach/event_form.html"
    screen_title = _("New event")
    active_tab = "coach_today"

    def get(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseRedirect(reverse("mobile:coach_today"))
        return super().get(request, *args, **kwargs)

    def _member_pools(self):
        season = current_season(self.request.club)
        roster_member_ids = list(TeamMembership.objects.filter(team=self.active_team, season=season).values_list("member_id", flat=True)) if season and self.active_team else []
        excluded_pool = Member.objects.filter(pk__in=roster_member_ids).order_by("last_name", "first_name")
        invited_pool = eligible_roster_members(self.request.club).exclude(pk__in=roster_member_ids).order_by("last_name", "first_name")
        return invited_pool, excluded_pool

    def _scope_shared_fields(self, form):
        # Widget swapped in *before* .queryset is set on every ModelMultiple-
        # ChoiceField below -- ModelChoiceField.queryset's setter pushes
        # `self.widget.choices = self.choices` as a side effect at the moment
        # it's assigned, so setting the queryset first and swapping the
        # widget after just discards that push, leaving the new widget's own
        # .choices empty (a real, previously-undetected bug this surfaced --
        # see the regression tests, CoachCreateNewsView.build_form's own
        # near-identical fix for ``teams`` there, and build_series_form's own
        # comment on ``weekdays`` for the plain-ChoiceField variant of the
        # same trap).
        #
        # teams: hard-locked to the active team, not a picker -- see this
        # view's own docstring. A hidden MultipleHiddenInput renders its
        # `initial` on GET without any template code; the queryset
        # restriction means a tampered request can't set a *different* team,
        # and required=True (Event.teams itself is blank=True, so the form
        # field defaults to optional) means a tampered request can't submit
        # an empty selection either -- both would otherwise still produce a
        # real, saveable event, just not one scoped to a single team anymore.
        form.fields["teams"].widget = forms.MultipleHiddenInput()
        form.fields["teams"].queryset = Team.objects.filter(pk=self.active_team.pk) if self.active_team is not None else Team.objects.none()
        form.fields["teams"].required = True
        # form.initial (not just field.initial) -- ModelForm.__init__ already
        # populated form.initial["teams"] = [] from the new, unsaved Event/
        # EventSeries instance's own (necessarily empty) m2m, and
        # get_initial_for_field's dict.get(name, field.initial) only ever
        # falls back to field.initial when the key is *absent*, not when it's
        # merely empty -- so field.initial alone renders nothing here.
        form.initial["teams"] = [self.active_team.pk] if self.active_team is not None else []
        del form.fields["groups"]
        if "club_wide" in form.fields:
            del form.fields["club_wide"]
        invited_pool, excluded_pool = self._member_pools()
        form.fields["invited_members"].widget = forms.CheckboxSelectMultiple()
        form.fields["invited_members"].queryset = invited_pool
        form.fields["excluded_members"].widget = forms.CheckboxSelectMultiple()
        form.fields["excluded_members"].queryset = excluded_pool
        # location/opponent stay on the form (scope_audience_fields already
        # scoped both to this club) so a submitted value still validates and
        # saves correctly -- just never rendered here via {{ form.location }}/
        # {{ form.opponent }}. The template renders _location_picker.html/
        # _opponent_picker.html instead (same "location"/"opponent" POST
        # names, same club-scoped queryset, built standalone so a freshly
        # created Location/Opponent can be handed back pre-selected without
        # reconstructing this whole form -- see their own docstrings).
        if "title" in form.fields:
            form.fields["title"].widget.attrs["class"] = _INPUT_CLASSES

    def build_event_form(self, data=None):
        # kind=training, not Event.kind's own model default (OTHER) -- Practice
        # is the tile picker's first/most common option, and OTHER isn't even
        # one of the four tiles COACH_EVENT_KINDS offers below.
        instance = Event(club=self.request.club, created_by=self.me, kind=Event.EventKind.TRAINING)
        form = EventForm(data, club=self.request.club, user=self.request.user, editing=False, instance=instance)
        # max_referees has a model default (2) but no blank=True, so the field
        # is required despite it -- delete it rather than render a referee-
        # count control this screen has no use for; construct_instance skips
        # a deleted field entirely, leaving the instance's own default.
        del form.fields["max_referees"]
        # Narrowed to the four kinds the tile picker actually offers -- social/
        # other don't get their own tile, and this keeps a tampered request from
        # setting one anyway (the desktop form still offers the full list).
        form.fields["kind"].choices = [choice for choice in form.fields["kind"].choices if choice[0] in COACH_EVENT_KINDS]
        self._scope_shared_fields(form)
        for field_name in ("start", "gathering", "deadline", "competition", "external_game_id"):
            form.fields[field_name].widget.attrs["class"] = _INPUT_CLASSES
        return form

    def build_series_form(self, data=None):
        instance = EventSeries(club=self.request.club, kind=Event.EventKind.TRAINING)
        form = EventSeriesForm(data, club=self.request.club, user=self.request.user, instance=instance)
        # The raw-RRULE escape hatch is a desktop-only affordance -- the
        # friendly frequency/interval/weekdays fields below cover the common
        # weekly/monthly cases this screen is for.
        del form.fields["advanced_rrule"]
        form.fields["kind"].choices = [choice for choice in form.fields["kind"].choices if choice[0] in COACH_EVENT_KINDS]
        self._scope_shared_fields(form)
        # SelectMultiple relies on the desktop's searchable-select JS (not loaded
        # here) to be usable at all -- checkboxes work without it, and there are
        # only ever seven, so a tile-style has-checked toggle (see the template)
        # reads better than a cramped multi-select on a phone regardless.
        # choices passed straight to the widget's own constructor, not left to
        # ChoiceField.choices' assignment-time push -- the field's own choices
        # were already pushed onto the *original* SelectMultiple back when the
        # field itself was declared, and swapping the widget here discards
        # that (same trap _scope_shared_fields' own comment covers, just the
        # plain-ChoiceField shape of it).
        form.fields["weekdays"].widget = forms.CheckboxSelectMultiple(attrs={"class": "sr-only"}, choices=form.fields["weekdays"].choices)
        for field_name in ("dtstart", "until", "frequency", "interval", "duration_hours", "duration_minutes", "gathering_minutes_before", "deadline_minutes_before"):
            form.fields[field_name].widget.attrs["class"] = _INPUT_CLASSES
        return form

    def get_context_data(self, **kwargs):
        form = kwargs.setdefault("form", self.build_event_form())
        series_form = kwargs.setdefault("series_form", self.build_series_form())
        # Whichever of the two actually carries the failed submission's data
        # (a validation failure always rebuilds the *other* one fresh/unbound,
        # so at most one of these is ever bound) -- GET has neither bound.
        bound = form if form.is_bound else series_form if series_form.is_bound else None
        # title/teams/invited_members/excluded_members are identical fields on
        # both forms (same name, same queryset) -- rendered once, from
        # whichever form is actually bound, so a validation failure on the
        # *series* form doesn't redisplay those as blank just because `form`
        # itself is a fresh, never-submitted EventForm in that response.
        kwargs.setdefault("shared_form", bound or form)
        kwargs.setdefault("is_recurring", series_form.is_bound)
        kwargs.setdefault("location_picker", _location_picker(self.request.club, selected=bound.data.get("location") if bound else None))
        kwargs.setdefault("opponent_picker", _opponent_picker(self.request.club, selected=bound.data.get("opponent") if bound else None))
        kwargs.setdefault("location_form", _styled_location_form())
        kwargs.setdefault("opponent_form", _styled_opponent_form())
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        if request.POST.get("is_recurring") == "on":
            series_form = self.build_series_form(request.POST)
            if not series_form.is_valid():
                return self.render_to_response(self.get_context_data(form=self.build_event_form(), series_form=series_form))

            series = series_form.save()
            # Not automatic on save -- without this the series would exist with
            # zero occurrences until the extend_event_series cron next runs.
            created = generate_occurrences(series)
            body = ngettext("“%(series)s” created, with %(count)d occurrence scheduled.", "“%(series)s” created, with %(count)d occurrences scheduled.", len(created)) % {"series": series, "count": len(created)}
            notify(request, f"s|{_('Series created')}|{body}")
            return HttpResponseRedirect(reverse("mobile:coach_today"))

        form = self.build_event_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, series_form=self.build_series_form()))

        event = form.save()
        body = _("“%(event)s” created.") % {"event": event}
        notify(request, f"s|{_('Event created')}|{body}")
        # A deliberately-planned single event, same as the desktop create
        # flow -- see notify_new_event's own docstring for why a recurring
        # series' occurrences aren't wired to this.
        notify_new_event.delay(str(event.pk))
        return HttpResponseRedirect(reverse("mobile:coach_today"))


class CoachLocationCreateView(CoachScopeMixin, LoginRequiredMixin, View):
    """New event's "+ New location" popup. The modal's own <form> targets
    just #location-modal-body (hx-swap="innerHTML") -- on a validation
    failure that's the whole response, re-showing the fields with errors. On
    success the response also carries an out-of-band #location-picker swap
    (the standard htmx way to update a second area from one request) so the
    freshly created Location shows up pre-selected on the field the modal was
    opened from, plus an HX-Trigger the modal listens for to close itself --
    all without touching (or losing progress in) the rest of the in-progress
    event/series form the modal is sitting on top of.
    """

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        form = _styled_location_form(request.POST)
        if not form.is_valid():
            return render(request, "mobile/coach/_location_modal_fields.html", {"location_form": form})

        location = form.save(commit=False)
        location.club = request.club
        location.save()
        response = render(
            request,
            "mobile/coach/_location_created_response.html",
            {"location_form": _styled_location_form(), "location_picker": _location_picker(request.club, selected=location.pk)},
        )
        response["HX-Trigger"] = "location-created"
        return response


class CoachOpponentCreateView(CoachScopeMixin, LoginRequiredMixin, View):
    """New event's "+ New opponent" popup -- same shape as
    CoachLocationCreateView, for Opponent instead."""

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        form = _styled_opponent_form(request.POST)
        if not form.is_valid():
            return render(request, "mobile/coach/_opponent_modal_fields.html", {"opponent_form": form})

        opponent = form.save(commit=False)
        opponent.club = request.club
        opponent.save()
        response = render(
            request,
            "mobile/coach/_opponent_created_response.html",
            {"opponent_form": _styled_opponent_form(), "opponent_picker": _opponent_picker(request.club, selected=opponent.pk)},
        )
        response["HX-Trigger"] = "opponent-created"
        return response


class CoachCreateNewsView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C5 -- reuses management.forms.NewsForm, re-scoped to the coach's own
    active team. ``teams`` isn't a picker at all here (same reasoning as
    CoachCreateEventView's own ``teams`` -- there's no "which team" question
    on a screen already scoped to one): hard-locked to the active team, a
    hidden field an editor can still widen from the desktop when reviewing
    the submission, which is the honest place for that judgment call to live
    -- not a checkbox list a coach has to get right on a phone.

    Gated with club.services.access.can_add_news, which already includes
    is_coach_manager -- no new authorization logic needed. On submit, the
    post is handed straight to News.submit_for_review() (plus the same
    notify_editors_of_pending_review call the desktop's own
    NewsSubmitForReviewView makes) rather than left a silent draft, so it
    actually reaches an editor's queue -- can_publish_news stays editor/
    admin-only, so "Send for review" is the honest label here, not "Publish"
    the way the design mock has it. Any uploaded photos are attached via
    management.forms.NewsPhotoUploadForm's own "images" field and
    NewsPhotoUploadView's own first-upload-becomes-main logic, reused
    directly rather than a second copy of either.

    title_en/body_en (the optional English fallback) aren't part of this
    screen -- both are blank=True on the model (a coach posting from their
    phone isn't expected to also draft an English translation).
    ``visibility`` is left at the model's own default (INTERNAL -- team
    families, in-app only) rather than building the mock's "also on club
    website" toggle: a coach's post always lands as PENDING_REVIEW first,
    and an editor reviewing it can widen visibility before publishing if a
    public-site placement is actually warranted -- that's a real gate, not a
    decorative row, so it isn't reproduced here as one.
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
        # teams: hard-locked to the active team, not a picker -- see this
        # view's own docstring. Widget swapped in *before* .queryset is set --
        # ModelChoiceField.queryset's setter pushes `self.widget.choices =
        # self.choices` as a side effect at the moment it's assigned, and
        # setting the queryset first (then swapping the widget after) would
        # discard that push -- harmless for MultipleHiddenInput specifically
        # (it renders from `value`/`initial`, not `choices`), but kept in
        # this order anyway for consistency with the ModelMultipleChoiceField
        # fields that *do* need it (CoachCreateEventView._scope_shared_fields'
        # own comment has the full mechanism).
        form.fields["teams"].widget = forms.MultipleHiddenInput()
        form.fields["teams"].queryset = Team.objects.filter(pk=self.active_team.pk) if self.active_team is not None else Team.objects.none()
        # News.teams is blank=True, so the auto-generated field defaults to
        # required=False -- without this, a tampered empty submission would
        # still save, silently becoming a club-wide post (see the analogous
        # fix and full comment on CoachCreateEventView._scope_shared_fields'
        # own "teams" field).
        form.fields["teams"].required = True
        # form.initial, not field.initial -- ModelForm.__init__ already set
        # form.initial["teams"] = [] from the new, unsaved News instance's
        # own (necessarily empty) m2m, and dict.get(name, field.initial)
        # only falls back to field.initial when the key is *absent*, not
        # merely empty, so field.initial alone silently renders unchecked.
        form.initial["teams"] = [self.active_team.pk] if self.active_team is not None else []
        form.fields["title"].widget.attrs["class"] = _INPUT_CLASSES
        form.fields["body"].widget.attrs["class"] = _TEXTAREA_CLASSES
        return form

    def build_photo_form(self, data=None, files=None):
        photo_form = NewsPhotoUploadForm(data, files)
        photo_form.fields["images"].required = False
        photo_form.fields["images"].widget.attrs["class"] = "m-file-input"
        return photo_form

    def get_context_data(self, **kwargs):
        kwargs.setdefault("form", self.build_form())
        kwargs.setdefault("photo_form", self.build_photo_form())
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if not can_add_news(request.user, request.club):
            return HttpResponseForbidden()

        form = self.build_form(request.POST)
        photo_form = self.build_photo_form(request.POST, request.FILES)
        if not form.is_valid() or not photo_form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, photo_form=photo_form))

        news_item = form.save()
        for index, image in enumerate(photo_form.cleaned_data["images"]):
            NewsPhoto.objects.create(news_item=news_item, image=image, is_main=index == 0)
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
    picker). A player added here starts with no jersey number/position set --
    the coach picks those afterward from the player's own row on the Squad
    screen (CoachRosterMemberView), same "blank is a normal, expected state"
    TeamMembership.position's own help_text already documents.

    The pool is teams.services.eligible_roster_members(club) minus whoever's
    already on this team+season -- the same two rules TeamMembershipForm
    applies internally, just reused directly rather than through the form.
    "Suggested" is two real, computed sources unioned together: whoever was
    on this team last season, plus whoever's on the closest younger team
    *this* season (see _feeder_team -- a guess from team naming, since
    neither Club nor Team carries a real age-group field to link them
    properly). "Age eligible" from the mock still isn't built -- there's no
    birth-date cutoff to compare against, and faking that filter would just
    mean it silently matched nothing. A plain first/last-name search (?q=)
    narrows the pool further, ANDed with whichever filter chip is active --
    useful once a club's eligible-member pool outgrows a single screenful.
    """

    template_name = "mobile/coach/add_player.html"
    screen_title = _("Add players")
    active_tab = "coach_squad"

    #: ?filter= values this screen understands -- anything else (including no
    #: param at all) means "All".
    FILTERS = {"suggested", "no_team"}

    #: Matches a "U<number>" youth age-group marker in a team's name/short
    #: name (e.g. "U14", "u16 boys") -- the only age-group signal available
    #: anywhere on Team today.
    AGE_GROUP_RE = re.compile(r"u(\d+)", re.IGNORECASE)

    def get(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseRedirect(reverse("mobile:coach_today"))
        return super().get(request, *args, **kwargs)

    def _age_group(self, team):
        match = self.AGE_GROUP_RE.search(team.name) or self.AGE_GROUP_RE.search(team.short_name)
        return int(match.group(1)) if match else None

    def _feeder_team(self):
        """The club's own team with the closest smaller age-group number
        than the active team's, if either carries one -- a guess (see this
        view's own docstring), so it silently returns None for a club that
        doesn't name teams "U<N>"."""
        active_age = self._age_group(self.active_team)
        if active_age is None:
            return None

        feeder, feeder_age = None, None
        for team in Team.objects.filter(club=self.request.club).exclude(pk=self.active_team.pk):
            age = self._age_group(team)
            if age is not None and age < active_age and (feeder_age is None or age > feeder_age):
                feeder, feeder_age = team, age
        return feeder

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
        search_query = self.request.GET.get("q", "").strip()

        if self.active_team is not None and season is not None:
            squad_count = TeamMembership.objects.filter(team=self.active_team, season=season).count()
            pool = self._candidate_pool(season)

            if filter_param == "no_team":
                pool = pool.exclude(team_memberships__season=season)
            elif filter_param == "suggested":
                suggested_ids = set()
                previous_season = Season.before(self.request.club, season)
                if previous_season is not None:
                    suggested_ids.update(pool.filter(team_memberships__team=self.active_team, team_memberships__season=previous_season).values_list("pk", flat=True))
                feeder_team = self._feeder_team()
                if feeder_team is not None:
                    suggested_ids.update(pool.filter(team_memberships__team=feeder_team, team_memberships__season=season).values_list("pk", flat=True))
                pool = pool.filter(pk__in=suggested_ids)

            if search_query:
                pool = pool.filter(Q(first_name__icontains=search_query) | Q(last_name__icontains=search_query))

            candidates = list(pool.distinct().order_by("last_name", "first_name"))

        return super().get_context_data(
            candidates=candidates,
            squad_count=squad_count,
            filter_param=filter_param,
            search_query=search_query,
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
    active_tab = "coach_squad"

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

    Saving here only ever writes LineupSelection -- it never touches
    Attendance.status or sends a notification, before or after the first
    publish. A coach can keep editing a published lineup freely without
    pinging anyone; CoachLineupPublishView's "Publish"/"Publish changes"
    button (see the template) is the only thing that syncs Attendance and
    notifies -- and events.services.lineup.publish_lineup only notifies
    whoever's status actually changed since the last publish, not everyone
    currently selected.
    """

    template_name = "mobile/coach/lineup.html"
    screen_title = _("Line-up")
    active_tab = "coach_schedule"

    def get_event(self):
        if self.active_team is None:
            raise Http404
        return get_object_or_404(Event, pk=self.kwargs["event_id"], club=self.request.club, teams=self.active_team, kind=Event.EventKind.GAME)

    def _categories(self, event, lineup):
        """Available roster players, grouped by position -- same "category"
        the member-side published view groups by (mobile/views.py's
        EventDetailView). A player with no TeamMembership for this team/
        season (a guest call-up) lands in a catch-all "No position set"
        bucket rather than being dropped.

        Each row also carries this season's turnout rate (``events.services.
        attendance.player_attendance_rankings``, one query for the whole
        team rather than one per row) -- a player saying "yes" to this game
        doesn't tell a coach how reliably they actually show up, and that's
        exactly the judgment call a line-up screen exists for. Riders with
        too little history (rankings' own ``minimum_responses`` floor) get
        no rate rather than a misleading 0%/100% from one data point."""
        season = current_season(self.request.club)
        memberships_by_member = {}
        rates_by_member = {}
        if season is not None:
            memberships_by_member = {tm.member_id: tm for tm in TeamMembership.objects.filter(team=self.active_team, season=season).select_related("position")}
            rates_by_member = {row["member"].pk: row["rate"] for row in player_attendance_rankings(self.active_team, season)}

        selected_ids = set(LineupSelection.objects.filter(lineup=lineup).values_list("member_id", flat=True))
        available = Attendance.objects.filter(event=event).exclude(status__in=UNAVAILABLE_STATUSES).select_related("member").order_by("member__last_name", "member__first_name")

        buckets = {}
        for attendance in available:
            membership = memberships_by_member.get(attendance.member_id)
            position = membership.position if membership else None
            key = position.pk if position else None
            bucket = buckets.setdefault(key, {"label": position.name if position else _("No position set"), "ordering": position.ordering if position else 9999, "rows": []})
            bucket["rows"].append({"member": attendance.member, "membership": membership, "selected": attendance.member_id in selected_ids, "attendance_rate": rates_by_member.get(attendance.member_id)})

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
    so the plain "Publish"/"Publish changes" button posts with no extra
    fields). Reachable, and does the right thing, whether this is the first
    publish or a republish of an already-published lineup a coach kept
    editing -- publish_lineup itself is what limits the notification to
    whoever's status actually changed.
    """

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
            was_already_published = lineup.published_at is not None
            publish_lineup(lineup)
            if was_already_published:
                notify(request, f"s|{_('Line-up updated')}|{_('Anyone whose status changed has been notified.')}")
            else:
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


class CoachStaffRemoveView(CoachScopeMixin, LoginRequiredMixin, View):
    """Squad screen's per-staff-row remove action. A manager can't remove
    their own StaffAssignment this way -- self-removal would strand them off
    a team they're actively viewing, with no one obviously left to undo it;
    that stays a desktop-only action (management.views.TeamStaffRemoveView),
    which any *other* admin/manager can still reach."""

    def post(self, request, *args, **kwargs):
        if self.active_team is None:
            raise Http404
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        assignment = get_object_or_404(StaffAssignment.objects.filter(team=self.active_team), pk=kwargs["assignment_pk"])
        if self.me is not None and assignment.member_id == self.me.pk:
            return HttpResponseForbidden()

        member = assignment.member
        assignment.delete()

        notify(request, f"w|{_('Staff removed')}|" + _("“%(member)s” removed from staff.") % {"member": member})
        return HttpResponseRedirect(reverse("mobile:coach_squad"))


class CoachScheduleView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """Bottom-tab "Schedule" -- every upcoming event for the active team, full
    stop (not Today's own "just the next session" scope). Each row jumps
    straight into the coach-relevant action -- Bench attendance for a
    practice, the Line-up for a game -- rather than mobile:event_detail (the
    Member-shell RSVP page a coach browsing their own team's schedule has no
    use for). Grouped into This week/Next week/by-month dividers via events.
    services.calendar.agenda_groups -- the same shared grouping mobile.views.
    CalendarView (the member app's own Calendar) and management.views.
    EventListView's "List" mode use, so all three read the same way."""

    template_name = "mobile/coach/schedule.html"
    screen_title = _("Schedule")
    active_tab = "coach_schedule"

    def get_context_data(self, **kwargs):
        this_week, next_week, later_months = [], [], []
        if self.active_team is not None:
            events = list(Event.objects.filter(teams=self.active_team, cancelled=False, start__gte=timezone.now()).order_by("start"))
            this_week, next_week, later_months = agenda_groups(events)

        return super().get_context_data(this_week=this_week, next_week=next_week, later_months=later_months, **kwargs)
