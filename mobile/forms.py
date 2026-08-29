from django import forms
from django.utils.translation import gettext_lazy as _

from members.models import Member
from teams.models import Position, TeamMembership
from teams.services.numbers import is_number_available

#: Shared by every text-ish field below -- mobile has no equivalent of
#: management/controlpanel's templatetags/field.html (which builds widget
#: classes at render time for their daisyUI-shaped inputs), so this app's one
#: form so far just bakes its own classes straight into the widget.
_INPUT_CLASSES = "h-11 w-full rounded-lg border border-stroke bg-paper px-3 text-[15px] text-ink placeholder:text-dim focus:border-ink focus:outline-none"

#: Same look as _INPUT_CLASSES, sized for a multi-line body instead of a
#: single-line value -- a fixed height (not h-11) plus vertical padding a
#: fixed-height input doesn't need, and resize-y so a longer post isn't
#: stuck scrolling inside a short box.
_TEXTAREA_CLASSES = "h-40 w-full resize-y rounded-lg border border-stroke bg-paper px-3 py-2.5 text-[15px] text-ink placeholder:text-dim focus:border-ink focus:outline-none"


class MemberProfileForm(forms.ModelForm):
    """M6 -- "Edit personal info" (design_handoff_rosterchief_platform/README.md).

    Covers exactly the fields ``members.models.Member`` actually has. The
    design mock also shows a "National register no.", an "Address", an
    "Allergies / notes" field and two "Consent" toggles -- none of those have
    a backing field on ``Member`` (see EditProfileView's own docstring), so
    they're simply not part of this form rather than being invented here.

    Same field list and date widget as ``management.forms.MemberForm`` (the
    staff-side equivalent editing the same model) -- diverging widget
    conventions across the platform for identical fields would be its own bug.
    """

    class Meta:
        model = Member
        fields = ["first_name", "last_name", "date_of_birth", "email", "phone", "emergency_phone"]
        # Same date widget as management.forms.MemberForm. phone/emergency_phone
        # deliberately keep django-phonenumber-field's own RegionalPhoneNumberWidget
        # (national-format display, region-aware parsing) rather than being
        # swapped for a plain TextInput here -- __init__ below only adds a CSS
        # class to whatever widget each field already has, never replaces it.
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = _INPUT_CLASSES


class CoachRosterEditForm(forms.ModelForm):
    """Coach-mode roster row edit -- mobile/coach/roster_member.html's player
    detail sheet. Same fields management.forms.TeamMembershipForm edits,
    minus ``member`` (fixed by the URL here, never reassigned from this
    screen) -- this used to be a desktop-only action; see CoachSquadView's
    own docstring for why that changed."""

    class Meta:
        model = TeamMembership
        fields = ["position", "jersey_number", "is_captain", "is_alternate_captain"]

    def __init__(self, *args, club=None, team=None, season=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.season = season
        self.fields["position"].queryset = Position.objects.filter(club=club, staff_position=False)
        self.fields["position"].required = True
        self.fields["position"].widget.attrs["class"] = _INPUT_CLASSES
        self.fields["jersey_number"].widget.attrs["class"] = _INPUT_CLASSES
        self.fields["is_captain"].widget.attrs["class"] = "h-5 w-5 shrink-0 accent-ink"
        self.fields["is_alternate_captain"].widget.attrs["class"] = "h-5 w-5 shrink-0 accent-ink"

    def clean(self):
        # Same jersey-clash check as TeamMembershipForm.clean -- team/season aren't
        # form fields, so Django's automatic validate_unique() can't catch this itself.
        cleaned = super().clean()
        jersey_number = cleaned.get("jersey_number")
        if jersey_number is not None and self.team is not None and self.season is not None:
            if self.team.pool_id is not None:
                if not is_number_available(self.team.pool, self.season, jersey_number, for_member=self.instance.member):
                    self.add_error("jersey_number", _("This number is already taken in this team's number pool this season."))
            elif TeamMembership.objects.filter(team=self.team, season=self.season, jersey_number=jersey_number).exclude(pk=self.instance.pk).exists():
                self.add_error("jersey_number", _("Another player on this team already has this jersey number this season."))
        return cleaned


#: By widget type, not field name -- the fields on a formbuilder.services.
#: form_factory.build_form result aren't known ahead of time (they're built
#: from a club-defined Form's Fields at request time), so the by-name idiom
#: every other form on this page uses doesn't apply. First matching type
#: wins; anything left over (CharField/DecimalField/EmailField/DateField)
#: falls through to the plain _INPUT_CLASSES default.
_DYNAMIC_WIDGET_CLASSES = (
    (forms.CheckboxInput, "h-5 w-5 shrink-0 accent-ink"),
    (forms.Textarea, _TEXTAREA_CLASSES),
    (forms.SelectMultiple, _INPUT_CLASSES),
    (forms.Select, _INPUT_CLASSES),
    (forms.ClearableFileInput, "block w-full text-sm text-ink file:mr-3 file:rounded-lg file:border-0 file:bg-ink file:px-3 file:py-2 file:text-sm file:font-semibold file:text-paper"),
)


def style_dynamic_form(bound_form):
    """Applies this app's own input styling to a formbuilder-built dynamic
    Form's widgets, by widget type rather than by field name -- see
    mobile.views.FormFillView. Mutates and returns ``bound_form``."""
    for field in bound_form.fields.values():
        for widget_class, css in _DYNAMIC_WIDGET_CLASSES:
            if isinstance(field.widget, widget_class):
                field.widget.attrs["class"] = css
                break
        else:
            field.widget.attrs["class"] = _INPUT_CLASSES
    return bound_form
