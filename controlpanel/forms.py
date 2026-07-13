from django import forms
from django.utils.translation import gettext_lazy as _
from waffle import get_waffle_flag_model

from club.models import Club

from .services.admins import find_member_by_email


class ClubForm(forms.ModelForm):
    class Meta:
        model = Club
        fields = ["name", "slug", "logo", "primary_color"]
        help_texts = {"slug": _("Drives the club's subdomain. Left blank, it is derived from the name.")}
        # Deliberately a text input, not <input type="color">: a colour picker cannot
        # express "no colour" -- it would submit #000000 for every club that never
        # touched it, and every club would silently get a black theme.
        widgets = {"primary_color": forms.TextInput(attrs={"placeholder": "#1e40af"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False


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
