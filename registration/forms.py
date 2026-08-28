from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from members.models import Member
from shop.models import ProductVariant
from teams.models import Team

from .models import RegistrationDetails
from .services.pricing import available_registration_products
from .services.submission import EntryInput


class RegistrationStatusDocumentForm(forms.Form):
    """One upload on the status page (registration.views.RegistrationStatusView)
    against one open, document-requiring OnboardingRequirement -- same bare
    "just the file" shape as management.forms.RequirementCompletionForm's own
    document field, just required here since the whole POST is the upload."""

    document = forms.FileField(label=_("Document"), required=True)


class RegistrationContactForm(forms.Form):
    """The "about you" half of the public registration page -- same
    ``lock_*_fields`` idea as members.forms.ParentClaimForm: a signed-in
    submitter (mobile re-registration, or a returning public visitor who
    happens to be logged in) is shown read-only text instead of editable
    inputs, so a mismatched typed email can never fork off a second
    User/Member -- the view fills these in from the authenticated Member
    afterwards, never from anything the client submits."""

    contact_first_name = forms.CharField(label=_("Your first name"), max_length=150)
    contact_last_name = forms.CharField(label=_("Your last name"), max_length=150)
    contact_email = forms.EmailField(label=_("Your email address"), help_text=_("We'll use this to keep you posted on this registration."))
    contact_phone = forms.CharField(label=_("Your phone number"), max_length=50, required=False)

    def __init__(self, *args, lock_contact_fields=False, **kwargs):
        super().__init__(*args, **kwargs)
        if lock_contact_fields:
            for name in ("contact_first_name", "contact_last_name", "contact_email"):
                del self.fields[name]


class RegistrationEntryRowForm(forms.Form):
    """One row of the registration form -- a person being registered.
    Entirely optional at the field level, same "a row left blank is simply
    skipped" idiom as management.forms.FieldRowForm/ProductVariantRowForm;
    the formset itself (BaseRegistrationEntryFormSet) requires at least one
    non-blank row.

    ``existing_member`` is a hidden field, unused by the public registration
    page (nothing there is a known Member yet) -- mobile.views.
    ReRegisterView sets it (scoped to the signed-in member's own
    managed_people via the ``people`` kwarg) so re-registering an existing
    child reuses their Member row instead of creating a duplicate. Its
    presence alone counts as "a person" for has_a_person()/clean(), same as
    a typed first/last name.

    No ``requested_position`` field -- a role isn't something registration
    asks for directly any more; it's implied by which product_variant was
    chosen (see the entry_kind/category check in clean() below). Staff
    still picks an actual Position by hand when placing a volunteer (the
    Volunteer list's own placement form), same as it always has -- this
    only drops the *request*, RegistrationDetails.requested_position
    itself is untouched."""

    first_name = forms.CharField(label=_("First name"), max_length=150, required=False)
    last_name = forms.CharField(label=_("Last name"), max_length=150, required=False)
    date_of_birth = forms.DateField(label=_("Date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    is_contact = forms.BooleanField(label=_("This is me"), required=False)
    existing_member = forms.ModelChoiceField(queryset=Member.objects.none(), required=False, widget=forms.HiddenInput())
    entry_kind = forms.ChoiceField(label=_("Registering as"), choices=RegistrationDetails.EntryKind.choices, required=False, initial=RegistrationDetails.EntryKind.PLAYER)
    product_variant = forms.ModelChoiceField(label=_("Registering for"), queryset=ProductVariant.objects.none(), required=False, widget=forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type to search...")}))
    requested_team = forms.ModelChoiceField(label=_("Team (optional)"), queryset=Team.objects.none(), required=False, widget=forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a team name...")}))

    def __init__(self, *args, club=None, people=None, season=None, **kwargs):
        super().__init__(*args, **kwargs)
        if club is not None:
            self.fields["requested_team"].queryset = Team.objects.filter(club=club).order_by("name")
            # Scoped to the one season this whole registration targets (once
            # known -- see registration.services.pricing.resolve_chosen_season)
            # so a batch can't be built mixing products from two different,
            # simultaneously-open registration windows.
            variants = ProductVariant.objects.filter(product__in=available_registration_products(club, season=season), is_active=True).select_related("product__category").order_by("product__name", "name")
            self.fields["product_variant"].queryset = variants
            self.fields["product_variant"].label_from_instance = lambda variant: f"{variant.product.name} — {variant.name} (€{variant.effective_price})"
        if people is not None:
            self.fields["existing_member"].queryset = Member.objects.filter(pk__in=[person.pk for person in people])

    def has_a_person(self):
        data = self.cleaned_data
        return bool(data.get("first_name") or data.get("last_name") or data.get("existing_member"))

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("first_name") or cleaned.get("last_name") or cleaned.get("existing_member")):
            return cleaned  # a genuinely blank row -- the formset skips it entirely

        if not cleaned.get("existing_member") and not cleaned.get("last_name"):
            self.add_error("last_name", _("Enter a last name."))

        variant = cleaned.get("product_variant")
        if not variant:
            self.add_error("product_variant", _("Choose what this person is registering for."))
        else:
            # A product tagged with one of the two system categories (Player/
            # Volunteer, ProductCategory.registration_kind) is only offered to
            # a matching entry_kind -- an ordinary/uncategorised product stays
            # available to either.
            registration_kind = getattr(variant.product.category, "registration_kind", "")
            if registration_kind and registration_kind != cleaned.get("entry_kind"):
                expected = dict(RegistrationDetails.EntryKind.choices).get(registration_kind, registration_kind)
                self.add_error("product_variant", _("This option is for %(kind)s registrations.") % {"kind": expected})

        return cleaned


class BaseRegistrationEntryFormSet(forms.BaseFormSet):
    def __init__(self, *args, club=None, people=None, season=None, **kwargs):
        self.club = club
        self.people = people
        self.season = season
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["club"] = self.club
        kwargs["people"] = self.people
        kwargs["season"] = self.season
        return kwargs

    def non_blank_forms(self):
        return [form for form in self.forms if form.cleaned_data and form.has_a_person()]

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not self.non_blank_forms():
            raise ValidationError(_("Register at least one person."))
        if sum(1 for form in self.non_blank_forms() if form.cleaned_data.get("is_contact")) > 1:
            raise ValidationError(_("Only one entry can be “this is me”."))


RegistrationEntryFormSet = forms.formset_factory(RegistrationEntryRowForm, formset=BaseRegistrationEntryFormSet, extra=1)


def entries_from_formset(entry_formset):
    """The shared bridge from a validated RegistrationEntryFormSet to the
    EntryInput list registration.services.submission.submit_registration
    expects -- used by both the public registration page and mobile's
    re-registration screen so the two can never map form fields to service
    kwargs differently."""
    entries = []
    for row in entry_formset.non_blank_forms():
        data = row.cleaned_data
        existing_member = data.get("existing_member")
        # A mobile row for an already-known person never has its own visible
        # first_name/last_name inputs (existing_member is enough to identify
        # them) -- fall back to the Member's own name so the price summary
        # doesn't show a blank line for it.
        first_name = data.get("first_name") or (existing_member.first_name if existing_member else "")
        last_name = data.get("last_name") or (existing_member.last_name if existing_member else "")
        entries.append(
            EntryInput(
                first_name=first_name,
                last_name=last_name,
                date_of_birth=data.get("date_of_birth"),
                entry_kind=data.get("entry_kind") or RegistrationDetails.EntryKind.PLAYER,
                requested_team=data.get("requested_team"),
                product_variant=data.get("product_variant"),
                existing_member=data.get("existing_member"),
                is_contact=bool(data.get("is_contact")),
            )
        )
    return entries
