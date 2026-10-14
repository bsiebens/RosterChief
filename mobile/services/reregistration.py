"""Re-registering the account's managed people for a season -- the steps behind the
member web app's Register screen (mobile.views.ReRegisterView) and the app API's
/registration endpoints. Both run the same RegistrationEntryFormSet validation, the same
pricing and the same registration.services.submit_registration as the public page.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from members.models import Member
from registration.forms import RegistrationEntryFormSet, RegistrationEntryRowForm, entries_from_formset
from registration.models import RegistrationDetails
from registration.services import price_entries, resolve_registration_season, submit_registration, team_number_pools
from registration.services.pricing import jersey_choices_for_entry
from registration.services.submission import EntryInput
from shop.models import ProductVariant
from teams.models import Team
from teams.services.numbers import member_current_number

#: The formset prefix both the web form and the API's generated data use.
PREFIX = "entries"


def member_current_numbers(club, people, season) -> dict:
    """``{str(member_id): {str(team_id): number}}``: each managed person's current number in
    every team with a number pool, so "keep #N" can be offered (and validates)."""
    pools_by_team = {team.pk: team.pool for team in Team.objects.filter(club=club, pool__isnull=False).select_related("pool")}
    result = {}
    for person in people:
        person_numbers = {}
        for team_id, pool in pools_by_team.items():
            current = member_current_number(person, pool, season)
            if current is not None:
                person_numbers[str(team_id)] = current
        if person_numbers:
            result[str(person.pk)] = person_numbers
    return result


def entry_formset(club, people, season, data=None, initial=None):
    """The entry formset scoped to ``people`` and ``season``. ``enforce_single_contact`` is
    off: here ``is_contact`` is each person's "Include this person" toggle, and including
    yourself and a child together is the normal case."""
    kwargs = {
        "club": club,
        "people": people,
        "season": season,
        "team_number_pools": team_number_pools(club, season),
        "member_current_numbers": member_current_numbers(club, people, season),
        "prefix": PREFIX,
        "enforce_single_contact": False,
    }
    if data is None and initial is not None:
        kwargs["initial"] = initial
    return RegistrationEntryFormSet(data, **kwargs)


def price_formset(formset) -> tuple[list, list]:
    """``(entries, priced)`` for a valid formset. Raises PricingError when the chosen
    products don't share one season."""
    entries = entries_from_formset(formset)
    resolve_registration_season([entry.product_variant for entry in entries])
    return entries, price_entries([entry.product_variant for entry in entries])


def submit_reregistration(club, user, me, entries):
    """Submit as the signed-in account (the contact is its own person). Raises
    RegistrationError."""
    contact_member = me or (entries[0].existing_member if entries and entries[0].existing_member else None)
    return submit_registration(
        club,
        submitted_by_user=user,
        entries=entries,
        contact_first_name=contact_member.first_name if contact_member else "",
        contact_last_name=contact_member.last_name if contact_member else "",
        contact_email=contact_member.contact_email if contact_member else "",
        contact_phone="",
    )


# ---------------------------------------------------------------------------
# The "list of registrations + editor sheet" flow (mirrors the iOS Register screen).
#
# The draft lives in the page itself: every entry is a set of hidden ``entries-N-*`` inputs, i.e.
# exactly the RegistrationEntryFormSet's own POST shape, so price/send run the unchanged formset
# validation, pricing and submit_registration. These helpers only read that state back out of a
# POST, edit the list, and re-index it.
# ---------------------------------------------------------------------------

#: The ``action`` verbs that only edit the draft list (``verb`` or ``verb:index``).
EDITOR_ACTIONS = ("add", "change", "remove", "refresh", "save", "cancel")

#: The entry fields carried per entry (the RegistrationEntryRowForm's own).
ENTRY_FIELDS = ("existing_member", "first_name", "last_name", "date_of_birth", "is_contact", "entry_kind", "product_variant", "requested_team", "requested_jersey_number")

#: A row naming nobody and picking nothing is the formset's "blank row" -- dropped from the list.
_INTENT_FIELDS = ("first_name", "last_name", "is_contact", "product_variant", "requested_team")


def entries_state(data, prefix=PREFIX) -> list[dict]:
    """The non-blank entries of a posted formset as ``[{field: raw string}, ...]``, in order."""
    try:
        total = min(int(data.get(f"{prefix}-TOTAL_FORMS", 0)), 1000)
    except TypeError, ValueError:
        total = 0
    entries = []
    for index in range(total):
        entry = {field: (data.get(f"{prefix}-{index}-{field}") or "").strip() for field in ENTRY_FIELDS}
        if any(entry[field] for field in _INTENT_FIELDS):
            entries.append(entry)
    return entries


def state_to_data(entries, season=None, prefix=PREFIX) -> dict:
    """A formset-shaped POST dict for ``entries`` (re-indexed from 0)."""
    data = {f"{prefix}-TOTAL_FORMS": str(len(entries)), f"{prefix}-INITIAL_FORMS": "0", f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000"}
    if season is not None:
        data["season"] = str(season.pk)
    for index, entry in enumerate(entries):
        for field, value in entry.items():
            if value:
                data[f"{prefix}-{index}-{field}"] = value
    return data


class EntryEditorForm(RegistrationEntryRowForm):
    """One registration in the editor sheet: who (a managed person, or "Someone new" with name +
    date of birth), the role, the option, the team preference and the jersey number. The same
    validation as the formset's own row (it *is* that row), with the person as a visible select,
    the options narrowed to the chosen role and the jersey number narrowed to the chosen team."""

    def __init__(self, *args, club=None, people=None, season=None, team_number_pools=None, member_current_numbers=None, **kwargs):
        super().__init__(*args, club=club, people=people, season=season, team_number_pools=team_number_pools, member_current_numbers=member_current_numbers, **kwargs)
        from mobile.forms import style_dynamic_form

        self.people = list(people or [])
        self.current_numbers = member_current_numbers or {}
        self.fields["existing_member"] = forms.ModelChoiceField(label=_("Person"), queryset=Member.objects.filter(pk__in=[person.pk for person in self.people]), required=False, empty_label=_("Someone new"))
        self.fields["entry_kind"].label = _("Role")
        self.fields["entry_kind"].initial = RegistrationDetails.EntryKind.PLAYER
        self.fields["entry_kind"].required = True
        self.fields["product_variant"].label = _("Option")
        self.fields["requested_team"].label = _("Team preference")
        self.fields["requested_team"].empty_label = _("No preference")
        self.fields["requested_team"].help_text = _("The club makes the final choice.")
        self.fields["product_variant"].empty_label = _("Choose…")
        for name in ("product_variant", "requested_team", "requested_jersey_number"):
            self.fields[name].widget.attrs.pop("data-searchable", None)
            self.fields[name].widget.attrs.pop("data-search-placeholder", None)
        # Narrow the options to the chosen role (an uncategorised option stays available either way).
        kind = self._raw("entry_kind") or RegistrationDetails.EntryKind.PLAYER
        variants = self.fields["product_variant"].queryset
        kept = [variant.pk for variant in variants if not getattr(variant.product.category, "registration_kind", "") or variant.product.category.registration_kind == kind]
        self.fields["product_variant"].queryset = variants.filter(pk__in=kept)
        self.fields["requested_jersey_number"].choices = self.jersey_options()
        # A change of person, role or team redraws the editor (htmx) so the options and numbers follow it.
        refresh = {"hx-post": reverse("mobile:reregister"), "hx-trigger": "change", "hx-vals": '{"action": "refresh"}', "hx-include": "#registration-editor-form", "hx-target": "#registration-editor-body", "hx-swap": "innerHTML"}
        for name in ("existing_member", "entry_kind", "requested_team"):
            self.fields[name].widget.attrs.update(refresh)
        self.fields["date_of_birth"].widget.format = "%Y-%m-%d"
        style_dynamic_form(self)

    def _raw(self, name):
        if self.is_bound:
            return (self.data.get(self.add_prefix(name)) or "").strip()
        value = (self.initial or {}).get(name, "")
        return str(value) if value not in (None, "") else ""

    def jersey_options(self):
        """``[("", "No number"), ("7", "Keep #7"), ("8", "#8"), ...]`` for the chosen team, or just the
        blank option when the team has no number pool (or the role isn't a player)."""
        team = self.fields["requested_team"].queryset.filter(pk=self._raw("requested_team")).first() if _is_pk(self._raw("requested_team")) else None
        member = next((person for person in self.people if str(person.pk) == self._raw("existing_member")), None)
        entry = EntryInput(entry_kind=self._raw("entry_kind") or RegistrationDetails.EntryKind.PLAYER, requested_team=team, existing_member=member)
        numbers = jersey_choices_for_entry(entry, self.team_number_pools, self.current_numbers)
        self.has_jersey_choice = bool(numbers)
        if not numbers:
            return [("", _("No number"))]
        current = self.current_numbers.get(str(member.pk), {}).get(str(team.pk)) if member is not None and team is not None else None
        return [("", _("No number"))] + [(str(number), _("Keep #%(number)s") % {"number": number} if current is not None and current == number else f"#{number}") for number in numbers]

    def has_a_person(self):
        return True  # the editor always produces an entry -- the checks below say what is missing

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("existing_member"):
            if not cleaned.get("first_name"):
                self.add_error("first_name", _("Enter a first name."))
            if not cleaned.get("last_name") and "last_name" not in self.errors:
                self.add_error("last_name", _("Enter a last name."))
        return cleaned

    def to_entry(self) -> dict:
        """The cleaned values as a state entry (raw strings, like entries_state)."""
        data = self.cleaned_data
        member = data.get("existing_member")
        return {
            "existing_member": str(member.pk) if member else "",
            "first_name": "" if member else data.get("first_name", ""),
            "last_name": "" if member else data.get("last_name", ""),
            "date_of_birth": "" if member or not data.get("date_of_birth") else data["date_of_birth"].isoformat(),
            # "Include this person" for a known person -- is_contact is only a presence marker there (a named
            # existing_member decides whom it registers, see registration.services.submission._is_self_entry).
            "is_contact": "on" if member else "",
            "entry_kind": data.get("entry_kind") or RegistrationDetails.EntryKind.PLAYER,
            "product_variant": str(data["product_variant"].pk) if data.get("product_variant") else "",
            "requested_team": str(data["requested_team"].pk) if data.get("requested_team") else "",
            "requested_jersey_number": data.get("requested_jersey_number") or "",
        }


def _is_pk(value) -> bool:
    return bool(value) and value.replace("-", "").isalnum()


def editor_form(club, people, season, *, data=None, entry=None, prefix="editor"):
    """The editor sheet's form: unbound and blank (``entry`` None), prefilled from ``entry`` (a state entry), or
    bound to posted ``data``."""
    kwargs = {"club": club, "people": people, "season": season, "team_number_pools": team_number_pools(club, season), "member_current_numbers": member_current_numbers(club, people, season), "prefix": prefix}
    if data is not None:
        return EntryEditorForm(data, **kwargs)
    initial = {}
    if entry:
        initial = {field: value for field, value in entry.items() if value and field != "is_contact"}
        if initial.get("existing_member"):
            initial["existing_member"] = initial["existing_member"]
        if initial.get("date_of_birth"):
            from datetime import date

            initial["date_of_birth"] = date.fromisoformat(initial["date_of_birth"])
    return EntryEditorForm(initial=initial, **kwargs)


def entry_cards(entries, formset, people, club) -> list[dict]:
    """The list rows: ``[{index, entry, name, role, option, team, jersey, errors}]``. ``formset`` (bound, or None)
    supplies each row's resolved option/team and, once validated, its error messages."""
    names = {str(person.pk): person.get_full_name() for person in people}
    role_labels = dict(RegistrationDetails.EntryKind.choices)
    # Display only, and scoped to this club (the formset validates the same values against the same scope).
    variants = ProductVariant.objects.filter(product__club=club).select_related("product")
    teams = Team.objects.filter(club=club)
    cards = []
    for index, entry in enumerate(entries):
        form = formset.forms[index] if formset is not None and index < len(formset.forms) else None
        variant = _lookup(variants, entry["product_variant"])
        team = _lookup(teams, entry["requested_team"])
        errors = []
        if form is not None and formset.is_bound:
            errors = [str(message) for messages in form.errors.values() for message in messages]
        cards.append(
            {
                "index": index,
                "entry": entry,
                "name": names.get(entry["existing_member"]) or f"{entry['first_name']} {entry['last_name']}".strip(),
                "is_new": not entry["existing_member"],
                "role": role_labels.get(entry["entry_kind"] or RegistrationDetails.EntryKind.PLAYER, ""),
                "option": f"{variant.product.name} · {variant.name}" if variant is not None else "",
                "team": team.name if team is not None else "",
                "jersey": entry["requested_jersey_number"],
                "errors": errors,
            }
        )
    return cards


def _lookup(queryset, value):
    if not _is_pk(value):
        return None
    try:
        return queryset.filter(pk=value).first()
    except ValueError, ValidationError:
        return None
