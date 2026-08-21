from django import forms

from members.models import Member

#: Shared by every text-ish field below -- mobile has no equivalent of
#: management/controlpanel's templatetags/field.html (which builds widget
#: classes at render time for their daisyUI-shaped inputs), so this app's one
#: form so far just bakes its own classes straight into the widget.
_INPUT_CLASSES = "h-11 w-full rounded-lg border border-stroke bg-paper px-3 text-[15px] text-ink placeholder:text-dim focus:border-ink focus:outline-none"


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
