from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from shop.models import ProductVariant
from teams.models import Position, Team

from .models import RegistrationDetails
from .services.pricing import available_registration_products


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
    """One row of the public registration form -- a person being
    registered. Entirely optional at the field level, same "a row left
    blank is simply skipped" idiom as management.forms.FieldRowForm/
    ProductVariantRowForm; the formset itself (BaseRegistrationEntryFormSet)
    requires at least one non-blank row."""

    first_name = forms.CharField(label=_("First name"), max_length=150, required=False)
    last_name = forms.CharField(label=_("Last name"), max_length=150, required=False)
    date_of_birth = forms.DateField(label=_("Date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    is_contact = forms.BooleanField(label=_("This is me"), required=False)
    entry_kind = forms.ChoiceField(label=_("Registering as"), choices=RegistrationDetails.EntryKind.choices, required=False, initial=RegistrationDetails.EntryKind.PLAYER)
    requested_team = forms.ModelChoiceField(label=_("Team (optional)"), queryset=Team.objects.none(), required=False, widget=forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a team name...")}))
    requested_position = forms.ModelChoiceField(label=_("Role (optional, volunteers only)"), queryset=Position.objects.none(), required=False, widget=forms.Select(attrs={"data-searchable": "true"}))
    product_variant = forms.ModelChoiceField(label=_("Registering for"), queryset=ProductVariant.objects.none(), required=False, widget=forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type to search...")}))

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        if club is not None:
            self.fields["requested_team"].queryset = Team.objects.filter(club=club).order_by("name")
            self.fields["requested_position"].queryset = Position.objects.filter(club=club, staff_position=True).order_by("name")
            variants = ProductVariant.objects.filter(product__in=available_registration_products(club), is_active=True).select_related("product").order_by("product__name", "name")
            self.fields["product_variant"].queryset = variants
            self.fields["product_variant"].label_from_instance = lambda variant: f"{variant.product.name} — {variant.name} (€{variant.effective_price})"

    def has_a_person(self):
        return bool(self.cleaned_data.get("first_name") or self.cleaned_data.get("last_name"))

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("first_name") or cleaned.get("last_name")):
            return cleaned  # a genuinely blank row -- the formset skips it entirely

        if not cleaned.get("last_name"):
            self.add_error("last_name", _("Enter a last name."))
        if not cleaned.get("product_variant"):
            self.add_error("product_variant", _("Choose what this person is registering for."))
        return cleaned


class BaseRegistrationEntryFormSet(forms.BaseFormSet):
    def __init__(self, *args, club=None, **kwargs):
        self.club = club
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["club"] = self.club
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
