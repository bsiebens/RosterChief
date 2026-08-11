import datetime
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership, ClubRole, FeePayment, Sponsor
from club.services.access import groups_manageable_by, is_club_admin, teams_managed_by
from events.models import Competition, Event, EventReferee, EventSeries, Location, Opponent
from events.services.rbihf_import import RBIHFImportError, extract_team_id
from members.models import Family, FamilyMembership, Group, Member
from members.services.family import find_member_by_email
from news.models import News
from teams.models import Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership, TeamPhoto
from teams.services import eligible_roster_members

from .recurrence_ui import FREQUENCY_CHOICES, WEEKDAY_CHOICES, build_rrule, parse_rrule

User = get_user_model()


class MemberForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = ["first_name", "last_name", "date_of_birth", "email", "phone", "emergency_phone"]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name", "short_name", "referee_management"]


class TeamPhotoForm(forms.ModelForm):
    """One photo per (team, season) -- bound to the existing TeamPhoto (if
    any) by the view's get_form_kwargs, same "create or update the one row
    for this scope" pattern as controlpanel.forms.HomeLocationForm."""

    class Meta:
        model = TeamPhoto
        fields = ["image"]


class GroupForm(forms.ModelForm):
    """A generic named collection of members -- nothing team- or
    referee-specific here; see MemberRefereeEligibilityForm for that."""

    class Meta:
        model = Group
        fields = ["name"]


class RefereeLevelForm(forms.ModelForm):
    class Meta:
        model = RefereeLevel
        fields = ["name", "ordering", "teams"]
        widgets = {"teams": forms.SelectMultiple(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a team to search...")})}

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teams"].queryset = Team.objects.filter(club=club)


class MemberRefereeEligibilityForm(forms.ModelForm):
    """This member's referee level and how long it's valid for -- edited from
    the member's own page (teams.RefereeProfile), get-or-created on save.
    Which teams that translates to is derived entirely from the level
    (RefereeLevel.teams), not picked here -- see RefereeProfile.eligible_teams."""

    class Meta:
        model = RefereeProfile
        fields = ["level", "valid_until"]
        widgets = {"valid_until": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, club=None, member=None, **kwargs):
        instance = RefereeProfile.objects.filter(member=member).first() if member is not None else None
        super().__init__(*args, instance=instance, **kwargs)
        # UUIDModel PKs get their default at construction time (not at save()),
        # so a brand-new instance already has a non-None pk -- "is this new"
        # can't be read off self.instance.pk. Setting member here (rather than
        # in save()) means it's already correct before validation runs too.
        if instance is None:
            self.instance.member = member
        self.fields["level"].queryset = RefereeLevel.objects.filter(club=club)
        self.fields["level"].required = False
        self.fields["level"].empty_label = _("— no level (not eligible) —")


class ExternalRefereeForm(forms.Form):
    """Log a non-member referee (federation-appointed, most often) by name
    only -- see events.services.referees.add_external_referee."""

    name = forms.CharField(label=_("Referee name"))


class EventRefereeFeeForm(forms.ModelForm):
    """One referee assignment's payment details -- see
    events.services.referees.set_referee_fee. `km`/`km_rate` are left blank
    (not defaulted to 0) whenever no travel was logged, so the PDF export can
    tell "no kilometers" from "zero kilometers claimed"."""

    class Meta:
        model = EventReferee
        fields = ["fee", "km", "km_rate"]
        widgets = {
            "fee": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "km": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "km_rate": forms.NumberInput(attrs={"step": "any", "min": "0"}),
        }


class TeamMembershipForm(forms.ModelForm):
    """Add/edit one roster entry -- team and season come from the view (the URL
    already identifies both), never from the form itself."""

    class Meta:
        model = TeamMembership
        fields = ["member", "position", "jersey_number", "is_captain", "is_alternate_captain"]
        widgets = {"member": forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")})}

    def __init__(self, *args, club=None, team=None, season=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.season = season
        # eligible_roster_members already covers "active this season or the next
        # one, not lapsed/pending/cancelled or active only in some other season" --
        # eligible regardless of which season's roster is being edited (the team
        # detail page's season switcher can be pointed at an older season).
        members = eligible_roster_members(club)
        if team is not None and season is not None:
            # Already on this team's roster this season -- offering them again
            # would just fail the unique_member_per_team_per_season constraint.
            taken = TeamMembership.objects.filter(team=team, season=season).exclude(pk=self.instance.pk).values_list("member_id", flat=True)
            members = members.exclude(pk__in=taken)
        self.fields["member"].queryset = members
        self.fields["position"].queryset = Position.objects.filter(club=club, staff_position=False)

    def clean(self):
        cleaned = super().clean()
        # team/season aren't form fields (the view sets them from the URL, not user
        # input), so Django's automatic validate_unique() excludes both of them --
        # and with them, the whole unique_jersey_number_per_team_per_season check.
        # Without this, a clashing jersey number reaches the database unrejected
        # and surfaces as a raw IntegrityError instead of a form error.
        jersey_number = cleaned.get("jersey_number")
        if jersey_number is not None and self.team is not None and self.season is not None:
            clash = TeamMembership.objects.filter(team=self.team, season=self.season, jersey_number=jersey_number).exclude(pk=self.instance.pk).exists()
            if clash:
                self.add_error("jersey_number", _("Another player on this team already has this jersey number this season."))
        return cleaned


class StaffAssignmentForm(forms.ModelForm):
    """Assign/edit one staff assignment -- team and season come from the view,
    same reasoning as TeamMembershipForm."""

    class Meta:
        model = StaffAssignment
        fields = ["member", "position"]
        widgets = {"member": forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")})}

    def __init__(self, *args, club=None, team=None, season=None, **kwargs):
        super().__init__(*args, **kwargs)
        # See TeamMembershipForm for why current-or-next-season: eligible to be
        # assigned regardless of which season's staff list is being edited.
        members = eligible_roster_members(club)
        if team is not None and season is not None:
            taken = StaffAssignment.objects.filter(team=team, season=season).exclude(pk=self.instance.pk).values_list("member_id", flat=True)
            members = members.exclude(pk__in=taken)
        self.fields["member"].queryset = members
        self.fields["position"].queryset = Position.objects.filter(club=club, staff_position=True)


class PositionSelect(forms.Select):
    """Marks staff positions in the DOM (``data-staff``) so a bulk-add row's
    jersey-number input can disable itself for them -- a jersey number is
    meaningless on a StaffAssignment, which has no such field."""

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        position = getattr(value, "instance", None)
        if position is not None and position.staff_position:
            option["attrs"]["data-staff"] = "1"
        return option


class GroupedPositionChoiceField(forms.ModelChoiceField):
    """Positions split into "Player positions" / "Staff positions" optgroups.

    Which group a position sits in is exactly what tells the bulk-add view
    whether a row means a ``TeamMembership`` or a ``StaffAssignment``
    (``Position.staff_position``) -- so the row needs no separate "player or
    staff?" control that could drift out of step with the position picked.
    """

    widget = PositionSelect

    def _get_choices(self):
        if hasattr(self, "_choices"):
            return self._choices

        iterator = self.iterator(self)
        player, staff = [], []
        for value, label in iterator:
            if value == "":
                continue  # the empty label is yielded separately below
            target = staff if getattr(value, "instance", None) is not None and value.instance.staff_position else player
            target.append((value, label))

        grouped = []
        if self.empty_label is not None:
            grouped.append(("", self.empty_label))
        if player:
            grouped.append((_("Player positions"), player))
        if staff:
            grouped.append((_("Staff positions"), staff))
        return grouped

    choices = property(_get_choices, forms.ChoiceField.choices.fset)


def bulk_add_member_label(member, *, rostered_ids, staffed_ids):
    """"Peter Player — on roster" for the bulk-add member picker.

    Already-assigned members stay selectable rather than being filtered out:
    someone already on the roster as a player can still legitimately be added
    as staff (a playing coach), so what's "taken" depends on the position the
    row ends up picking. The label is the hint; the row's own clean() is what
    actually refuses a true duplicate.
    """
    marks = []
    if member.pk in rostered_ids:
        marks.append(_("on roster"))
    if member.pk in staffed_ids:
        marks.append(_("on staff"))
    if not marks:
        return str(member)
    # Not itself a translatable message -- just a separator between the member's
    # name and the already-translated markers.
    return f"{member} — {', '.join(str(mark) for mark in marks)}"


class TeamBulkAddRowForm(forms.Form):
    """One row of the team bulk-add page: who, in what position, and (players
    only) their jersey number and captaincy.

    Rows replace the old "every eligible member gets a table row" layout, which
    doesn't survive a club with a hundred-plus members. `member` is a searchable
    select (static ``choices``, not the field's own lazy queryset iterator, so a
    twenty-row formset doesn't re-query the member list twenty times over).
    """

    member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        label=_("Member"),
        widget=forms.Select(attrs={"class": "select select-bordered w-full", "data-searchable": "true", "data-search-placeholder": _("Type a name to search...")}),
    )
    position = GroupedPositionChoiceField(
        queryset=Position.objects.none(),
        label=_("Position"),
        widget=PositionSelect(attrs={"class": "select select-bordered w-full position-select"}),
    )
    jersey_number = forms.IntegerField(
        required=False,
        min_value=0,
        label=_("Jersey #"),
        widget=forms.NumberInput(attrs={"class": "input input-bordered w-full player-only", "min": "0"}),
    )
    # Player-only, same as jersey_number -- "player-only" marks them for the
    # row script, which greys them out when a staff position is picked.
    is_captain = forms.BooleanField(required=False, label=_("Captain"), widget=forms.CheckboxInput(attrs={"class": "checkbox player-only"}))
    is_alternate_captain = forms.BooleanField(required=False, label=_("Alternate"), widget=forms.CheckboxInput(attrs={"class": "checkbox player-only"}))

    def __init__(self, *args, club=None, team=None, season=None, member_choices=None, position_queryset=None, rostered_ids=frozenset(), staffed_ids=frozenset(), **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.season = season
        self.rostered_ids = rostered_ids
        self.staffed_ids = staffed_ids

        self.fields["member"].queryset = eligible_roster_members(club)
        if member_choices is not None:
            self.fields["member"].choices = member_choices

        self.fields["position"].queryset = position_queryset if position_queryset is not None else Position.objects.filter(club=club)

    def clean(self):
        cleaned = super().clean()
        member, position = cleaned.get("member"), cleaned.get("position")
        if member is None or position is None:
            return cleaned

        if position.staff_position:
            # A staff row becomes a StaffAssignment, which has no jersey number
            # and no captaincy -- both belong to TeamMembership only.
            if member.pk in self.staffed_ids:
                self.add_error("member", _("%(member)s is already on this team's staff for this season.") % {"member": member})
            if cleaned.get("jersey_number") is not None:
                self.add_error("jersey_number", _("A jersey number doesn't apply to a staff position."))
            if cleaned.get("is_captain"):
                self.add_error("is_captain", _("Captaincy doesn't apply to a staff position."))
            if cleaned.get("is_alternate_captain"):
                self.add_error("is_alternate_captain", _("Captaincy doesn't apply to a staff position."))
            return cleaned

        if member.pk in self.rostered_ids:
            self.add_error("member", _("%(member)s is already on this team's roster for this season.") % {"member": member})

        # Contradictory rather than a club policy: an alternate captain is by
        # definition not the captain. How *many* captains a team may have isn't
        # constrained anywhere (not by the model, not by the single-add form),
        # so this row deliberately doesn't invent that rule either.
        if cleaned.get("is_captain") and cleaned.get("is_alternate_captain"):
            self.add_error("is_alternate_captain", _("Someone can be captain or alternate captain, not both."))

        jersey_number = cleaned.get("jersey_number")
        if jersey_number is not None and self.team is not None and self.season is not None:
            if TeamMembership.objects.filter(team=self.team, season=self.season, jersey_number=jersey_number).exists():
                self.add_error("jersey_number", _("Jersey #%(number)s is already taken on this team this season.") % {"number": jersey_number})
        return cleaned


class BaseTeamBulkAddFormSet(forms.BaseFormSet):
    """Cross-row checks the individual rows can't see: the same person twice,
    or two rows claiming one jersey number. Per-row checks (already assigned,
    jersey already taken by an *existing* roster entry) live on the row form."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        seen_assignments, seen_jerseys = set(), set()
        for form in self.forms:
            member, position = form.cleaned_data.get("member"), form.cleaned_data.get("position")
            if member is None or position is None:
                continue

            assignment = (member.pk, bool(position.staff_position))
            if assignment in seen_assignments:
                form.add_error("member", _("%(member)s is listed twice for the same kind of position.") % {"member": member})
            seen_assignments.add(assignment)

            jersey_number = form.cleaned_data.get("jersey_number")
            if jersey_number is None or position.staff_position:
                continue
            if jersey_number in seen_jerseys:
                form.add_error("jersey_number", _("Jersey #%(number)s is claimed by more than one row.") % {"number": jersey_number})
            seen_jerseys.add(jersey_number)


TeamBulkAddFormSet = forms.formset_factory(TeamBulkAddRowForm, formset=BaseTeamBulkAddFormSet, extra=3)


class GroupBulkAddRowForm(forms.Form):
    """One row of the group bulk-add page -- just who. Group membership carries
    no per-member attributes, so there's nothing else to fill in."""

    member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        label=_("Member"),
        widget=forms.Select(attrs={"class": "select select-bordered w-full", "data-searchable": "true", "data-search-placeholder": _("Type a name to search...")}),
    )

    def __init__(self, *args, member_queryset=None, member_choices=None, existing_ids=frozenset(), **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_ids = existing_ids
        self.fields["member"].queryset = member_queryset if member_queryset is not None else Member.objects.none()
        if member_choices is not None:
            self.fields["member"].choices = member_choices

    def clean_member(self):
        member = self.cleaned_data["member"]
        if member.pk in self.existing_ids:
            raise forms.ValidationError(_("%(member)s is already in this group.") % {"member": member})
        return member


class BaseGroupBulkAddFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        seen = set()
        for form in self.forms:
            member = form.cleaned_data.get("member")
            if member is None:
                continue
            if member.pk in seen:
                form.add_error("member", _("%(member)s is listed twice.") % {"member": member})
            seen.add(member.pk)


GroupBulkAddFormSet = forms.formset_factory(GroupBulkAddRowForm, formset=BaseGroupBulkAddFormSet, extra=3)


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


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["name", "address", "city", "zip_code", "country"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # CountryField's own widget (a lazily-translated Select) must stay the widget
        # class -- only the searchable-select JS hooks are added on top of it, the
        # same progressive enhancement TeamMembershipForm uses for its member field.
        self.fields["country"].widget.attrs.update({"data-searchable": "true", "data-search-placeholder": _("Type a country to search...")})


class OpponentForm(forms.ModelForm):
    class Meta:
        model = Opponent
        fields = ["name", "logo"]


class SponsorForm(forms.ModelForm):
    class Meta:
        model = Sponsor
        fields = ["name", "logo", "url", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned = super().clean()
        # Mirrors Sponsor.clean() -- caught here too so it reads as a form
        # error tied to the end_date field, not a raw ValidationError.
        start_date, end_date = cleaned.get("start_date"), cleaned.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", _("End date can't be before the start date."))
        return cleaned


def _location_label(location) -> str:
    """"Name — City" for the location picker, plus the country when it isn't
    Belgium (the club's home country and, in practice, nearly every location
    a club will ever add) -- lets an admin tell two same-named venues apart,
    or spot an away trip abroad, straight from the dropdown."""
    label = f"{location.name} — {location.city}"
    if location.country.code != "BE":
        label += f", {location.country.name}"
    return label


class EventAudienceFormMixin:
    """Shared club/user-scoped audience fields for EventForm and EventSeriesForm:
    teams restricted to the ones the requester manages (all of them for an
    admin), groups restricted to the ones the requester belongs to (all of them
    for an admin -- Group has no manager/owner concept the way Team does, so
    membership is the only claim there is), and a non-admin must pick at least
    one team or group -- a club-wide event (e.g. an AGM) has no such claim to
    anchor it to, so `club_wide` stays admin-only (the field doesn't even exist
    on the form otherwise)."""

    def scope_audience_fields(self, club, user):
        self.club = club
        self.user = user
        self.fields["teams"].queryset = teams_managed_by(user, club)
        self.fields["groups"].queryset = groups_manageable_by(user, club)
        self.fields["location"].queryset = Location.objects.filter(club=club)
        self.fields["location"].label_from_instance = _location_label
        self.fields["opponent"].queryset = Opponent.objects.filter(club=club)
        members = Member.objects.filter(member_of__club=club).distinct()
        self.fields["invited_members"].queryset = members
        self.fields["excluded_members"].queryset = members
        if not is_club_admin(user, club):
            del self.fields["club_wide"]

    def clean_audience_requires_a_claim_for_non_admins(self, cleaned):
        if is_club_admin(self.user, self.club):
            return
        teams = cleaned.get("teams")
        groups = cleaned.get("groups")
        if not (teams is not None and teams.exists()) and not (groups is not None and groups.exists()):
            self.add_error(None, _("Select at least one of your teams or groups, or ask an admin to create a club-wide event."))

    def clean_club_wide_excludes_teams_and_groups(self, cleaned):
        if not cleaned.get("club_wide"):
            return
        teams = cleaned.get("teams")
        groups = cleaned.get("groups")
        if (teams is not None and teams.exists()) or (groups is not None and groups.exists()):
            self.add_error("club_wide", _("A whole-club event can't also list specific teams or groups -- clear them, or turn this off."))


_AUDIENCE_WIDGETS = {
    "teams": forms.SelectMultiple(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a team to search...")}),
    "groups": forms.SelectMultiple(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a group to search...")}),
    "invited_members": forms.SelectMultiple(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")}),
    "excluded_members": forms.SelectMultiple(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name to search...")}),
    "location": forms.Select(attrs={"data-searchable": "true", "data-search-placeholder": _("Type a name or city to search...")}),
}


class EventForm(EventAudienceFormMixin, forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "title",
            "kind",
            "teams",
            "groups",
            "club_wide",
            "invited_members",
            "excluded_members",
            "location",
            "opponent",
            "start",
            "end",
            "gathering",
            "deadline",
            "competition",
            "external_game_id",
            "score_for",
            "score_against",
            "is_live",
            "max_referees",
        ]
        widgets = {
            "start": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "gathering": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "score_for": forms.NumberInput(attrs={"min": 0}),
            "score_against": forms.NumberInput(attrs={"min": 0}),
            "max_referees": forms.NumberInput(attrs={"min": 1}),
            **_AUDIENCE_WIDGETS,
        }

    def __init__(self, *args, club=None, user=None, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope_audience_fields(club, user)
        # Unlike the Django-admin form (events.admin.EventAdminForm), this dropdown
        # isn't filtered by which competitions have their feature flag on for the
        # club -- a manager should be able to pick any competition when scheduling a
        # game; it's fetch_game_info (events.services.competitions) that gates the
        # actual per-club fetch once a competition is set.
        self.fields["competition"] = forms.ChoiceField(
            choices=[("", "---------"), *[(competition.name, competition.name) for competition in Competition.objects.all()]],
            required=False,
            label=self.fields["competition"].label,
            help_text=self.fields["competition"].help_text,
        )
        if not editing:
            # Score/live status don't exist yet for a game that's only just being
            # scheduled -- offering them on the add form is just noise. Editing an
            # existing game is the only time there's anything to record here.
            del self.fields["score_for"]
            del self.fields["score_against"]
            del self.fields["is_live"]

    def clean(self):
        cleaned = super().clean()
        self.clean_audience_requires_a_claim_for_non_admins(cleaned)
        self.clean_club_wide_excludes_teams_and_groups(cleaned)
        return cleaned


class RBIHFImportForm(forms.Form):
    """Step 1 of importing a team's fixtures from RBIHF's own website -- see
    events.services.rbihf_import. Only the URL and which of the club's own
    teams it applies to; everything else is scraped."""

    url = forms.CharField(
        label=_("RBIHF team page URL"),
        help_text=_("E.g. https://www.rbihf.be/league/team/4460"),
        widget=forms.URLInput(attrs={"placeholder": "https://www.rbihf.be/league/team/4460"}),
    )
    team = forms.ModelChoiceField(queryset=Team.objects.none(), label=_("Team"), help_text=_("Which of your teams this fixture list is for."))

    def __init__(self, *args, club=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = Team.objects.filter(club=club)

    def clean_url(self):
        url = self.cleaned_data["url"].strip()
        try:
            extract_team_id(url)
        except RBIHFImportError as error:
            raise forms.ValidationError(str(error)) from error
        return url


class EventSeriesForm(EventAudienceFormMixin, forms.ModelForm):
    """A friendly weekly/monthly recurrence picker on top of EventSeries' raw
    RRULE (see management.recurrence_ui) -- covers the common case (every N
    weeks/months) with an advanced raw-RRULE field as an escape hatch for
    anything else."""

    frequency = forms.ChoiceField(choices=FREQUENCY_CHOICES, initial="weekly", label=_("Repeats"))
    interval = forms.IntegerField(min_value=1, initial=1, label=_("Every"), help_text=_("E.g. 2 for every other week/month."))
    # A plain multi-select, not CheckboxSelectMultiple: its widget_type ("checkboxselectmultiple")
    # isn't one form_field's ui.py recognises, which would silently render a broken
    # plain text input instead -- see the "lazyselect" fix for the same class of bug.
    weekdays = forms.MultipleChoiceField(choices=WEEKDAY_CHOICES, required=False, label=_("On"), widget=forms.SelectMultiple(attrs={"data-searchable": "true"}))
    duration_hours = forms.IntegerField(min_value=0, required=False, label=_("Duration (hours)"))
    duration_minutes = forms.IntegerField(min_value=0, max_value=59, required=False, label=_("Duration (minutes)"))
    gathering_minutes_before = forms.IntegerField(min_value=0, required=False, label=_("Gather (minutes before)"))
    deadline_minutes_before = forms.IntegerField(min_value=0, required=False, label=_("Registration deadline (minutes before)"))
    advanced_rrule = forms.CharField(required=False, label=_("Advanced: raw recurrence rule"), help_text=_("Overrides the fields above if filled in. RFC 5545 RRULE, e.g. FREQ=WEEKLY;BYDAY=MO,WE."))

    class Meta:
        model = EventSeries
        fields = ["title", "kind", "dtstart", "until", "teams", "groups", "club_wide", "invited_members", "excluded_members", "location", "opponent"]
        widgets = {
            "dtstart": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            **_AUDIENCE_WIDGETS,
        }

    def __init__(self, *args, club=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope_audience_fields(club, user)

        if self.instance.pk:
            parsed = parse_rrule(self.instance.rrule)
            if parsed is not None:
                self.fields["frequency"].initial = parsed["frequency"]
                self.fields["interval"].initial = parsed["interval"]
                self.fields["weekdays"].initial = parsed["weekdays"]
            else:
                # Not one of our two shapes (hand-written/imported) -- show it
                # verbatim in the advanced field rather than guessing at it.
                self.fields["advanced_rrule"].initial = self.instance.rrule

            if self.instance.duration is not None:
                total_minutes = int(self.instance.duration.total_seconds() // 60)
                self.fields["duration_hours"].initial = total_minutes // 60
                self.fields["duration_minutes"].initial = total_minutes % 60
            if self.instance.gathering_offset is not None:
                self.fields["gathering_minutes_before"].initial = int(self.instance.gathering_offset.total_seconds() // 60)
            if self.instance.deadline_offset is not None:
                self.fields["deadline_minutes_before"].initial = int(self.instance.deadline_offset.total_seconds() // 60)

    def clean(self):
        cleaned = super().clean()
        self.clean_audience_requires_a_claim_for_non_admins(cleaned)
        self.clean_club_wide_excludes_teams_and_groups(cleaned)

        if cleaned.get("advanced_rrule"):
            return cleaned  # the advanced field wins outright; nothing else to check

        if cleaned.get("frequency") == "weekly" and not cleaned.get("weekdays"):
            self.add_error("weekdays", _("Pick at least one day of the week."))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        cleaned = self.cleaned_data

        advanced = (cleaned.get("advanced_rrule") or "").strip()
        instance.rrule = advanced or build_rrule(cleaned["frequency"], cleaned["interval"], cleaned.get("weekdays"))

        hours, minutes = cleaned.get("duration_hours") or 0, cleaned.get("duration_minutes") or 0
        instance.duration = datetime.timedelta(hours=hours, minutes=minutes) if (hours or minutes) else None

        gathering_minutes = cleaned.get("gathering_minutes_before")
        instance.gathering_offset = datetime.timedelta(minutes=gathering_minutes) if gathering_minutes else None

        deadline_minutes = cleaned.get("deadline_minutes_before")
        instance.deadline_offset = datetime.timedelta(minutes=deadline_minutes) if deadline_minutes else None

        if commit:
            instance.save()
            self.save_m2m()
        return instance


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
        fields = ["title", "title_en", "teams", "visibility", "body", "body_en"]
        widgets = {
            "teams": forms.SelectMultiple(attrs={"data-searchable": "true", "data-search-placeholder": _("Type to filter teams...")}),
            "body": forms.Textarea(attrs={"rows": 8}),
            "body_en": forms.Textarea(attrs={"rows": 8}),
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
