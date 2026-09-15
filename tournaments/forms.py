from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from club.models import Season
from members.models import Member
from teams.models import Team

from .models import DressingRoom, Tournament, TournamentField, TournamentRegistration, TournamentRosterEntry, TournamentRosterRole, TournamentStage, TournamentTask, TournamentTeam


class TournamentRegistrationForm(forms.ModelForm):
    """The public tournament-entry form's "about your team" half -- a visiting
    (foreign) team's identity and contact details. The host club's own team
    never reaches this form at all: it skips straight to a live
    ``TournamentTeam`` via a staff-side shortcut (see ``TournamentRegistration``'s
    own docstring), so there's no team picker here -- only the ``foreign_*``
    fields, matching the model's own team-xor-foreign_name split.

    ``foreign_name``/``foreign_contact_name``/``foreign_contact_email`` are
    ``blank=True`` on the model (shared with ``TournamentTeam``, where a
    club-team row legitimately has none of them set) but are required here --
    a public submission needs a team name and someone to reach.
    """

    class Meta:
        model = TournamentRegistration
        fields = ["foreign_name", "foreign_contact_name", "foreign_contact_email", "foreign_contact_phone", "foreign_primary_color", "foreign_secondary_color", "foreign_logo"]
        widgets = {
            "foreign_primary_color": forms.TextInput(attrs={"placeholder": "#1e40af"}),
            "foreign_secondary_color": forms.TextInput(attrs={"placeholder": "#be185d"}),
            "foreign_logo": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/gif,image/webp,image/svg+xml"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("foreign_name", "foreign_contact_name", "foreign_contact_email"):
            self.fields[name].required = True


class TournamentRegistrationPlayerRowForm(forms.Form):
    """One row of the roster submitted alongside a ``TournamentRegistration`` --
    same "entirely optional at the field level, a blank row is simply
    skipped" idiom as ``registration.forms.RegistrationEntryRowForm``. The
    roster itself is optional too (a team may register before its roster is
    finalized, and add players later via TournamentRegistrationStatusView) --
    the formset below doesn't require any non-blank row.

    ``tournament`` (threaded through via the formset's own ``form_kwargs``,
    not a plain constructor default -- every call site now passes it) drives
    whether/how ``lunch_days`` even appears: no field at all when
    ``lunch_mode`` is NONE, a per-day checkbox group when OPTIONAL, nothing
    shown (everyone gets every day, decided in ``lunch_days_value()`` below
    rather than left for the submitter to tick) when MANDATORY. ``role``
    carries the ``role-select``/``date_of_birth`` the ``player-only`` CSS
    hook static/js/bulk-add-rows.js's own ``syncPlayerOnlyFields`` already
    understands (extended there to recognise ``.role-select`` alongside the
    team roster's own ``.position-select``), so a Staff row greys out its
    date-of-birth input the same way a staff position already greys out
    jersey/captaincy there.
    """

    first_name = forms.CharField(label=_("First name"), max_length=150, required=False)
    last_name = forms.CharField(label=_("Last name"), max_length=150, required=False)
    jersey_number = forms.IntegerField(label=_("Jersey number"), required=False, min_value=0)
    #: "role-select"/"player-only" as data attrs, not CSS classes -- this
    #: form renders through the form_field template tag (controlpanel/
    #: templatetags/field.html), which already hardcodes its own "class" on
    #: every widget and then
    #: layers the field's *own* widget.attrs on top verbatim, duplicate
    #: "class" attribute and all -- a browser keeps only the first one it
    #: sees, so a plain CSS class added here would silently never apply. A
    #: data attribute has no such collision (nothing else in field.html sets
    #: one), which is exactly why static/js/bulk-add-rows.js's own
    #: syncPlayerOnlyFields looks for "[data-role-select]"/"[data-player-only]"
    #: here rather than the ".position-select"/".player-only" *classes* it
    #: already uses for the team roster's own bulk-add row (rendered via
    #: plain {{ form.field }} there, so a class attr collides with nothing).
    role = forms.ChoiceField(label=_("Role"), choices=TournamentRosterRole.choices, initial=TournamentRosterRole.PLAYER, required=False, widget=forms.Select(attrs={"data-role-select": "1"}))
    position_label = forms.CharField(label=_("Position"), max_length=100, required=False)
    date_of_birth = forms.DateField(label=_("Date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date", "data-player-only": "1"}))
    license_number = forms.CharField(label=_("License number"), max_length=100, required=False)
    dietary_restrictions = forms.CharField(label=_("Dietary restrictions"), required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, tournament=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tournament = tournament
        if tournament is not None and tournament.lunch_mode == Tournament.LunchMode.OPTIONAL:
            self.fields["lunch_days"] = forms.MultipleChoiceField(
                label=_("Lunch days"),
                required=False,
                choices=[(day.isoformat(), day.strftime("%a %d %b")) for day in tournament.date_range()],
                widget=forms.CheckboxSelectMultiple,
            )

    def has_a_person(self):
        data = self.cleaned_data
        return bool(data.get("first_name") or data.get("last_name"))

    def clean(self):
        cleaned = super().clean()
        if not self.has_a_person():
            return cleaned  # a genuinely blank row -- the formset skips it entirely

        if not cleaned.get("last_name"):
            self.add_error("last_name", _("Enter a last name."))
        if not cleaned.get("role"):
            cleaned["role"] = TournamentRosterRole.PLAYER
        if cleaned.get("role") == TournamentRosterRole.STAFF and cleaned.get("date_of_birth"):
            cleaned["date_of_birth"] = None  # a staff row's DOB is greyed out client-side; a bypassed value is just dropped, not an error
        return cleaned

    def lunch_days_value(self) -> list:
        """The list of ISO date strings to actually store -- every tournament
        day when lunch is MANDATORY (nothing for the submitter to have
        picked, since no field is rendered for that case), whatever was
        ticked when OPTIONAL, or an empty list when there's no lunch at
        all."""
        if self.tournament is None or self.tournament.lunch_mode == Tournament.LunchMode.NONE:
            return []
        if self.tournament.lunch_mode == Tournament.LunchMode.MANDATORY:
            return [day.isoformat() for day in self.tournament.date_range()]
        return list(self.cleaned_data.get("lunch_days") or [])


class BaseTournamentRegistrationPlayerFormSet(forms.BaseFormSet):
    def non_blank_forms(self):
        return [form for form in self.forms if form.cleaned_data and form.has_a_person()]


TournamentRegistrationPlayerFormSet = forms.formset_factory(TournamentRegistrationPlayerRowForm, formset=BaseTournamentRegistrationPlayerFormSet, extra=1)


def players_from_formset(player_formset):
    """The shared bridge from a validated TournamentRegistrationPlayerFormSet
    to a list of ``TournamentRegistrationPlayer`` field-kwargs dicts -- mirrors
    ``registration.forms.entries_from_formset``'s own role."""
    return [
        {
            "first_name": row.cleaned_data.get("first_name") or "",
            "last_name": row.cleaned_data["last_name"],
            "jersey_number": row.cleaned_data.get("jersey_number"),
            "role": row.cleaned_data.get("role") or TournamentRosterRole.PLAYER,
            "position_label": row.cleaned_data.get("position_label") or "",
            "date_of_birth": row.cleaned_data.get("date_of_birth"),
            "license_number": row.cleaned_data.get("license_number") or "",
            "dietary_restrictions": row.cleaned_data.get("dietary_restrictions") or "",
            "lunch_days": row.lunch_days_value(),
        }
        for row in player_formset.non_blank_forms()
    ]


def roster_entries_from_formset(player_formset):
    """The same bridge as ``players_from_formset``, but into
    ``TournamentRosterEntry`` field-kwargs dicts instead of
    ``TournamentRegistrationPlayer`` ones -- used by
    ``TournamentRegistrationStatusView`` once a foreign team's own roster
    editing has moved from the pending submission onto its live roster (see
    that view's own docstring). Reuses the exact same
    ``TournamentRegistrationPlayerFormSet``/row form/``_player_row.html`` as
    the pending editor -- only the target model's name fields differ
    (``foreign_first_name``/``foreign_last_name`` here, not a flat
    ``first_name``/``last_name``), which is all this second bridge function
    exists to translate."""
    return [
        {
            "foreign_first_name": row.cleaned_data.get("first_name") or "",
            "foreign_last_name": row.cleaned_data["last_name"],
            "jersey_number": row.cleaned_data.get("jersey_number"),
            "role": row.cleaned_data.get("role") or TournamentRosterRole.PLAYER,
            "position_label": row.cleaned_data.get("position_label") or "",
            "date_of_birth": row.cleaned_data.get("date_of_birth"),
            "license_number": row.cleaned_data.get("license_number") or "",
            "dietary_restrictions": row.cleaned_data.get("dietary_restrictions") or "",
            "lunch_days": row.lunch_days_value(),
        }
        for row in player_formset.non_blank_forms()
    ]


class TournamentForm(forms.ModelForm):
    """Create/edit a tournament itself -- closes the "admin-only" gap
    TournamentListView used to describe in its own docstring. ``club`` is
    never a form field (set by the view, same as ``TeamForm``'s own
    club-scoped fields in ``management.forms``).

    ``event`` is deliberately NOT a form field -- it used to be a plain
    picker, but now that ``ensure_home_team`` auto-creates and re-points
    it at whichever team is ``home_team``, letting staff also hand-pick
    it here just invited the two to disagree. The settings template shows
    it as a plain link to whatever's currently linked instead (see
    tournament_form.html).

    ``season`` isn't a form field at all -- picking one by hand duplicated
    the start/end dates already on the form and, for a club with no season
    covering "today" (a fresh club, a demo, a tournament scheduled between
    two seasons), left the picker with nothing valid to select at all (a
    required field an admin physically cannot fill in). ``clean()`` resolves
    it from the date range instead: whichever of this club's seasons
    contains both ``start_date`` and ``end_date``. No match raises a
    validation error rather than silently leaving the tournament unassigned
    -- ``Tournament.season`` is a required, PROTECTed FK (the approval flow
    reads a club team's *that season's* roster off it), so there has to be
    a real one.

    ``registration_open`` is the manual override that closes entries early
    (e.g. once full) without touching ``status`` -- see
    ``Tournament.accepts_registrations``, the one place both are checked
    together.
    """

    class Meta:
        model = Tournament
        fields = [
            "name",
            "status",
            "start_date",
            "end_date",
            "registration_open",
            "max_teams",
            "min_age",
            "max_age",
            "contact_email",
            "description",
            "home_team",
            "deposit_amount",
            "entry_fee_amount",
            "lunch_mode",
            "lunch_price_per_day",
            "lunch_billed_with_fee",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._club = club
        self.fields["home_team"].queryset = Team.objects.filter(club=club)
        self.fields["home_team"].required = False
        self.fields["home_team"].empty_label = _("— none —")

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", _("Must be on or after the start date."))
        elif start and end:
            season = Season.objects.filter(club=self._club, start_date__lte=start, end_date__gte=end).first()
            if season is None:
                self.add_error(None, _("No season on this club covers that date range. Add a season that includes these dates first."))
            else:
                self.instance.season = season
        return cleaned


class TournamentFieldForm(forms.ModelForm):
    """Create/edit one bookable field/pitch. ``availability`` is a JSON list
    of per-day windows on the model (see ``TournamentField.availability``'s
    own docstring), but this first-pass screen only ever collects **one**
    date + start + end, saved as a single-entry list -- multi-window/multi-
    day editing is a known simplification, not solved here; a club that
    needs more can still use the Django admin's raw JSON widget for that one
    field. ``tournament`` comes from the view, never the form. No
    ``location`` field here at all -- see ``TournamentField``'s own
    docstring for why a field's venue is just the tournament's."""

    availability_date = forms.DateField(label=_("Date"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    availability_start = forms.TimeField(label=_("Start time"), required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    availability_end = forms.TimeField(label=_("End time"), required=False, widget=forms.TimeInput(attrs={"type": "time"}))

    class Meta:
        model = TournamentField
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        window = (self.instance.availability or [None])[0] if self.instance.pk else None
        if window:
            self.fields["availability_date"].initial = window.get("date")
            self.fields["availability_start"].initial = window.get("start")
            self.fields["availability_end"].initial = window.get("end")

    def clean(self):
        cleaned = super().clean()
        date_, start, end = cleaned.get("availability_date"), cleaned.get("availability_start"), cleaned.get("availability_end")
        if (date_ or start or end) and not (date_ and start and end):
            raise ValidationError(_("Set a date, start time, and end time together, or leave all three blank."))
        if start and end and end <= start:
            self.add_error("availability_end", _("Must be after the start time."))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        date_, start, end = self.cleaned_data.get("availability_date"), self.cleaned_data.get("availability_start"), self.cleaned_data.get("availability_end")
        instance.availability = [{"date": date_.isoformat(), "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}] if (date_ and start and end) else []
        if commit:
            instance.save()
        return instance


#: Every key tournaments.services.scheduling.compute_standings' own
#: _break_ties actually understands -- "head_to_head" is a special case
#: (a completed-matches mini-table among just the tied teams), the rest
#: read straight off compute_standings' own per-team row dict. Kept here
#: (not on the model) since it's purely a TournamentStageForm concern --
#: the scheduler itself just reads whatever list of strings it's given.
TIEBREAKER_CHOICES = [
    ("goal_diff", _("Goal difference")),
    ("goals_for", _("Goals scored")),
    ("goals_against", _("Goals conceded")),
    ("won", _("Wins")),
    ("head_to_head", _("Head-to-head")),
]


class TournamentStageForm(forms.ModelForm):
    """Create/edit one stage. ``config``/``standings_config`` used to be
    free-form JSON textareas here -- unreadable to anyone who didn't already
    know tournaments.services.scheduling's own internal key names by heart.
    Replaced with the small number of fields the scheduler actually reads
    from them (``rounds``/``num_groups`` from ``config`` --
    ``generate_next_swiss_round``/``generate_stage_matches`` are the only
    two readers; ``points``/``tiebreakers`` from ``standings_config`` --
    ``compute_standings`` the only reader), shown or hidden by the selected
    ``format`` via a bit of inline JS on the template side (tournament_field_
    stage_list.html) -- this form itself always validates/saves all of them
    regardless of which are currently visible, same as any other
    conditionally-shown field elsewhere in this app.

    This is still a known simplification, not the full flexibility raw JSON
    offered (only two tiebreakers, no seeding method / best-of / anything
    else a future format might want) -- a club that needs more can still
    reach for the Django admin's raw JSON widget on this one stage, same
    "structured UI covers the common case, admin covers the rest" idiom
    ``TournamentFieldForm``'s own ``availability`` editor already uses.
    ``tournament`` comes from the view, never the form."""

    rounds = forms.IntegerField(label=_("Number of rounds"), required=False, min_value=1, help_text=_("Swiss format only."))
    num_groups = forms.IntegerField(label=_("Number of groups"), required=False, min_value=1, help_text=_("Groups format only."))
    points_win = forms.IntegerField(label=_("Points for a win"), required=False, min_value=0, initial=3)
    points_draw = forms.IntegerField(label=_("Points for a draw"), required=False, min_value=0, initial=1)
    points_loss = forms.IntegerField(label=_("Points for a loss"), required=False, min_value=0, initial=0)
    tiebreaker_1 = forms.ChoiceField(label=_("1st tiebreaker"), required=False, choices=TIEBREAKER_CHOICES, initial="goal_diff")
    tiebreaker_2 = forms.ChoiceField(label=_("2nd tiebreaker"), required=False, choices=[("", "—"), *TIEBREAKER_CHOICES], initial="goals_for")

    class Meta:
        model = TournamentStage
        fields = ["name", "sequence", "format"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = self.instance.config or {}
        standings = self.instance.standings_config or {}
        points = standings.get("points", {})
        tiebreakers = standings.get("tiebreakers", [])
        self.fields["rounds"].initial = config.get("rounds")
        self.fields["num_groups"].initial = config.get("num_groups")
        self.fields["points_win"].initial = points.get("win", 3)
        self.fields["points_draw"].initial = points.get("draw", 1)
        self.fields["points_loss"].initial = points.get("loss", 0)
        self.fields["tiebreaker_1"].initial = tiebreakers[0] if len(tiebreakers) > 0 else "goal_diff"
        self.fields["tiebreaker_2"].initial = tiebreakers[1] if len(tiebreakers) > 1 else "goals_for"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("format") == TournamentStage.Format.SWISS and not cleaned.get("rounds"):
            self.add_error("rounds", _("Required for a Swiss stage."))
        if cleaned.get("format") == TournamentStage.Format.GROUPS and not cleaned.get("num_groups"):
            self.add_error("num_groups", _("Required for a groups stage."))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)

        config = {}
        if self.cleaned_data.get("rounds"):
            config["rounds"] = self.cleaned_data["rounds"]
        if self.cleaned_data.get("num_groups"):
            config["num_groups"] = self.cleaned_data["num_groups"]
        instance.config = config

        tiebreakers = [value for value in (self.cleaned_data.get("tiebreaker_1"), self.cleaned_data.get("tiebreaker_2")) if value]
        instance.standings_config = {
            "points": {
                "win": self.cleaned_data.get("points_win") if self.cleaned_data.get("points_win") is not None else 3,
                "draw": self.cleaned_data.get("points_draw") if self.cleaned_data.get("points_draw") is not None else 1,
                "loss": self.cleaned_data.get("points_loss") if self.cleaned_data.get("points_loss") is not None else 0,
            },
            "tiebreakers": tiebreakers or ["goal_diff", "goals_for"],
        }

        if commit:
            instance.save()
        return instance


class TournamentTaskForm(forms.ModelForm):
    """Create/edit one volunteer shift definition -- mirrors
    ``management.forms.EventTaskForm`` plus the time/place fields a
    tournament-wide shift needs that a single-event favour doesn't.
    ``tournament``/``created_by`` come from the view, never the form. The
    model's own ``clean()`` already rejects an end before the start."""

    class Meta:
        model = TournamentTask
        fields = ["title", "description", "starts_at", "ends_at", "location_label", "needed_quantity"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "needed_quantity": forms.NumberInput(attrs={"min": "1"}),
        }


class TournamentTaskGenerateForm(forms.Form):
    """The Tasks tab's own "Generate shifts" action -- alongside the single
    ad-hoc TournamentTaskForm above, this bulk-creates a whole run of
    same-length shifts covering one window (e.g. "Gate duty, 09:00-12:00,
    30 minutes each" -> 6 tasks) instead of filling in that same form over
    and over by hand. The actual slicing is
    ``tournaments.services.tasks.generate_task_shifts``, not this form's own
    ``save()`` -- there isn't one, same "form validates, a service function
    does the real work" split ``TournamentTeamForm`` already uses.

    ``location_label`` is plain free text -- a tournament field's own name,
    an entrance, a kiosk, whatever staff type; no picker over
    ``TournamentField`` (dropped -- most tournaments only have one or two
    fields, so a free-text label is no more typing than a picker and
    doesn't need this form to know about ``TournamentField`` at all)."""

    title = forms.CharField(label=_("Title"), max_length=255)
    location_label = forms.CharField(label=_("Location"), max_length=255, required=False)
    starts_at = forms.DateTimeField(label=_("Starts at"), widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    ends_at = forms.DateTimeField(label=_("Ends at"), widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    shift_minutes = forms.IntegerField(label=_("Shift length (minutes)"), min_value=5, initial=30)
    needed_quantity = forms.IntegerField(label=_("People needed per shift"), min_value=1, initial=1)

    def __init__(self, *args, tournament=None, **kwargs):
        # tournament kept as an accepted-and-ignored kwarg -- the view still
        # passes one (matching every other tournament-scoped form's own
        # __init__ shape) even though nothing here needs it anymore now
        # that location_field is gone.
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("starts_at"), cleaned.get("ends_at")
        if start and end and end <= start:
            self.add_error("ends_at", _("Must be after the start time."))
        return cleaned


class TournamentTaskAssignForm(forms.Form):
    """A staff-facing counterpart to the mobile self-claim flow -- a
    tournament admin picking any club member for an open shift, e.g. a
    parent who agreed in person rather than through the app.
    ``tournaments.services.tasks.claim_task`` does the actual claim (same
    capacity/duplicate checks either path goes through); this is just the
    picker. ``club``/``tournament`` come from the view, never the form."""

    member = forms.ModelChoiceField(queryset=Member.objects.none(), label=_("Member"), widget=forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")}))

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        # A shift is as often covered by a parent (a FamilyMembership, no
        # ClubMembership of their own) as by a player/coach (member_of) --
        # unlike SignupLinkMemberForm's own member_of-only queryset (which
        # deliberately wants "real club members" only), this needs both.
        self.fields["member"].queryset = Member.objects.filter(Q(member_of__club=club) | Q(family_memberships__family__club=club)).distinct().order_by("last_name", "first_name")


class DressingRoomForm(forms.ModelForm):
    """Create/edit one of the club's own dressing rooms -- a club-wide,
    persists-across-tournaments record (see ``DressingRoom``'s own
    docstring), so this is reached from Settings, not from any one
    tournament's own pages. No ``location`` field -- same simplification
    as ``TournamentField``'s own dropped location: a room is just at the
    club's own location, not a separately-picked one. ``club`` comes from
    the view, never the form (kept as a kwarg for a consistent
    signature with this file's other club-scoped forms, even though
    nothing here reads it any more)."""

    class Meta:
        model = DressingRoom
        fields = ["name", "capacity", "notes"]
        widgets = {
            "capacity": forms.NumberInput(attrs={"min": "0"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)


class DressingRoomAssignmentForm(forms.Form):
    """The "assign a team to a room" action on a tournament's own Dressing
    rooms tab -- a plain Form, not a ModelForm, since it's never used to
    create a ``DressingRoomAssignment`` row directly: the view always routes
    through ``tournaments.services.dressing_rooms.assign_dressing_room``,
    which does the actual double-booking check that model alone can't
    express (see that module's own docstring)."""

    dressing_room = forms.ModelChoiceField(queryset=DressingRoom.objects.none(), label=_("Dressing room"))
    tournament_team = forms.ModelChoiceField(queryset=TournamentTeam.objects.none(), label=_("Team"))
    assigned_from = forms.DateTimeField(required=False, label=_("From"), help_text=_("Leave both dates blank to book the room for the whole tournament."), widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    assigned_until = forms.DateTimeField(required=False, label=_("Until"), widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    note = forms.CharField(required=False, label=_("Note"), widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, club=None, tournament=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["dressing_room"].queryset = DressingRoom.objects.filter(club=club)
        self.fields["tournament_team"].queryset = TournamentTeam.objects.filter(tournament=tournament, withdrawn_at__isnull=True)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("assigned_from"), cleaned.get("assigned_until")
        if start and end and end <= start:
            self.add_error("assigned_until", _("Must be after the start."))
        return cleaned


class TournamentTeamForm(forms.Form):
    """The management-only "Add team" modal -- creates a ``TournamentTeam``
    directly (``registration=None``), bypassing the public queue entirely,
    same shortcut ``TournamentTeam``'s own docstring already describes for
    the host club's own team, extended here to any team staff want to enter
    by hand (a late walk-up, one that registered by phone). A plain
    ``Form``, not a ``ModelForm`` split the ``team``/``foreign_*`` way
    ``TournamentRegistrationForm`` is -- the ``kind`` choice below switches
    which set of fields is actually required, which a single ModelForm's
    ``Meta.fields`` can't express, and the actual ``TournamentTeam``
    creation (plus, for a club team, its roster snapshot) goes through
    ``tournaments.services.registration.create_manual_team`` rather than
    this form's own ``save()`` -- there isn't one."""

    KIND_CLUB = "club"
    KIND_FOREIGN = "foreign"

    kind = forms.ChoiceField(label=_("Team"), choices=[(KIND_CLUB, _("Our own team")), (KIND_FOREIGN, _("Another team"))], widget=forms.RadioSelect, initial=KIND_CLUB)
    team = forms.ModelChoiceField(queryset=Team.objects.none(), required=False, label=_("Team"))
    foreign_name = forms.CharField(required=False, max_length=255, label=_("Team name"))
    foreign_contact_name = forms.CharField(required=False, max_length=255, label=_("Contact name"))
    foreign_contact_email = forms.EmailField(required=False, label=_("Contact email"))
    foreign_contact_phone = forms.CharField(required=False, max_length=32, label=_("Contact phone"))
    foreign_primary_color = forms.CharField(required=False, max_length=7, label=_("Primary color"), widget=forms.TextInput(attrs={"placeholder": "#1e40af"}))
    foreign_secondary_color = forms.CharField(required=False, max_length=7, label=_("Secondary color"), widget=forms.TextInput(attrs={"placeholder": "#be185d"}))
    foreign_logo = forms.ImageField(required=False, label=_("Logo"), widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/gif,image/webp,image/svg+xml"}))

    def __init__(self, *args, club=None, tournament=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Excludes a club team already entered in this tournament -- the
        # model's own unique_real_team_per_tournament constraint would
        # reject a duplicate anyway, but filtering it out of the picker
        # avoids the round trip to find that out.
        self.fields["team"].queryset = Team.objects.filter(club=club).exclude(tournament_entries__tournament=tournament)

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        if kind == self.KIND_CLUB:
            if not cleaned.get("team"):
                self.add_error("team", _("Choose a team."))
        elif kind == self.KIND_FOREIGN:
            for name in ("foreign_name", "foreign_contact_name", "foreign_contact_email"):
                if not cleaned.get(name):
                    self.add_error(name, _("This field is required."))
        return cleaned

    def foreign_fields(self) -> dict:
        """The ``foreign_*`` kwargs for ``create_manual_team`` -- omits
        ``foreign_logo`` entirely when none was uploaded rather than passing
        ``None`` (an ``ImageField`` wants its own empty string, not ``None``,
        for "no file")."""
        data = {
            "foreign_name": self.cleaned_data.get("foreign_name", ""),
            "foreign_contact_name": self.cleaned_data.get("foreign_contact_name", ""),
            "foreign_contact_email": self.cleaned_data.get("foreign_contact_email", ""),
            "foreign_contact_phone": self.cleaned_data.get("foreign_contact_phone", ""),
            "foreign_primary_color": self.cleaned_data.get("foreign_primary_color", ""),
            "foreign_secondary_color": self.cleaned_data.get("foreign_secondary_color", ""),
        }
        if self.cleaned_data.get("foreign_logo"):
            data["foreign_logo"] = self.cleaned_data["foreign_logo"]
        return data


class TournamentRosterEntryForm(forms.ModelForm):
    """The management-only "always editable" roster screen -- one
    ``TournamentRosterEntry`` at a time, for either a club team's snapshotted
    row or a foreign team's, regardless of tournament status (see
    ``TournamentTeamRosterView``'s own docstring for why this screen exists
    at all: it's where DOB/license/dietary get filled in for a club-team
    row, since ``TeamMembership`` has no such fields to snapshot from in the
    first place).

    ``foreign_first_name``/``foreign_last_name`` are dropped from the form
    entirely for an existing *member*-backed row (name comes from the real
    Member there, not something this screen edits) -- kept for a new row or
    an existing foreign-backed one, where they're how the person is actually
    named.

    No ``lunch_days`` field here at all, unlike the public row form's own --
    this form renders through controlpanel/_form_fields.html's blind
    ``{% for field in form %}`` loop (every modal-form/card-form in this app
    shares it), which only knows how to render ``form_field``'s handful of
    widget types and has no per-field override hook to special-case a
    ``CheckboxSelectMultiple`` the way ``_player_row.html`` hand-rolls it.
    ``TournamentRosterEntryLunchUpdateView``/the roster template's own inline
    checkbox row cover editing it instead, the same single-field-inline-post
    idiom ``TournamentTeamPaymentUpdateView`` already uses for
    ``payment_status``. ``save()`` still resolves the NONE/MANDATORY cases
    here (fully determined either way, nothing to edit), leaving an existing
    OPTIONAL value untouched rather than wiping it on every unrelated field
    edit."""

    class Meta:
        model = TournamentRosterEntry
        fields = ["foreign_first_name", "foreign_last_name", "jersey_number", "role", "position_label", "date_of_birth", "license_number", "dietary_restrictions"]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "dietary_restrictions": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, tournament=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tournament = tournament
        self.fields["position_label"].required = True
        if self.instance.pk and self.instance.member_id:
            del self.fields["foreign_first_name"]
            del self.fields["foreign_last_name"]
        else:
            # A new entry (or an existing foreign-backed one) has no Member
            # to fall back on -- the model's own member_xor_foreign_name
            # constraint needs both set, so this can't be left to blank=True
            # (which only describes the model, not what a *new* row here
            # actually needs).
            self.fields["foreign_first_name"].required = True
            self.fields["foreign_last_name"].required = True

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("role") == TournamentRosterRole.STAFF and cleaned.get("date_of_birth"):
            self.add_error("date_of_birth", _("Staff rows don't need a date of birth."))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.tournament is not None and self.tournament.lunch_mode == Tournament.LunchMode.NONE:
            instance.lunch_days = []
        elif self.tournament is not None and self.tournament.lunch_mode == Tournament.LunchMode.MANDATORY:
            instance.lunch_days = [day.isoformat() for day in self.tournament.date_range()]
        if commit:
            instance.save()
        return instance
