from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from club.models import Season
from events.models import Event, Location

from .models import DressingRoom, Tournament, TournamentField, TournamentRegistration, TournamentStage, TournamentTask, TournamentTeam


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
    skipped" idiom as ``registration.forms.RegistrationEntryRowForm``; the
    formset itself (below) requires at least one non-blank row."""

    first_name = forms.CharField(label=_("First name"), max_length=150, required=False)
    last_name = forms.CharField(label=_("Last name"), max_length=150, required=False)
    jersey_number = forms.IntegerField(label=_("Jersey number"), required=False, min_value=0)

    def has_a_person(self):
        data = self.cleaned_data
        return bool(data.get("first_name") or data.get("last_name"))

    def clean(self):
        cleaned = super().clean()
        if not self.has_a_person():
            return cleaned  # a genuinely blank row -- the formset skips it entirely

        if not cleaned.get("last_name"):
            self.add_error("last_name", _("Enter a last name."))
        return cleaned


class BaseTournamentRegistrationPlayerFormSet(forms.BaseFormSet):
    def non_blank_forms(self):
        return [form for form in self.forms if form.cleaned_data and form.has_a_person()]

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not self.non_blank_forms():
            raise ValidationError(_("Add at least one player to the roster."))


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
        }
        for row in player_formset.non_blank_forms()
    ]


class TournamentForm(forms.ModelForm):
    """Create/edit a tournament itself -- closes the "admin-only" gap
    TournamentListView used to describe in its own docstring. ``club`` is
    never a form field (set by the view, same as ``TeamForm``'s own
    club-scoped fields in ``management.forms``); ``event`` is scoped to the
    given club in ``__init__`` the same way.

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
        fields = ["name", "status", "start_date", "end_date", "registration_open", "contact_email", "description", "event"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._club = club
        # An Event already claimed by some other Tournament is excluded --
        # except this form's own instance's current event, so editing a
        # tournament without touching this field doesn't spuriously drop it
        # from the choices. See Tournament.event's own docstring.
        event_queryset = Event.objects.filter(club=club).filter(Q(tournament__isnull=True) | Q(pk=self.instance.event_id))
        self.fields["event"].queryset = event_queryset
        self.fields["event"].required = False
        self.fields["event"].empty_label = _("— none —")

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
    field. ``tournament`` comes from the view, never the form."""

    availability_date = forms.DateField(label=_("Date"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    availability_start = forms.TimeField(label=_("Start time"), required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    availability_end = forms.TimeField(label=_("End time"), required=False, widget=forms.TimeInput(attrs={"type": "time"}))

    class Meta:
        model = TournamentField
        fields = ["name", "location"]

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = Location.objects.filter(club=club)
        self.fields["location"].required = False
        self.fields["location"].empty_label = _("— none —")

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


class TournamentStageForm(forms.ModelForm):
    """Create/edit one stage. ``config``/``standings_config`` are free-form
    JSON on the model (format-specific parameters, points/tie-break rules --
    see ``TournamentStage``'s own docstring); for this first pass they're a
    plain Textarea with a basic "is this valid JSON object" check rather than
    a dynamic per-format field UI, matching the model's own "enum + flexible
    JSON" idiom. ``tournament`` comes from the view, never the form."""

    class Meta:
        model = TournamentStage
        fields = ["name", "sequence", "format", "config", "standings_config"]
        widgets = {
            "config": forms.Textarea(attrs={"rows": 4}),
            "standings_config": forms.Textarea(attrs={"rows": 4}),
        }

    def _clean_json_object(self, name):
        # forms.JSONField (the model field's auto-generated form field) has
        # already parsed the raw text and rejected malformed JSON by the time
        # this runs -- this only adds the extra "must be an object" check,
        # since config/standings_config are both documented as JSON *objects*
        # (a bare list or string would silently break the scheduler).
        value = self.cleaned_data.get(name)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValidationError(_('Enter a JSON object, e.g. {"key": "value"}.'))
        return value

    def clean_config(self):
        return self._clean_json_object("config")

    def clean_standings_config(self):
        return self._clean_json_object("standings_config")


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


class DressingRoomForm(forms.ModelForm):
    """Create/edit one of the club's own dressing rooms -- a club-wide,
    persists-across-tournaments record (see ``DressingRoom``'s own
    docstring), so this is reached from Settings, not from any one
    tournament's own pages. ``club`` comes from the view, never the form."""

    class Meta:
        model = DressingRoom
        fields = ["name", "location", "capacity", "notes"]
        widgets = {
            "capacity": forms.NumberInput(attrs={"min": "0"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = Location.objects.filter(club=club)
        self.fields["location"].required = False
        self.fields["location"].empty_label = _("— none —")


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
