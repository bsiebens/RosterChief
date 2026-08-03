from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership, ClubRole, FeePayment
from members.models import Family, FamilyMembership, Member
from members.services.family import find_member_by_email
from news.models import News
from teams.models import Position, StaffAssignment, Team, TeamMembership

User = get_user_model()


class MemberForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = ["first_name", "last_name", "date_of_birth", "email", "phone", "emergency_phone"]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name", "short_name"]


class TeamMembershipForm(forms.ModelForm):
    """Add/edit one roster entry -- team and season come from the view (the URL
    already identifies both), never from the form itself."""

    class Meta:
        model = TeamMembership
        fields = ["member", "position", "jersey_number", "is_captain", "is_alternate_captain"]
        widgets = {"member": forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")})}

    def __init__(self, *args, club=None, team=None, season=None, **kwargs):
        super().__init__(*args, **kwargs)
        members = Member.objects.filter(member_of__club=club).distinct()
        if team is not None and season is not None:
            # Already on this team's roster this season -- offering them again
            # would just fail the unique_member_per_team_per_season constraint.
            taken = TeamMembership.objects.filter(team=team, season=season).exclude(pk=self.instance.pk).values_list("member_id", flat=True)
            members = members.exclude(pk__in=taken)
        self.fields["member"].queryset = members
        self.fields["position"].queryset = Position.objects.filter(club=club, staff_position=False)


class StaffAssignmentForm(forms.ModelForm):
    """Assign/edit one staff assignment -- team and season come from the view,
    same reasoning as TeamMembershipForm."""

    class Meta:
        model = StaffAssignment
        fields = ["member", "position"]
        widgets = {"member": forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")})}

    def __init__(self, *args, club=None, team=None, season=None, **kwargs):
        super().__init__(*args, **kwargs)
        members = Member.objects.filter(member_of__club=club).distinct()
        if team is not None and season is not None:
            taken = StaffAssignment.objects.filter(team=team, season=season).exclude(pk=self.instance.pk).values_list("member_id", flat=True)
            members = members.exclude(pk__in=taken)
        self.fields["member"].queryset = members
        self.fields["position"].queryset = Position.objects.filter(club=club, staff_position=True)


class PositionForm(forms.ModelForm):
    class Meta:
        model = Position
        fields = ["name", "short_name", "ordering", "staff_position", "management_position"]

    def clean(self):
        cleaned = super().clean()
        # Mirrors Position's management_position_implies_staff_position check
        # constraint -- caught here so it reads as a form error, not a 500.
        if cleaned.get("management_position") and not cleaned.get("staff_position"):
            self.add_error("management_position", _("A management position must also be a staff position."))
        return cleaned


class ClubRoleAssignForm(forms.ModelForm):
    """Grant a club-wide role to a member already affiliated with this club."""

    class Meta:
        model = ClubRole
        fields = ["member", "role"]
        widgets = {"member": forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")})}

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Never list members of other clubs -- this isn't a platform-wide picker.
        self.fields["member"].queryset = Member.objects.filter(member_of__club=club).distinct()


class FamilyCreateForm(forms.Form):
    """One new family in one go: a parent (who gets a login) and a child (who
    doesn't). See members.services.family.register_family."""

    parent_first_name = forms.CharField(label=_("Parent first name"))
    parent_last_name = forms.CharField(label=_("Parent last name"))
    parent_email = forms.EmailField(label=_("Parent email"), help_text=_("If this email has no account yet, one is created and they set a password via the reset link."))

    child_first_name = forms.CharField(label=_("Child first name"))
    child_last_name = forms.CharField(label=_("Child last name"))
    child_date_of_birth = forms.DateField(label=_("Child date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date"}))


class AddChildForm(forms.Form):
    """A family that needs one more child registered -- see
    members.services.family.add_child_to_family."""

    first_name = forms.CharField(label=_("First name"))
    last_name = forms.CharField(label=_("Last name"))
    date_of_birth = forms.DateField(label=_("Date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date"}))


class AddParentForm(forms.Form):
    """A family that needs one more parent/guardian registered -- see
    members.services.family.add_parent_to_family."""

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


class AttachToFamilyForm(forms.Form):
    """Link a standalone member into a family -- a new one, or an existing one they
    turn out to belong to. See members.services.family.attach_to_family."""

    role = forms.ChoiceField(label=_("Role"), choices=FamilyMembership.FamilyRole.choices)
    family = forms.ModelChoiceField(label=_("Family"), queryset=Family.objects.none(), required=False, empty_label=_("— start a new family —"))

    def __init__(self, *args, club=None, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Same scoping query as management.views.families_of_club -- inlined rather
        # than imported, since that function lives in views.py, which imports this
        # module (a module-level import back here would be circular).
        queryset = Family.objects.filter(memberships__member__member_of__club=club).distinct()
        if member is not None:
            # Already a member of it -- offering it again would be a no-op re-add.
            queryset = queryset.exclude(memberships__member=member)
        self.fields["family"].queryset = queryset


class MemberImportUploadForm(forms.Form):
    """The mass-upload entry point -- one .xlsx file, built from the downloadable
    template. See management.bulk_import.read_member_import_workbook."""

    file = forms.FileField(label=_("Excel file"), help_text=_("Use the downloaded template — one row per member."))


class GrantLoginForm(forms.Form):
    """A login-less family member (a child, typically) getting their own account --
    see members.services.family.grant_login. Pre-filled from the member's contact
    email where one is already on file; still editable, and required either way."""

    email = forms.EmailField(label=_("Email"), help_text=_("They'll set a password via the reset link the first time they sign in."))

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("This email is already in use."))
        return email


class ClubMembershipForm(forms.ModelForm):
    """This season's standing -- shown and edited right on the member's own page,
    since a Member has no club of its own without one. fee_amount is the only
    money field here -- amount_paid is exclusively written by club.services.fees,
    never hand-edited."""

    class Meta:
        model = ClubMembership
        fields = ["license", "status", "fee_status", "fee_amount"]


class NewsForm(forms.ModelForm):
    """Title/teams/visibility/body only -- status and published_at are never
    directly editable, only through the publish/unpublish actions."""

    class Meta:
        model = News
        fields = ["title", "teams", "visibility", "body"]
        widgets = {
            "teams": forms.SelectMultiple(attrs={"data-searchable": "true", "data-search-placeholder": _("Type to filter teams...")}),
            "body": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teams"].queryset = Team.objects.filter(club=club)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Django's own documented recipe for a multi-file upload field: the plain
    FileField only ever picks up one of several selected files, so clean() has
    to iterate the list itself instead."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"multiple": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(item, initial) for item in data]
        return [single_file_clean(data, initial)] if data else []


class NewsPhotoUploadForm(forms.Form):
    images = MultipleFileField(label=_("Photos"))


class NewsPublishForm(forms.Form):
    published_at = forms.DateTimeField(
        label=_("Publish date"),
        initial=timezone.now,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text=_("Leave as now to publish immediately, or pick a future date/time to schedule it."),
    )


class RecordFeePaymentForm(forms.Form):
    """Money received against one membership's fee -- see club.services.fees.record_payment.
    Reusable for any amount, partial or the exact remaining balance; "Mark fully
    paid" (management.views.MembershipMarkFullyPaidView) skips this form entirely
    and settles the balance directly in one click."""

    amount = forms.DecimalField(label=_("Amount"), max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    method = forms.ChoiceField(label=_("Method"), choices=FeePayment.Method.choices)
    reference = forms.CharField(label=_("Reference"), required=False, help_text=_("Bank reference, transaction id — whatever lets you find this again."))
    note = forms.CharField(label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 2}))
