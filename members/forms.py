from django import forms
from django.utils.translation import gettext_lazy as _


class ParentClaimForm(forms.Form):
    """The public "link me to my child" form -- see members.models.ParentClaim.

    Every child field is free text. There is deliberately no picker, no search
    and no autocomplete: this page is reachable without logging in, so anything
    that confirmed whether a given child exists would turn it into a way to
    enumerate the club's children. The submitter types what they know and an
    admin matches it against the real record.
    """

    parent_first_name = forms.CharField(label=_("Your first name"), max_length=150)
    parent_last_name = forms.CharField(label=_("Your last name"), max_length=150)
    parent_email = forms.EmailField(label=_("Your email address"), help_text=_("We'll use this to set up your login once the club has confirmed the link."))

    child_first_name = forms.CharField(label=_("Child's first name"), max_length=150)
    child_last_name = forms.CharField(label=_("Child's last name"), max_length=150)
    child_date_of_birth = forms.DateField(label=_("Child's date of birth"), widget=forms.DateInput(attrs={"type": "date"}))


class ClaimReviewForm(forms.Form):
    """An admin approving one claim: which child it actually refers to.

    The child is chosen from the shortlist rather than typed, and the shortlist
    is only ever children who have no parent on file -- approving a claim can
    never quietly re-parent a child who already has one.
    """

    # No hardcoded "class" here -- templatetags/field.html's own select branch
    # already builds the full class list (including the size modifier), and a
    # class baked into the widget attrs would render a second, conflicting
    # class="..." on the <select> alongside it rather than merging with it.
    child = forms.ModelChoiceField(queryset=None, label=_("Link to"), widget=forms.Select())

    def __init__(self, *args, candidates=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = candidates
        self.fields["child"].label_from_instance = lambda child: f"{child} ({child.date_of_birth:%d %b %Y})" if child.date_of_birth else str(child)
