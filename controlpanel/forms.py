from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _
from waffle import get_waffle_flag_model

from billing.models import DuePayment, Plan, PlanPrice, Subscription
from club.models import Club
from events.models import Location

from .services.admins import find_member_by_email


class ClubForm(forms.ModelForm):
    class Meta:
        model = Club
        fields = ["name", "slug", "sport_type", "logo", "primary_color", "secondary_color", "season_start", "season_duration_months"]
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


class PlanForm(forms.ModelForm):
    """Field order is chosen for the two-column modal (see _form_fields.html): description
    spans both columns, so pairing name with duration and the two day-counts with each other
    fills every row instead of leaving half of one empty.

        name              | duration_months
        description ....................... (spans both)
        renewal_lead_days | grace_days
        is_trial          | is_active
    """

    class Meta:
        model = Plan
        fields = ["name", "duration_months", "description", "renewal_lead_days", "grace_days", "is_trial", "is_active"]


class PlanPriceForm(forms.ModelForm):
    class Meta:
        model = PlanPrice
        fields = ["active_from", "amount"]
        widgets = {"active_from": forms.DateInput(attrs={"type": "date"})}
        help_texts = {
            "active_from": _(
                "Periods opening on or after this date are billed at this amount. Existing periods keep the amount "
                "they were billed at — including any already issued during a plan's renewal lead window, so enter a "
                "price change before that window opens."
            )
        }


class SubscriptionForm(forms.ModelForm):
    """Put a club on a plan. The first period opens when the subscription is created."""

    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label=_("First period starts"), help_text=_("Left blank, the period starts today."))

    class Meta:
        model = Subscription
        fields = ["plan", "auto_renew", "auto_archive", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # An inactive plan still bills its existing subscriptions, but must not be picked up
        # by a new one — which is the whole point of retiring a plan. Trial plans are excluded
        # too: they are reached through the trial form, which converts them properly.
        self.fields["plan"].queryset = Plan.objects.filter(is_active=True, is_trial=False)


class TrialForm(forms.Form):
    """Put a club with no subscription yet on a trial that switches itself to
    ``post_trial_plan`` automatically once it ends -- see billing.services.dues.start_trial.

    There is no length field: a trial's length is its plan's own ``duration_months``, so a
    1-month and a 3-month trial are two plans rather than one plan plus a number typed here.
    """

    trial_plan = forms.ModelChoiceField(queryset=Plan.objects.none(), label=_("Trial plan"), help_text=_("What this club is billed on during the trial. Its length is the plan's own duration."))
    post_trial_plan = forms.ModelChoiceField(queryset=Plan.objects.none(), label=_("Then switch to"), help_text=_("The plan it lands on automatically once the trial ends."))
    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label=_("Trial starts"), help_text=_("Left blank, the trial starts today."))
    # Same two switches SubscriptionForm offers. Without them here a trial could only be
    # started on the defaults, and the only way to change them afterwards is the "Change
    # plan" modal -- which ends the trial as a side effect.
    auto_renew = forms.BooleanField(required=False, initial=True, label=_("Auto renew"), help_text=_("Issue the next period automatically before this one ends."))
    auto_archive = forms.BooleanField(required=False, initial=True, label=_("Auto archive"), help_text=_("Archive this club when a period goes unpaid past its grace period."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Same reasoning as SubscriptionForm: a retired plan keeps billing whoever is
        # already on it, but must not be offered for a new trial or a new plan either.
        self.fields["trial_plan"].queryset = Plan.objects.filter(is_active=True, is_trial=True)
        self.fields["post_trial_plan"].queryset = Plan.objects.filter(is_active=True, is_trial=False)


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
