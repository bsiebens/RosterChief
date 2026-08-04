from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _
from waffle import get_waffle_flag_model

from billing.models import DuePayment, Subscription, Tier, TierPrice
from club.models import Club
from events.models import Location

from .services.admins import find_member_by_email


class ClubForm(forms.ModelForm):
    class Meta:
        model = Club
        fields = ["name", "slug", "logo", "primary_color", "secondary_color", "season_start", "season_duration_months"]
        help_texts = {"slug": _("Drives the club's subdomain. Left blank, it is derived from the name.")}
        # Deliberately a text input, not <input type="color">: a colour picker cannot
        # express "no colour" -- it would submit #000000 for every club that never
        # touched it, and every club would silently get a black theme.
        widgets = {
            "primary_color": forms.TextInput(attrs={"placeholder": "#1e40af"}),
            "secondary_color": forms.TextInput(attrs={"placeholder": "#be185d"}),
            "logo": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/gif,image/webp,image/svg+xml"}),
            "season_start": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False


class HomeLocationForm(forms.ModelForm):
    """Create or update the club's home ground -- this *is* an events.Location row
    (flagged ``is_home``), the same one that shows up under the club's own
    Teams > Locations page, so the two stay in sync by construction rather than
    needing anything to keep them that way."""

    class Meta:
        model = Location
        fields = ["name", "address", "city", "zip_code", "country"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["country"].widget.attrs.update({"data-searchable": "true", "data-search-placeholder": _("Type a country to search...")})


class ClubAdminForm(forms.Form):
    """Grant club-admin rights to an email address, creating the person if new."""

    email = forms.EmailField(label=_("Email address"), help_text=_("If this email has no account yet, one is created and they set a password via the reset link."))
    first_name = forms.CharField(label=_("First name"), required=False)
    last_name = forms.CharField(label=_("Last name"), required=False)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")

        # Only a brand-new person needs a name; an existing member already has one.
        if email and find_member_by_email(email) is None:
            for field in ("first_name", "last_name"):
                if not cleaned.get(field):
                    self.add_error(field, _("Required: this email has no account yet."))

        return cleaned


class PlatformAdminForm(forms.Form):
    """Grant platform access to an email address, creating the account if new."""

    email = forms.EmailField(label=_("Email address"), help_text=_("If this email has no account yet, one is created and they set a password via the reset link."))
    is_superuser = forms.BooleanField(label=_("Superuser"), required=False, help_text=_("Superusers can manage platform admins. Everyone granted access is staff."))


class FlagForm(forms.ModelForm):
    class Meta:
        model = get_waffle_flag_model()
        fields = ["name", "note", "everyone", "superusers", "staff", "percent"]
        help_texts = {
            "everyone": _("Yes = on for all clubs, No = off everywhere (overrides club targeting). Leave unknown to target clubs."),
        }


class TierForm(forms.ModelForm):
    class Meta:
        model = Tier
        fields = ["name", "description", "is_active"]


class TierPriceForm(forms.ModelForm):
    class Meta:
        model = TierPrice
        fields = ["active_from", "amount"]
        widgets = {"active_from": forms.DateInput(attrs={"type": "date"})}
        help_texts = {"active_from": _("Periods opening on or after this date are billed at this amount. Existing periods keep the amount they were billed at.")}


class SubscriptionForm(forms.ModelForm):
    """Put a club on a tier. The first period opens when the subscription is created."""

    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label=_("First period starts"), help_text=_("Left blank, the period starts today."))

    class Meta:
        model = Subscription
        fields = ["tier", "auto_renew", "auto_archive", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # An inactive tier still bills its existing subscriptions, but must not be picked up
        # by a new one — which is the whole point of retiring a tier.
        self.fields["tier"].queryset = Tier.objects.filter(is_active=True)


class DuePaymentForm(forms.Form):
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"), label=_("Amount"))
    method = forms.ChoiceField(choices=DuePayment.Method.choices, initial=DuePayment.Method.BANK_TRANSFER, label=_("Method"))
    reference = forms.CharField(required=False, label=_("Reference"), help_text=_("Bank reference, transaction id — whatever lets you find this again."))
    paid_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}), label=_("Received"), help_text=_("Left blank, now."))
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label=_("Note"))


class OpenPeriodForm(forms.Form):
    """Renew, or reactivate an archived club."""

    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label=_("Period starts"), help_text=_("Left blank, it continues from the end of the last period — so a lapsed year is still owed."))


class MaintenanceForm(forms.Form):
    """Closing the platform is a deliberate act, so it takes a sentence explaining itself —
    that message is the only thing a club will see."""

    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label=_("Message"),
        help_text=_("Shown to every club while the platform is closed. Left blank, they get a generic notice."),
    )
