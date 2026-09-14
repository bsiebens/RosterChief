from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import TournamentRegistration


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
