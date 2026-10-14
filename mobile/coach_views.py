"""Coach-mode screens (C1-C6) -- the design handoff's
"Coach mode (mobile, dark chrome)" section. Kept separate from mobile/views.py
(Member mode, M1-M7) since the two modes share almost no view logic beyond the
club/season plumbing already factored into club.services.access -- see
mobile/coach_mixins.py's CoachScopeMixin for the shared scaffolding.
"""

from urllib.parse import urlsplit

from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.generic import TemplateView, View
from waffle import flag_is_active

from appapi import coach_events as api_coach_events
from appapi.errors import ApiError
from appapi.idempotency import idempotent
from club.models import ClubMembership, Season
from club.services.access import can_add_news, can_manage_evaluations, current_season, get_current_season
from controlpanel.messages import notify
from events.models import Attendance, Event, EventSeries, EventTask, Lineup, LineupSelection, Location, Opponent
from events.services import detach_occurrence, generate_occurrences, propagate_series
from events.services.attendance import COACH_NOTE_MAX, CoachAnswerError, member_attendance_counts, player_attendance_rankings, record_check_in, set_answer_as_coach
from events.services.calendar import agenda_groups
from events.services.coach import COACH_EVENT_KINDS, IN_STATUSES, LINEUP_EVENT_KINDS, OUT_STATUSES, notify_event_cancelled, notify_event_changed, remind_silent, still_current_events
from events.services.lineup import UNAVAILABLE_STATUSES, cancel_scheduled_publish, changes_on_publish, publish_lineup, schedule_lineup_publish, toggle_selection
from events.services.notifications import dispatch_notify_new_event
from events.services.officials import needs_official_management, officials_enabled_for
from events.services.recurrence import cancel_occurrence
from events.services.referees import needs_referee_management
from management.forms import EventForm, EventSeriesForm, EventTaskForm, LocationForm, NewsForm, NewsPhotoUploadForm, OpponentForm, _location_label
from management.recurrence_ui import describe_rrule, parse_rrule
from members.models import Member
from members.services.family import claim_label_for
from news.models import News, NewsPhoto
from news.services import dispatch_notify_editors_of_pending_review
from teams.models import Position, StaffAssignment, Team, TeamMembership
from teams.services import eligible_roster_members

from .coach_mixins import CoachScopeMixin
from .forms import _INPUT_CLASSES, _TEXTAREA_CLASSES, CoachRosterEditForm


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
    picker.fields["location"].label_from_instance = _location_label
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


def _styled_task_form(data=None, instance=None):
    form = EventTaskForm(data, instance=instance)
    form.fields["title"].widget.attrs["class"] = _INPUT_CLASSES
    form.fields["description"].widget.attrs["class"] = _TEXTAREA_CLASSES
    form.fields["needed_quantity"].widget.attrs["class"] = _INPUT_CLASSES
    return form


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

            upcoming = still_current_events(team)
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

            upcoming_games = list(upcoming.filter(kind__in=LINEUP_EVENT_KINDS)[: self.UPCOMING_GAMES_CHECKED])
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
            my_upcoming = (
                Attendance.objects.filter(
                    member=self.me,
                    event__club=self.request.club,
                    event__cancelled=False,
                    event__start__gte=now,
                )
                .select_related("event", "event__location")
                .order_by("event__start")
            )
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
            replies_open=session_event is not None and session_event.deadline is not None and session_event.deadline > now,
            hero_attendance=hero_attendance,
            rsvp_closed=rsvp_closed,
            **kwargs,
        )


def is_inline_request(request):
    """True for an htmx request the page itself issued (hx-post/hx-get on an
    element), false for a boosted navigation (HX-Boosted) or a plain browser
    request -- inline requests get a partial back, everything else the
    classic redirect/full page, so the screens keep working without JS."""
    return request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"


def run_once(request, work):
    """Run ``work()`` -> ``(status, payload)`` at most once per ``Idempotency-Key``
    header (appapi.idempotency, the same store the native apps use). The PWA's
    offline queue (static/js/mobile-offline-queue.js) generates a key per action and
    keeps it across retries, so a replay after a lost response returns the stored
    payload instead of applying (or notifying) twice.

    ``payload`` is a small JSON-able dict (toast, status_text, ...) -- never the
    rendered HTML and never the private coach note; the caller re-renders the
    *current* state from it. Non-2xx statuses release the key (retry allowed).
    Returns ``(status, payload)``; ``payload`` is None for a key-level problem
    (409 still in progress / 422 key reused), which the caller answers bare."""
    try:
        return idempotent(request, work)
    except ApiError as error:
        return error.status, None


def tag_result(response, status):
    """Mark an inline response whose action was refused (validation etc.), so the offline queue
    can tell "saved" from "answered with an error toast" -- both are HTTP 200 for htmx to swap."""
    if status >= 300:
        response["X-Offline-Result"] = "error"
    return response


def _answer_for_status(status):
    """The three-way answer (+ reset) a stored Attendance.status reads as to a
    coach: selected counts as In, not-selected/excused as Out."""
    Status = Attendance.AttendanceStatus
    if status in (Status.PRESENT, Status.SELECTED):
        return "present"
    if status in (Status.ABSENT, Status.EXCUSED, Status.NOT_SELECTED):
        return "absent"
    if status == Status.MAYBE:
        return "maybe"
    return "no_response"


_PILL_FOR_ANSWER = {
    "present": (_("In"), "pill-ok"),
    "maybe": (_("Maybe"), "pill-warn"),
    "absent": (_("Out"), "pill-danger"),
    "no_response": (_("No reply"), "pill-neutral"),
}


class CoachAttendanceView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """C2 -- bench attendance: check off who actually showed up to one event,
    separate from their RSVP. Writes through
    events.services.attendance.record_check_in, built for exactly this and
    previously uncalled anywhere in the codebase.

    Check-ins, the per-player answer sheet and "Remind" all save inline: each
    posts to its own view below (subclasses of this one, so they share the
    event lookup and context) and gets the ``_attendance_body.html`` partial
    back to swap in, plus an out-of-band status line and toast. The plain
    form ``post`` (a batch of showed_up_<pk> fields, redirect back) stays for
    clients without JS.

    Read-only for staff without a management position on the active team --
    can_manage_active_team hides the check-in/edit controls in the template
    (hide, don't disable), and every write 403s regardless, since a hidden
    control is still a client-side fact, not a real permission check.

    Responses are never cacheable: they can carry the managers-only private
    coach note.
    """

    template_name = "mobile/coach/attendance.html"
    body_template_name = "mobile/coach/_attendance_body.html"
    screen_title = _("Attendance")
    active_tab = "coach_schedule"

    #: ?filter= values this screen understands -- anything else (including no
    #: param) means "Responded" (IN_STATUSES: present/selected/maybe), the
    #: default view. A coach doesn't need to check in someone silent or
    #: declined -- neither is expected to show up -- so those are left out of
    #: the default rather than needing to be filtered away each time; each
    #: still gets its own chip to review who's in either bucket, plus "All".
    FILTERS = {"silent", "declined", "all"}

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        patch_cache_control(response, no_store=True, private=True)
        if request.method == "GET" and response.status_code == 200 and not is_inline_request(request) and self.page_is_free_of_private_notes(kwargs.get("event_id")):
            # Opt-in for the service worker's read-only offline copy (mobile/templates/mobile/sw.js):
            # only a page that carries no private coach note may be stored on the device.
            response["X-Offline-Cache"] = "1"
        return response

    def page_is_free_of_private_notes(self, event_id):
        if event_id is None or self.active_team is None:
            return False
        return not Attendance.objects.filter(event_id=event_id, event__club=self.request.club).exclude(Q(coach_note__isnull=True) | Q(coach_note="")).exists()

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
            attendance.answer = _answer_for_status(attendance.status)
            attendance.pill_label, attendance.pill_class = _PILL_FOR_ANSWER[attendance.answer]
            attendance.in_published_lineup = attendance.status == Attendance.AttendanceStatus.SELECTED

        filter_param = self.request.GET.get("filter")
        if filter_param not in self.FILTERS:
            filter_param = ""
        if filter_param == "silent":
            rows = [row for row in attendances if row.is_silent]
        elif filter_param == "declined":
            rows = [row for row in attendances if row.status in OUT_STATUSES]
        elif filter_param == "all":
            rows = attendances
        else:
            rows = [row for row in attendances if row.status in IN_STATUSES]

        responded_count = sum(1 for row in attendances if row.status in IN_STATUSES)
        return super().get_context_data(
            event=event,
            rows=rows,
            total_count=len(attendances),
            responded_count=responded_count,
            silent_count=sum(1 for row in attendances if row.is_silent),
            declined_count=sum(1 for row in attendances if row.status in OUT_STATUSES),
            checked_in_count=sum(1 for row in attendances if row.showed_up is not None),
            filter_param=filter_param,
            # Answers can be corrected by a manager on any event that isn't cancelled
            # -- the reply deadline and the start time don't matter (see
            # events.services.attendance.set_answer_as_coach).
            can_edit_answers=self.can_manage_active_team and not event.cancelled,
            coach_note_max=COACH_NOTE_MAX,
            **kwargs,
        )

    def render_body(self, *, toast=None, status_text="", needs_lineup_swap=False, status=200):
        """The swap-in partial plus the out-of-band status line/toast."""
        context = self.get_context_data(toast=toast, status_text=status_text, needs_lineup_swap=needs_lineup_swap, inline_response=True)
        response = render(self.request, self.body_template_name, context, status=status)
        patch_cache_control(response, no_store=True, private=True)
        return response

    def finish(self, request, *, toast=None, status_text="", needs_lineup_swap=False):
        """Inline requests get the partial; everything else the classic redirect."""
        if is_inline_request(request):
            return self.render_body(toast=toast, status_text=status_text, needs_lineup_swap=needs_lineup_swap)
        if toast:
            level = {"ok": "s", "warn": "w", "error": "e"}[toast["kind"]]
            notify(request, f"{level}|{toast['title']}|{toast['text']}")
        return HttpResponseRedirect(reverse("mobile:coach_attendance", kwargs={"event_id": self.kwargs["event_id"]}))

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
        return HttpResponseRedirect(reverse("mobile:coach_attendance", kwargs={"event_id": event.pk}))


class CoachAttendanceCheckInView(CoachAttendanceView):
    """One tap on a row's showed-up / didn't-show-up button. Posting the value
    a row already has clears it back to "not recorded" (same as the iOS app),
    so each button is a real toggle (aria-pressed)."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        event = self.get_event()
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        wanted = request.POST.get("showed_up")
        if wanted not in ("true", "false"):
            return HttpResponseBadRequest()
        wanted_value = wanted == "true"

        def work():
            with transaction.atomic():
                attendance = get_object_or_404(Attendance.objects.select_for_update(), pk=kwargs["attendance_id"], event=event)
                record_check_in(attendance, showed_up=None if attendance.showed_up == wanted_value else wanted_value)
            return 200, {"status_text": str(_("Saved"))}

        # Idempotency-Key replays must not flip the toggle back (the value above is a toggle when it matches).
        status, payload = run_once(request, work)
        if payload is None:
            return HttpResponse(status=status)
        return self.finish(request, status_text=payload["status_text"])


class CoachAttendanceEditView(CoachAttendanceView):
    """The per-player edit sheet: a manager sets a player's answer on their
    behalf (or resets it), writes the private coach note, and chooses whether
    to tell the player. All rules live in events.services.attendance.
    set_answer_as_coach, shared with the app API.

    The coach note is deliberately never put in a toast, a message, a log line
    or a cacheable response -- it's compared and stored, nothing more.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        event = self.get_event()
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        status = request.POST.get("status", "")
        raw_note = request.POST.get("coach_note")
        notify_player = request.POST.get("notify") == "on"

        def work():
            if raw_note is not None and len(raw_note.strip()) > COACH_NOTE_MAX:
                return 422, {"error": str(_("The note is too long (500 characters at most)."))}
            try:
                with transaction.atomic():
                    attendance = get_object_or_404(Attendance.objects.select_for_update().select_related("event", "member"), pk=kwargs["attendance_id"], event=event)
                    was_in_lineup = attendance.status == Attendance.AttendanceStatus.SELECTED
                    note = None
                    if raw_note is not None and raw_note.strip() != (attendance.coach_note or "").strip():
                        note = raw_note.strip()
                    set_answer_as_coach(attendance, status, note, coach=self.me, notify=notify_player)
            except CoachAnswerError as error:
                return 422, {"error": str(error.message)}
            # The private coach note is deliberately never part of this payload (it is stored in the idempotency table).
            return 200, {
                "toast": {"kind": "ok", "title": str(_("Answer saved")), "text": str(_("“%(member)s” updated.") % {"member": attendance.member})},
                "status_text": str(_("Saved")),
                "needs_lineup_swap": was_in_lineup and status == "absent",
            }

        result, payload = run_once(request, work)
        if payload is None:
            return HttpResponse(status=result)
        if result >= 300:
            return tag_result(self._error(payload["error"]), result)
        return self.finish(request, toast=payload["toast"], status_text=payload["status_text"], needs_lineup_swap=payload["needs_lineup_swap"])

    def _error(self, message):
        toast = {"kind": "error", "title": _("Couldn't save"), "text": message}
        if is_inline_request(self.request):
            return self.render_body(toast=toast, status_text=_("Not saved"))
        notify(self.request, f"e|{toast['title']}|{message}")
        return HttpResponseRedirect(reverse("mobile:coach_attendance", kwargs={"event_id": self.kwargs["event_id"]}))


class CoachAttendanceRemindSilentView(CoachAttendanceView):
    """Attendance sheet's "Remind N players" button -- an on-demand version of
    events.tasks.send_deadline_reminders' own NO_RESPONSE nudge, for a coach
    who doesn't want to wait for that once-a-day sweep. Same title/body shape
    as that task, so a player who gets both isn't looking at two differently
    worded pushes for the same event."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        event = self.get_event()
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        count = remind_silent(event)

        if count:
            toast = {"kind": "ok", "title": _("Reminder sent"), "text": ngettext("%(count)d player nudged.", "%(count)d players nudged.", count) % {"count": count}}
        else:
            toast = {"kind": "warn", "title": _("Nobody to remind"), "text": _("Everyone has already responded.")}
        return self.finish(request, toast=toast)


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

    After a successful single-event save: the same dispatch_notify_new_event(...)
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

    #: False here, True on CoachEditEventView -- passed straight through to
    #: EventForm(editing=...), which is what actually adds score_for/
    #: score_against/is_live to the form (management/forms.py) when relevant.
    editing = False

    def get(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseRedirect(reverse("mobile:coach_today"))
        return super().get(request, *args, **kwargs)

    def get_event_instance(self):
        # kind=training, not Event.kind's own model default (OTHER) -- Practice
        # is the tile picker's first/most common option, and OTHER isn't even
        # one of the four tiles COACH_EVENT_KINDS offers below.
        return Event(club=self.request.club, created_by=self.me, kind=Event.EventKind.TRAINING)

    def get_series_instance(self):
        return EventSeries(club=self.request.club, kind=Event.EventKind.TRAINING)

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
        # Same data-searchable typeahead widget as the desktop EventForm
        # (management.forms._AUDIENCE_WIDGETS) -- a checkbox per eligible
        # club member doesn't scale, and searchable-select.js is already
        # loaded on mobile (coach/base.html) and already styled by
        # assets/mobile.css.
        form.fields["invited_members"].widget = forms.SelectMultiple(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")})
        form.fields["invited_members"].queryset = invited_pool
        form.fields["excluded_members"].widget = forms.SelectMultiple(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")})
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
        form = EventForm(data, club=self.request.club, user=self.request.user, editing=self.editing, instance=self.get_event_instance())
        # Narrowed to the four kinds the tile picker actually offers -- social/
        # other don't get their own tile, and this keeps a tampered request from
        # setting one anyway (the desktop form still offers the full list).
        form.fields["kind"].choices = [choice for choice in form.fields["kind"].choices if choice[0] in COACH_EVENT_KINDS]
        self._scope_shared_fields(form)
        if "end" in form.fields:
            # No end-time input on this screen: disabled keeps the stored value instead of an absent POST value clearing it.
            form.fields["end"].disabled = True
        for field_name in ("start", "gathering", "deadline", "competition", "external_game_id", "max_referees"):
            form.fields[field_name].widget.attrs["class"] = _INPUT_CLASSES
        form.fields["is_friendly"].widget.attrs["class"] = "h-5 w-5 shrink-0 accent-ink"
        # max_officials, unlike the fields above, isn't always on the form at
        # all -- EventForm.__init__ deletes it entirely when the "officials"
        # flag isn't on for this club, same as the desktop form.
        if "max_officials" in form.fields:
            form.fields["max_officials"].widget.attrs["class"] = _INPUT_CLASSES
        # score_for/score_against/is_live only exist when editing=True (a
        # game/tournament that's only just being scheduled has nothing to
        # record yet -- same reasoning EventForm.__init__ itself documents).
        if "score_for" in form.fields:
            form.fields["score_for"].widget.attrs["class"] = _INPUT_CLASSES
            form.fields["score_against"].widget.attrs["class"] = _INPUT_CLASSES
            form.fields["is_live"].widget.attrs["class"] = "h-5 w-5 shrink-0 accent-ink"
        return form

    def build_series_form(self, data=None):
        form = EventSeriesForm(data, club=self.request.club, user=self.request.user, instance=self.get_series_instance())
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
        kwargs.setdefault("return_url", _return_url(self.request))
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
        # The "Tell the players" checkbox (iOS parity). A form that never rendered it (notify_present
        # missing) keeps the old always-notify behaviour.
        if request.POST.get("notify") == "on" or "notify_present" not in request.POST:
            dispatch_notify_new_event(str(event.pk))
        return HttpResponseRedirect(reverse("mobile:coach_today"))


class CoachEditEventView(CoachCreateEventView):
    """C4 continued (issue #18) -- edit a single Event's own fields. Reuses
    every bit of CoachCreateEventView's form-building machinery (kind tile
    choices, teams/audience scoping, field styling) via get_event_instance()/
    editing=True, which is what makes EventForm add score_for/score_against/
    is_live (management/forms.py), same as management.views.EventUpdateView.

    Never touches EventSeries -- series_form only gets built here because
    mobile/coach/event_form.html unconditionally references it (the "This
    repeats" toggle is hidden via lock_recurring, see the template), not
    because this screen can switch a single event into a series or back.
    Editing a recurring series' own pattern is CoachEditEventSeriesView,
    reached from the event detail hub instead.
    """

    screen_title = _("Edit event")
    editing = True

    def get_event_instance(self):
        # Cached: build_event_form() and get_context_data() each call this
        # once per request -- without caching that's two identical fetches.
        if not hasattr(self, "_event_instance"):
            if self.active_team is None:
                raise Http404
            self._event_instance = get_object_or_404(Event, pk=self.kwargs["pk"], club=self.request.club, teams=self.active_team)
        return self._event_instance

    def get(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseRedirect(reverse("mobile:coach_today"))
        return self.render_to_response(self.get_context_data(**kwargs))

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, lock_recurring="single", event=self.get_event_instance(), **kwargs)

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        form = self.build_event_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        # Same semantics as the API's PATCH /events/{id} (and so the iOS editor): ``scope`` only
        # means something for an occurrence that still follows its series, and only for the fields
        # the series template carries (title, kind, place, opponent, end/gathering/deadline,
        # audience). "Only this event" detaches the occurrence when one of those changed, so the
        # next series edit can't undo it.
        scope = request.POST.get("scope", "this")
        if scope not in ("this", "this_and_following", "all"):
            scope = "this"
        before = self.get_event_instance()
        follows_series = before.series_id is not None and not before.detached
        touched = {api_coach_events.FIELD_ALIASES.get(name, name) for name in form.changed_data} & api_coach_events.TEMPLATE_FIELDS

        with transaction.atomic():
            event = form.save()
            if follows_series and touched:
                if scope == "this":
                    detach_occurrence(event)
                else:
                    series = api_coach_events.split_series(event) if scope == "this_and_following" else event.series
                    event.series = series
                    api_coach_events.apply_to_series(series, event, touched)

        if request.POST.get("notify") == "on":
            notify_event_changed(event)
        notify(request, f"s|{_('Event updated')}|" + _("“%(event)s” has been updated.") % {"event": event})
        return HttpResponseRedirect(_return_url(request, avoid=None) or reverse("mobile:coach_event_detail", kwargs={"pk": event.pk}))


class CoachEditEventSeriesView(CoachCreateEventView):
    """Edit an EventSeries' own recurrence pattern -- same form-building
    machinery, but the ``series_form`` side of it (get_series_instance()
    against an existing series). ``form`` (single-event fields) is still
    built here purely because the shared template references it
    unconditionally; this screen never renders or submits it (lock_recurring
    hides the toggle and locks it onto the series side, see the template).

    propagate_series()+generate_occurrences() on save mirror management.
    views.EventSeriesUpdateView.form_valid exactly: push the changed
    template to future non-detached occurrences, then fill in any further-
    out dates the (possibly changed) pattern now implies. Occurrences that no
    longer match a changed pattern are NOT auto-removed -- same desktop
    reasoning, left as a manual per-occurrence cleanup.
    """

    screen_title = _("Edit series")

    def get_series_instance(self):
        if not hasattr(self, "_series_instance"):
            if self.active_team is None:
                raise Http404
            self._series_instance = get_object_or_404(EventSeries, pk=self.kwargs["pk"], club=self.request.club, teams=self.active_team)
        return self._series_instance

    def get(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseRedirect(reverse("mobile:coach_today"))
        return self.render_to_response(self.get_context_data(**kwargs))

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, lock_recurring="series", series=self.get_series_instance(), **kwargs)

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        series_form = self.build_series_form(request.POST)
        if not series_form.is_valid():
            return self.render_to_response(self.get_context_data(series_form=series_form))

        series = series_form.save()
        propagate_series(series)
        generate_occurrences(series)
        notify(request, f"s|{_('Series updated')}|" + _("“%(series)s” has been updated.") % {"series": series})
        return HttpResponseRedirect(reverse("mobile:coach_today"))


class CoachEventDetachView(CoachScopeMixin, LoginRequiredMixin, View):
    """Stop this occurrence from being touched by future series-wide edits --
    mirrors management.views.EventDetachView exactly. Surfaced on the event
    detail hub whenever event.series_id and not event.detached, since
    editing a still-attached occurrence directly (CoachEditEventView) would
    otherwise be silently overwritten by the next CoachEditEventSeriesView
    save."""

    def post(self, request, *args, **kwargs):
        if self.active_team is None:
            raise Http404
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = get_object_or_404(Event, pk=kwargs["pk"], club=request.club, teams=self.active_team)
        detach_occurrence(event)
        notify(request, f"s|{_('Detached from series')}|" + _("“%(event)s” is now edited independently and won't be touched by future series-wide changes.") % {"event": event})
        return HttpResponseRedirect(_return_url(request) or reverse("mobile:coach_event_detail", kwargs={"pk": event.pk}))


def _is_sheet_request(request):
    """An htmx fetch that is not a boosted navigation: the Today/Events rows load the event detail
    as a bottom sheet over the current tab (hx-get into #sheet-host); a plain visit or a boosted
    link still gets the full page, so deep links, push notifications and redirects keep working."""
    return request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"


def _return_url(request, *, avoid=None):
    """Where a sheet action should land afterwards: the tab it was opened over (``next``, or htmx's
    HX-Current-URL for the sheet itself), if it is a safe local URL and not the screen about to
    stop existing (``avoid``, e.g. a deleted event's own page)."""
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if not candidate and _is_sheet_request(request):
        current = urlsplit(request.headers.get("HX-Current-URL", ""))
        candidate = f"{current.path}?{current.query}" if current.query else current.path
    if not candidate or not url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return ""
    if avoid is not None and str(avoid) in candidate:
        return ""
    return candidate


class CoachEventDetailView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """The coach-facing event hub. Opened from the Today/Events rows it is a bottom sheet over the
    current tab (iOS pushes a screen, the PWA product decision is a sheet): the row fetches this
    URL with hx-get and the response is just the ``<dialog>`` (coach/_event_sheet.html). A direct
    visit, a boosted link or a redirect (after saving an edit, say) gets the full page instead.

    Shows what the iOS CoachEventDetailView shows -- kind chip, score for a game, the info rows,
    Attendance/Line-up links, and (managers) Edit / Edit the repeat pattern / Stop following the
    series / Delete -- plus the PWA's own task management (add/edit/delete; claiming a slot is
    still member-only, mobile.views.EventTaskRespondView).
    """

    template_name = "mobile/coach/event_detail.html"
    sheet_template_name = "mobile/coach/_event_sheet.html"
    screen_title = _("Event")
    active_tab = "coach_schedule"

    def get_template_names(self):
        return [self.sheet_template_name if _is_sheet_request(self.request) else self.template_name]

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        # The same URL answers with a sheet or a page depending on how it was fetched.
        patch_vary_headers(response, ["HX-Request", "HX-Boosted"])
        return response

    def get_event(self):
        if self.active_team is None:
            raise Http404
        return get_object_or_404(Event.objects.select_related("location", "opponent", "series"), pk=self.kwargs["pk"], club=self.request.club, teams=self.active_team)

    def get_context_data(self, **kwargs):
        event = self.get_event()

        tasks = list(event.tasks.prefetch_related("claims__member").order_by("created_at"))
        for task in tasks:
            task.claim_labels = [claim_label_for(claim.member, event.club) for claim in task.claims.all()]
            task.is_full = len(task.claim_labels) >= task.needed_quantity
            task.edit_form = _styled_task_form(instance=task)

        series_summary = ""
        can_edit_pattern = False
        if event.series_id:
            series_summary = describe_rrule(event.series.rrule)
            can_edit_pattern = parse_rrule(event.series.rrule) is not None

        return super().get_context_data(
            event=event,
            tasks=tasks,
            series_summary=series_summary,
            can_edit_pattern=can_edit_pattern,
            is_sheet=_is_sheet_request(self.request),
            return_url=_return_url(self.request, avoid=event.pk),
            **kwargs,
        )


class CoachEventDeleteView(CoachScopeMixin, LoginRequiredMixin, View):
    """The Delete sheet's action, same four outcomes as iOS DeleteEventSheet (and the API's DELETE
    /events/{id}): a one-off is deleted; an occurrence of a series is cancelled but kept
    (``cancel``), removed (``remove``, the series won't regenerate it) or goes with its whole
    series (``series``). ``notify`` tells the invited people first. Managers only."""

    MODES = ("delete", "cancel", "remove", "series")

    def post(self, request, *args, **kwargs):
        if self.active_team is None:
            raise Http404
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = get_object_or_404(Event.objects.select_related("series"), pk=kwargs["pk"], club=request.club, teams=self.active_team)
        mode = request.POST.get("mode", "delete")
        if mode not in self.MODES:
            return HttpResponseBadRequest()
        tell = request.POST.get("notify") == "on"
        title = str(event)
        event_pk = event.pk  # event.delete() clears .pk

        if mode == "series":
            if event.series_id is None:
                return HttpResponseBadRequest()
            series = get_object_or_404(EventSeries, pk=event.series_id, club=request.club, teams=self.active_team)
            name = str(series)
            series.delete()
            notify(request, f"w|{_('Series deleted')}|" + _("“%(series)s” and all of its occurrences have been deleted.") % {"series": name})
        else:
            keep_record = mode == "cancel" and event.series_id is not None
            with transaction.atomic():
                if tell:
                    notify_event_cancelled(event, keep_link=keep_record)
                if event.series_id:
                    cancel_occurrence(event, hard_delete=not keep_record)
                else:
                    event.delete()
            if keep_record:
                notify(request, f"w|{_('Occurrence cancelled')}|" + _("“%(event)s” was cancelled.") % {"event": title})
            else:
                notify(request, f"w|{_('Event deleted')}|" + _("“%(event)s” has been deleted.") % {"event": title})

        return HttpResponseRedirect(_return_url(request, avoid=event_pk) or reverse("mobile:coach_schedule"))


class CoachEventTaskCreateView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """Add a task to an event -- its own screen (mobile/coach/task_form.html),
    same shape as every other coach "add X" flow (New event, Add player, ...),
    not an inline form that pushes the rest of the task list down underneath
    it (issue #18 feedback on the first cut of this screen). Re-renders with
    field errors on an invalid submission rather than management.views.
    EventTaskCreateView's own notify-and-redirect -- a standalone screen has
    room to show them inline; event/created_by still come from the view,
    never the form, same as the desktop version."""

    template_name = "mobile/coach/task_form.html"
    screen_title = _("Add task")
    active_tab = "coach_schedule"

    def get_event(self):
        if self.active_team is None:
            raise Http404
        return get_object_or_404(Event, pk=self.kwargs["pk"], club=self.request.club, teams=self.active_team)

    def get(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseRedirect(reverse("mobile:coach_event_detail", kwargs={"pk": self.kwargs["pk"]}))
        return self.render_to_response(self.get_context_data(**kwargs))

    def get_context_data(self, **kwargs):
        kwargs.setdefault("task_form", _styled_task_form())
        return super().get_context_data(event=self.get_event(), **kwargs)

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = self.get_event()
        form = _styled_task_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(task_form=form))

        task = form.save(commit=False)
        task.event = event
        task.created_by = self.me
        task.save()
        notify(request, f"s|{_('Task added')}|" + _("“%(title)s” was added to this event.") % {"title": task.title})
        return HttpResponseRedirect(reverse("mobile:coach_event_detail", kwargs={"pk": event.pk}))


class CoachEventTaskUpdateView(CoachScopeMixin, LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if self.active_team is None:
            raise Http404
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = get_object_or_404(Event, pk=kwargs["pk"], club=request.club, teams=self.active_team)
        task = get_object_or_404(EventTask, pk=kwargs["task_pk"], event=event)
        form = EventTaskForm(request.POST, instance=task)
        if not form.is_valid():
            for error in form.errors.values():
                notify(request, f"e|{_('Could not update task')}|{' '.join(error)}")
            return HttpResponseRedirect(reverse("mobile:coach_event_detail", kwargs={"pk": event.pk}))

        form.save()
        notify(request, f"s|{_('Task updated')}|" + _("“%(title)s” was updated.") % {"title": task.title})
        return HttpResponseRedirect(reverse("mobile:coach_event_detail", kwargs={"pk": event.pk}))


class CoachEventTaskDeleteView(CoachScopeMixin, LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if self.active_team is None:
            raise Http404
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = get_object_or_404(Event, pk=kwargs["pk"], club=request.club, teams=self.active_team)
        task = get_object_or_404(EventTask, pk=kwargs["task_pk"], event=event)
        title = task.title
        task.delete()
        notify(request, f"w|{_('Task deleted')}|" + _("“%(title)s” was removed from this event.") % {"title": title})
        return HttpResponseRedirect(reverse("mobile:coach_event_detail", kwargs={"pk": event.pk}))


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
    #: Most photos one post can carry (same cap as the app API and the iOS composer).
    MAX_PHOTOS = 10

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
        kwargs.setdefault("next_param", "news" if (self.request.GET.get("next") or self.request.POST.get("next")) == "news" else "")
        kwargs.setdefault("max_photos", self.MAX_PHOTOS)
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if not can_add_news(request.user, request.club):
            return HttpResponseForbidden()

        form = self.build_form(request.POST)
        photo_form = self.build_photo_form(request.POST, request.FILES)
        valid = form.is_valid() and photo_form.is_valid()
        if valid and len(photo_form.cleaned_data["images"]) > self.MAX_PHOTOS:
            photo_form.add_error("images", ngettext("At most %(count)d photo.", "At most %(count)d photos.", self.MAX_PHOTOS) % {"count": self.MAX_PHOTOS})
            valid = False
        if not valid:
            return self.render_to_response(self.get_context_data(form=form, photo_form=photo_form))

        news_item = form.save()
        for index, image in enumerate(photo_form.cleaned_data["images"]):
            NewsPhoto.objects.create(news_item=news_item, image=image, is_main=index == 0)
        news_item.submit_for_review()
        dispatch_notify_editors_of_pending_review(news_item.pk)

        body = _("“%(news)s” is ready for review.") % {"news": news_item}
        notify(request, f"s|{_('Sent for review')}|{body}")
        # Opened from the News tab ("?next=news"): land back on the list, where the new post
        # shows as "Waiting for review". Anywhere else (the + menu) goes home as before.
        return HttpResponseRedirect(reverse("mobile:coach_news" if request.POST.get("next") == "news" else "mobile:coach_today"))


class CoachNewsView(CoachScopeMixin, LoginRequiredMixin, TemplateView):
    """Manager "News" tab (iOS TeamNewsView): the posts *you* submitted for the
    active team, newest first, each with where it stands -- waiting for an
    editor's review, published, scheduled, or not published (taken back to
    draft). Any staff of the team can look; the "Share news" button needs
    club.services.access.can_add_news, same gate as the compose screen.
    """

    template_name = "mobile/coach/news_list.html"
    screen_title = _("Team news")
    active_tab = "coach_news"
    #: Newest posts shown; a coach's own submissions for one team stay small.
    LIMIT = 50

    @staticmethod
    def status_for(post, now):
        """``(key, label, pill class)`` -- the same four states as the iOS pill."""
        if post.status == News.Status.PUBLISHED:
            if post.published_at is not None and post.published_at > now:
                return "scheduled", _("Scheduled"), "pill-info"
            return "published", _("Published"), "pill-ok"
        if post.status == News.Status.PENDING_REVIEW:
            return "pending_review", _("Waiting for review"), "pill-warn"
        return "draft", _("Not published, ask the editor"), "pill-neutral"

    def get_context_data(self, **kwargs):
        posts = []
        if self.active_team is not None and self.me is not None:
            posts = list(News.objects.filter(club=self.request.club, teams=self.active_team, created_by=self.me).prefetch_related("photos").order_by("-created")[: self.LIMIT])
        now = timezone.now()
        for post in posts:
            post.status_key, post.status_label, post.status_class = self.status_for(post, now)
            post.cover = post.main_photo or next(iter(post.photos.all()), None)
        return super().get_context_data(posts=posts, can_post_news=can_add_news(self.request.user, self.request.club), **kwargs)


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
    on this team last season, plus whoever's on any team that feeds into
    this one *this* season (Team.feeds_into -- an explicit, admin-set
    relationship, not a guess from team naming). "Age eligible" from the
    mock still isn't built -- there's no birth-date cutoff to compare
    against, and faking that filter would just mean it silently matched
    nothing. A plain first/last-name search (?q=) narrows the pool further,
    ANDed with whichever filter chip is active -- useful once a club's
    eligible-member pool outgrows a single screenful.
    """

    template_name = "mobile/coach/add_player.html"
    screen_title = _("Add players")
    active_tab = "coach_squad"

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

    #: Why a player is suggested, best first -- the same four reasons (and
    #: order) the app API's candidate list uses; each is a label + a pill look.
    SUGGESTION_REASONS = {
        "age_match": _("Age match"),
        "previous_team": _("Last season"),
        "feeder_team": _("Feeder team"),
        "age_adjacent": _("Close in age"),
    }

    def _age_signals(self, season):
        """``(match, adjacent)`` Q objects on Member for this team's age band, or
        ``(None, None)`` when the team has none -- same rule as the app API's
        candidate list: a player's birth year against the season's start year."""
        team = self.active_team
        if team.age_min is None and team.age_max is None:
            return None, None
        year = season.start_date.year
        oldest = year - team.age_max if team.age_max is not None else None  # earliest birth year
        youngest = year - team.age_min if team.age_min is not None else None  # latest birth year
        match = Q(date_of_birth__isnull=False)
        if oldest is not None:
            match &= Q(date_of_birth__year__gte=oldest)
        if youngest is not None:
            match &= Q(date_of_birth__year__lte=youngest)
        adjacent = Q()
        if oldest is not None:
            adjacent |= Q(date_of_birth__year=oldest - 1)
        if youngest is not None:
            adjacent |= Q(date_of_birth__year=youngest + 1)
        return match, Q(date_of_birth__isnull=False) & adjacent

    def _suggestion_ids(self, pool, season):
        """``{reason: set of member ids in pool}`` for every reason that applies."""
        by_reason = {reason: set() for reason in self.SUGGESTION_REASONS}
        match, adjacent = self._age_signals(season)
        if match is not None:
            by_reason["age_match"] = set(pool.filter(match).values_list("pk", flat=True))
            by_reason["age_adjacent"] = set(pool.filter(adjacent).values_list("pk", flat=True))
        previous_season = Season.before(self.request.club, season)
        if previous_season is not None:
            by_reason["previous_team"] = set(pool.filter(team_memberships__team=self.active_team, team_memberships__season=previous_season).values_list("pk", flat=True))
        by_reason["feeder_team"] = set(pool.filter(team_memberships__team__in=self.active_team.feeder_teams.all(), team_memberships__season=season).values_list("pk", flat=True))
        return by_reason

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
            reasons_by_member = {}
            reason_ids = self._suggestion_ids(pool, season)
            # Best reason wins when a player has several (dict order = rank).
            for reason in self.SUGGESTION_REASONS:
                for member_id in reason_ids[reason]:
                    reasons_by_member.setdefault(member_id, reason)

            if filter_param == "no_team":
                # Staff-only members (a coach/manager with no playing TeamMembership
                # of their own) aren't "unassigned" in the sense this filter means --
                # exclude them the same way TeamMembership is excluded, so they don't
                # show up as add-as-player candidates.
                pool = pool.exclude(team_memberships__season=season).exclude(staff_assignments__season=season)
            elif filter_param == "suggested":
                pool = pool.filter(pk__in=set(reasons_by_member))

            if search_query:
                pool = pool.filter(Q(first_name__icontains=search_query) | Q(last_name__icontains=search_query))

            candidates = list(pool.distinct().order_by("last_name", "first_name"))
            if filter_param == "suggested":
                rank = {reason: index for index, reason in enumerate(self.SUGGESTION_REASONS)}
                candidates.sort(key=lambda member: rank[reasons_by_member[member.pk]])

            teams_by_member = {}
            for membership in TeamMembership.objects.filter(season=season, member_id__in=[member.pk for member in candidates]).select_related("team").order_by("team__name"):
                teams_by_member.setdefault(membership.member_id, []).append(membership.team.name)
            for member in candidates:
                reason = reasons_by_member.get(member.pk)
                member.suggestion_reason = reason
                member.suggestion_label = self.SUGGESTION_REASONS.get(reason)
                member.current_team_names = teams_by_member.get(member.pk, [])
                member.birth_year = member.date_of_birth.year if member.date_of_birth else None

        return super().get_context_data(
            candidates=candidates,
            squad_count=squad_count,
            filter_param=filter_param,
            search_query=search_query,
            filter_chips=[("", _("All")), ("suggested", _("Suggested")), ("no_team", _("No team"))],
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
        search_query = self.request.GET.get("q", "").strip()

        if self.active_team is not None and season is not None:
            pool = self._candidate_pool(season)
            if search_query:
                pool = pool.filter(Q(first_name__icontains=search_query) | Q(last_name__icontains=search_query))
            candidates = list(pool.order_by("last_name", "first_name"))
            positions = Position.objects.filter(club=self.request.club, staff_position=True)

        return super().get_context_data(candidates=candidates, positions=positions, search_query=search_query, **kwargs)

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
        return get_object_or_404(Event, pk=self.kwargs["event_id"], club=self.request.club, teams=self.active_team, kind__in=LINEUP_EVENT_KINDS)

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
        exactly the judgment call a line-up screen exists for. Shown from a
        player's very first response (``minimum_responses=1``) rather than
        rankings' own default-3 floor: that floor exists to keep a
        comparative leaderboard (management's own team ranking board) from
        unfairly burying someone on one data point, but here there's no
        ranking at all, just one player's own number -- a coach picking a
        line-up for game 2 needs whatever signal game 1 gave, not silence
        until game 4."""
        season = current_season(self.request.club)
        memberships_by_member = {}
        rates_by_member = {}
        if season is not None:
            memberships_by_member = {tm.member_id: tm for tm in TeamMembership.objects.filter(team=self.active_team, season=season).select_related("position")}
            rates_by_member = {row["member"].pk: row["rate"] for row in player_attendance_rankings(self.active_team, season, minimum_responses=1)}

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

    def _officiating_info(self, event):
        """Read-only summary of this game's logistics -- open tasks and,
        for a home game the club itself arranges, who's refereeing/
        officiating -- so a coach opening the line-up sees the same picture
        management.views.EventListView's own list row shows on the desktop
        side, without having to go find the event on the desktop app.
        Nothing here is editable from mobile; assigning/claiming still only
        happens on the desktop event page or (for tasks) a member's own
        Home/Calendar screen."""
        task_rows = list(event.tasks.prefetch_related("claims__member"))
        for task in task_rows:
            task.claim_labels = [claim_label_for(claim.member, event.club) for claim in task.claims.all()]
            task.is_full = len(task.claim_labels) >= task.needed_quantity

        needs_referees = needs_referee_management(event)
        referee_rows = list(event.referees.select_related("member")) if needs_referees else []

        needs_officials = officials_enabled_for(self.request.club) and needs_official_management(event)
        official_rows = list(event.officials.select_related("member")) if needs_officials else []

        return {
            "task_rows": task_rows,
            "needs_referees": needs_referees,
            "referee_rows": referee_rows,
            "needs_officials": needs_officials,
            "official_rows": official_rows,
        }

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        patch_cache_control(response, no_store=True, private=True)
        return response

    def get_context_data(self, **kwargs):
        event = self.get_event()
        lineup, _created = Lineup.objects.get_or_create(event=event, defaults={"team": self.active_team, "created_by": self.me})
        unavailable = list(Attendance.objects.filter(event=event, status__in=UNAVAILABLE_STATUSES).select_related("member"))
        categories = self._categories(event, lineup)
        selected_count = sum(1 for category in categories for row in category["rows"] if row["selected"])
        newly_selected, newly_dropped = changes_on_publish(lineup)

        if lineup.scheduled_publish_at:
            lineup_status, lineup_status_label, lineup_status_class = "scheduled", _("Scheduled"), "pill-info"
        elif lineup.published_at:
            lineup_status, lineup_status_label, lineup_status_class = "published", _("Published"), "pill-ok"
        elif selected_count:
            lineup_status, lineup_status_label, lineup_status_class = "draft", _("Draft"), "pill-warn"
        else:
            lineup_status, lineup_status_label, lineup_status_class = "not_started", _("Not started"), "pill-neutral"

        return super().get_context_data(
            event=event,
            lineup=lineup,
            categories=categories,
            unavailable=unavailable,
            selected_count=selected_count,
            lineup_status=lineup_status,
            lineup_status_label=lineup_status_label,
            lineup_status_class=lineup_status_class,
            pending_selected_count=len(newly_selected),
            pending_dropped_count=len(newly_dropped),
            **self._officiating_info(event),
            **kwargs,
        )

    body_template_name = "mobile/coach/_lineup_body.html"

    def render_body(self, *, toast=None, status_text=""):
        """The swap-in partial plus the out-of-band status line/toast."""
        response = render(self.request, self.body_template_name, self.get_context_data(toast=toast, status_text=status_text, inline_response=True))
        patch_cache_control(response, no_store=True, private=True)
        return response

    def finish(self, request, *, toast=None, status_text=""):
        """Inline requests get the partial; everything else the classic redirect."""
        if is_inline_request(request):
            return self.render_body(toast=toast, status_text=status_text)
        if toast:
            level = {"ok": "s", "warn": "w", "error": "e"}[toast["kind"]]
            notify(request, f"{level}|{toast['title']}|{toast['text']}")
        return HttpResponseRedirect(reverse("mobile:coach_lineup", kwargs={"event_id": self.kwargs["event_id"]}))

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


class CoachLineupToggleView(CoachLineupView):
    """One tap on a player row: flips their pick and answers with the refreshed
    list. Saving a pick never touches Attendance or notifies anyone (see
    CoachLineupView's docstring) -- the pending-changes line says who a later
    Publish will tell."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = self.get_event()
        # The client says what it wants (so a double tap or a retry can't flip it back).
        wants_selected = request.POST.get("selected") == "true"

        def work():
            with transaction.atomic():
                lineup, _created = Lineup.objects.select_for_update().get_or_create(event=event, defaults={"team": self.active_team, "created_by": self.me})
                attendance = get_object_or_404(Attendance.objects.exclude(status__in=UNAVAILABLE_STATUSES).select_related("member"), event=event, member_id=kwargs["member_id"])
                if wants_selected != LineupSelection.objects.filter(lineup=lineup, member=attendance.member).exists():
                    toggle_selection(lineup, attendance.member)
            return 200, {"status_text": str(_("Picks saved. Nobody has been told yet."))}

        status, payload = run_once(request, work)
        if payload is None:
            return HttpResponse(status=status)
        return self.finish(request, status_text=payload["status_text"])


class CoachLineupPublishView(CoachLineupView):
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

    Inline (htmx) requests get the refreshed list back with a success/error
    toast and refuse an empty line-up ("Pick at least one player first"),
    like the iOS app; a plain form post keeps the old redirect behaviour.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        event = self.get_event()
        lineup = get_object_or_404(Lineup, event=event)
        action = request.POST.get("action", "publish_now")
        inline = is_inline_request(request)

        def work():
            """Returns (200, {toast}) or (422, {toast}); the toast is all a replay needs, so a
            repeated Idempotency-Key never publishes (or notifies) a second time."""
            has_picks = LineupSelection.objects.filter(lineup=lineup).exists()
            if inline and action != "cancel_schedule" and not has_picks:
                return 422, {"toast": {"kind": "error", "title": str(_("Couldn't publish")), "text": str(_("Pick at least one player first."))}}
            if action == "schedule":
                when = parse_datetime(request.POST.get("publish_at", ""))
                if when is not None and timezone.is_naive(when):
                    when = timezone.make_aware(when)
                if when is None or when <= timezone.now():
                    return 422, {"toast": {"kind": "error", "title": str(_("Could not schedule")), "text": str(_("Pick a date and time in the future."))}}
                schedule_lineup_publish(lineup, when)
                return 200, {"toast": {"kind": "ok", "title": str(_("Publish scheduled")), "text": str(_("The line-up will publish itself automatically at that time."))}}
            if action == "cancel_schedule":
                cancel_scheduled_publish(lineup)
                return 200, {"toast": {"kind": "ok", "title": str(_("Schedule cancelled")), "text": str(_("Publish it manually whenever you are ready."))}}
            was_already_published = lineup.published_at is not None
            newly_selected, newly_dropped = changes_on_publish(lineup)
            told = len(newly_selected) + len(newly_dropped)
            publish_lineup(lineup)
            if inline:
                text = _("Nobody needed to be told.") if not told else ngettext("%(count)d player was told.", "%(count)d players were told.", told) % {"count": told}
                return 200, {"toast": {"kind": "ok", "title": str(_("Published")), "text": str(text)}}
            if was_already_published:
                return 200, {"toast": {"kind": "ok", "title": str(_("Line-up updated")), "text": str(_("Anyone whose status changed has been notified."))}}
            return 200, {"toast": {"kind": "ok", "title": str(_("Line-up published")), "text": str(_("Selected players have been notified."))}}

        status, payload = run_once(request, work)
        if payload is None:
            return HttpResponse(status=status)
        return tag_result(self.finish(request, toast=payload["toast"], status_text=""), status)


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
            # Same "not yet approved on Sign-up" warning management's own
            # TeamDetailView shows -- a coach needs to see it just as much.
            pending_member_ids = set(ClubMembership.objects.filter(club=self.request.club, season=season, member_id__in=[membership.member_id for membership in roster], status=ClubMembership.StatusChoices.PENDING).values_list("member_id", flat=True))
            for membership in roster:
                membership.club_status_pending = membership.member_id in pending_member_ids

        return super().get_context_data(
            roster=roster,
            staff=staff,
            # Gates the "Evaluations" action on a player's sheet -- the feature flag plus the club-wide
            # grant (see mobile.coach_evaluation_views.EvaluationAccessMixin), not a team role.
            evaluations_enabled=flag_is_active(self.request, "evaluations") and can_manage_evaluations(self.request.user, self.request.club),
            **kwargs,
        )


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
        # Current season only: a past season's row of the same team (with its
        # phone numbers) is outside what the coach's current staff role covers.
        return get_object_or_404(TeamMembership.objects.filter(team=self.active_team, season=get_current_season(self.request)).select_related("member", "position"), pk=self.kwargs["membership_pk"])

    def build_form(self, membership, data=None):
        return CoachRosterEditForm(data, instance=membership, club=self.request.club, team=self.active_team, season=membership.season)

    def get_context_data(self, **kwargs):
        membership = self.get_membership()
        member = membership.member

        kwargs.setdefault("form", self.build_form(membership) if self.can_manage_active_team else None)
        return super().get_context_data(
            membership=membership,
            member=member,
            guardians=member.guardians(self.request.club),
            attendance_counts=member_attendance_counts(member, membership.season),
            # Gates the "Evaluations" row below -- a club-wide grant (see
            # mobile.coach_evaluation_views.EvaluationAccessMixin), not
            # can_manage_active_team, so a coach who merely staffs this team
            # doesn't see it just because they can edit the roster.
            evaluations_enabled=flag_is_active(self.request, "evaluations") and can_manage_evaluations(self.request.user, self.request.club),
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

        membership = get_object_or_404(TeamMembership.objects.filter(team=self.active_team, season=get_current_season(request)), pk=kwargs["membership_pk"])
        member = membership.member
        membership.delete()

        notify(request, f"w|{_('Player removed')}|" + _("“%(member)s” removed from the roster.") % {"member": member})
        return HttpResponseRedirect(reverse("mobile:coach_squad"))


class CoachStaffRemoveView(CoachSquadView):
    """Squad screen's per-staff-row remove action. A manager can't remove
    their own StaffAssignment this way -- self-removal would strand them off
    a team they're actively viewing, with no one obviously left to undo it;
    that stays a desktop-only action (management.views.TeamStaffRemoveView),
    which any *other* admin/manager can still reach.

    Inline (htmx) requests get the refreshed squad list back with a toast --
    a refused self-removal is a toast too ("You can't remove yourself from the
    staff."); a plain form post keeps the 403/redirect behaviour.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if self.active_team is None:
            raise Http404
        if not self.can_manage_active_team:
            return HttpResponseForbidden()

        assignment = get_object_or_404(StaffAssignment.objects.filter(team=self.active_team, season=get_current_season(request)), pk=kwargs["assignment_pk"])
        inline = is_inline_request(request)
        if self.me is not None and assignment.member_id == self.me.pk:
            if not inline:
                return HttpResponseForbidden()
            return render(request, "mobile/coach/_squad_body.html", self.get_context_data(inline_response=True, toast={"kind": "error", "title": _("Couldn't remove"), "text": _("You can't remove yourself from the staff.")}))

        member = assignment.member
        assignment.delete()

        title = _("Staff removed")
        text = _("“%(member)s” removed from staff.") % {"member": member}
        if inline:
            return render(request, "mobile/coach/_squad_body.html", self.get_context_data(inline_response=True, toast={"kind": "ok", "title": title, "text": text}))
        notify(request, f"w|{title}|{text}")
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
    screen_title = _("Events")
    active_tab = "coach_schedule"

    def get_context_data(self, **kwargs):
        # ?kind= narrows the list to one of the four coach kinds (the iOS Events tab's chip bar);
        # anything else is "All".
        kind = self.request.GET.get("kind", "")
        if kind not in COACH_EVENT_KINDS:
            kind = ""
        this_week, next_week, later_months = [], [], []
        if self.active_team is not None:
            events = Event.objects.filter(teams=self.active_team, cancelled=False, start__gte=timezone.now()).select_related("location", "series").order_by("start")
            if kind:
                events = events.filter(kind=kind)
            this_week, next_week, later_months = agenda_groups(list(events))

        return super().get_context_data(
            this_week=this_week,
            next_week=next_week,
            later_months=later_months,
            kind_filter=kind,
            kind_choices=[(kind_value, Event.EventKind(kind_value).label) for kind_value in COACH_EVENT_KINDS],
            **kwargs,
        )
