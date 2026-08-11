from django import forms
from django.utils.translation import gettext_lazy as _

from members.models import Member


class ParentClaimForm(forms.Form):
    """The public "link me to my child" form -- see members.models.ParentClaim.

    Every child field is free text. There is deliberately no picker, no search
    and no autocomplete: this page is reachable without logging in, so anything
    that confirmed whether a given child exists would turn it into a way to
    enumerate the club's children. The submitter types what they know and an
    admin matches it against the real record.

    ``lock_parent_fields`` drops the "about you" fields entirely rather than
    just pre-filling them: a signed-in parent claiming a second child is shown
    read-only text instead (see members.views.ParentClaimView), because an
    editable-but-pre-filled input still lets a mismatched email slip through.
    The view fills parent_first_name/parent_last_name/parent_email in from the
    authenticated Member afterwards -- never from anything the client submits.
    """

    parent_first_name = forms.CharField(label=_("Your first name"), max_length=150)
    parent_last_name = forms.CharField(label=_("Your last name"), max_length=150)
    parent_email = forms.EmailField(label=_("Your email address"), help_text=_("We'll use this to set up your login once the club has confirmed the link."))

    child_first_name = forms.CharField(label=_("Child's first name"), max_length=150)
    child_last_name = forms.CharField(label=_("Child's last name"), max_length=150)
    child_date_of_birth = forms.DateField(label=_("Child's date of birth"), widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, lock_parent_fields=False, **kwargs):
        super().__init__(*args, **kwargs)
        if lock_parent_fields:
            for name in ("parent_first_name", "parent_last_name", "parent_email"):
                del self.fields[name]


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
    # data-searchable matches every other member-picker in the app (e.g.
    # TeamMembershipForm.member) -- useful once a club has many candidates.
    child = forms.ModelChoiceField(
        queryset=None,
        label=_("Link to"),
        widget=forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")}),
    )

    def __init__(self, *args, candidates=None, **kwargs):
        super().__init__(*args, **kwargs)
        if candidates is None:
            candidates = Member.objects.none()
        self.fields["child"].queryset = candidates
        self.fields["child"].label_from_instance = lambda child: f"{child} ({child.date_of_birth:%d %b %Y})" if child.date_of_birth else str(child)
        # Pre-select the top match rather than leaving the empty placeholder as
        # the genuinely-selected option -- see management.views.ParentClaimListView.
        top_match = candidates.first()
        if top_match is not None:
            self.fields["child"].initial = top_match.pk


class ClaimRejectForm(forms.Form):
    """The optional reason recorded when an admin rejects a claim -- see
    members.services.claims.reject_claim. Shown in a modal rather than an
    inline row input so a real reason isn't fighting for space next to the
    Approve/Reject buttons."""

    note = forms.CharField(label=_("Reason"), required=False, widget=forms.Textarea(attrs={"rows": 2}), help_text=_("Recorded in the club's own history -- not sent to the parent."))
