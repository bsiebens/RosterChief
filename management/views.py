from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, ProtectedError, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView, View

from billing.models import Due
from club.mixins import (
    ClubAdminRequiredMixin,
    ClubStaffRequiredMixin,
    EventManagerRequiredMixin,
    FeatureRequiredMixin,
    ManagementPositionRequiredMixin,
    MemberAdminRequiredMixin,
    NewsAuthorRequiredMixin,
    NewsEditRequiredMixin,
    NewsPublisherRequiredMixin,
    TeamManagerRequiredMixin,
)
from club.models import ClubMembership, ClubRole, DuesInvoice, MemberRequirementStatus, OnboardingRequirement, Season, Sponsor
from club.services.access import _guardians_only, can_edit_news, can_publish_news, current_season, groups_manageable_by, is_club_admin, members_visible_to, teams_managed_by, teams_staffed_by
from club.services.fees import mark_as_paid, record_payment, remaining_balance
from club.services.invoicing import DuesInvoicePDFError, create_or_resend_invoice, invoice_pdf, invoices_due_for_reminder, recipient_for, send_invoice_email, send_reminders
from club.services.onboarding import annotate_onboarding_status, approve_all_clean, approve_one, blocking_event_kinds, checklist_for, is_signup_clean, mark_bypassed, mark_complete, mark_incomplete, members_with_open_requirements
from controlpanel.messages import notify
from controlpanel.mixins import RedirectOnInvalidMixin
from controlpanel.services.statistics import club_attention, club_charts, club_statistics, unrostered_members
from events.models import Attendance, Event, EventReferee, EventSeries, Location, Opponent
from events.services.attendance import member_attendance_counts, member_attendance_sparkline, player_attendance_rankings, players_who_missed_recent_practices, team_attendance_rate, team_no_shows
from events.services.calendar import add_months, month_bounds, month_grid, season_grid, week_bounds, week_grid
from events.services.competitions import CompetitionFetchError, fetch_game_info
from events.services.rbihf_import import RBIHFImportError, apply_plan, build_plan, extract_team_id, fetch_html
from events.services.recurrence import cancel_occurrence, detach_occurrence, generate_occurrences, propagate_series
from events.services.referees import RefereeAssignmentError, add_external_referee, assign_referee, conflicting_events, eligible_referees, needs_referee_management, remove_referee, set_referee_fee
from formbuilder.models import Form as FormBuilderForm
from formbuilder.models import Submission
from members.forms import ClaimRejectForm, ClaimReviewForm
from members.models import Family, FamilyMembership, Group, GroupMembership, Member, ParentClaim
from members.services.claims import ClaimError, approve_claim, children_awaiting_a_parent, reject_claim, send_claim_approved_email, suggested_children
from members.services.family import add_child_to_family, add_parent_to_family, attach_to_family, detach_from_family, get_or_create_login_user, grant_login, register_family
from news.models import News, NewsPhoto
from news.tasks import notify_news_published
from shop.models import Discount, Invoice, Order, Product
from teams.models import Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership, TeamPhoto
from teams.services import eligible_roster_members

from .bulk_import import build_member_import_template, parse_member_import_rows, read_member_import_workbook
from .email_previews import EMAIL_PREVIEWS, EMAIL_PREVIEWS_BY_KEY, render_preview
from .forms import (
    AddChildForm,
    AddParentForm,
    AttachToFamilyForm,
    ClubMembershipForm,
    ClubRoleAssignForm,
    ClubSettingsForm,
    EventForm,
    EventRefereeFeeForm,
    EventSeriesForm,
    ExternalRefereeForm,
    FamilyCreateForm,
    GrantLoginForm,
    GroupBulkAddFormSet,
    GroupForm,
    LocationForm,
    MemberForm,
    MemberImportUploadForm,
    MemberRefereeEligibilityForm,
    NewsForm,
    NewsPhotoUploadForm,
    NewsPublishForm,
    OnboardingRequirementForm,
    OpponentForm,
    PositionForm,
    RBIHFImportForm,
    RecordFeePaymentForm,
    RefereeLevelForm,
    RequirementBypassForm,
    RequirementCompletionForm,
    SendDuesInvoicesForm,
    SignupTeamPlacementForm,
    SponsorForm,
    StaffAssignmentForm,
    TeamBulkAddFormSet,
    TeamForm,
    TeamMembershipForm,
    TeamPhotoForm,
    bulk_add_member_label,
)
from .pdf import PDFExportError, event_referee_form_pdf, membership_list_pdf, referee_form_colors
from .pdf_previews import PDF_PREVIEWS, PDF_PREVIEWS_BY_KEY, render_pdf_preview
from .recurrence_ui import describe_rrule


class HomeView(ClubStaffRequiredMixin, TemplateView):
    """The at-a-glance numbers a club admin/team manager/coach would actually want:
    club_attention/club_charts/club_statistics are the exact functions
    controlpanel/club_detail.html uses for the platform admin's per-club drill-down --
    already club-scoped, so directly reusable for this club's own staff. Published
    news is open to everyone; upcoming events are scoped the same way the events
    list is (see scoped_to_managed_teams) -- a manager shouldn't see another
    team's practice show up here either."""

    template_name = "management/home.html"

    def get_context_data(self, **kwargs):
        club, user = self.request.club, self.request.user
        subscription = getattr(club, "subscription", None)

        # "Your period ends soon" is a different question from "you owe us money", and only
        # worth raising when nothing is owed -- an unpaid club gets the billing notice from
        # the context processor instead, which is louder and more urgent.
        billing_ends_at = None
        if subscription is not None and not club.dues.filter(status__in=Due.OWING).exists():
            latest_due = club.dues.exclude(status=Due.Status.CANCELLED).order_by("-period_end").first()
            if latest_due is not None and 0 <= (latest_due.period_end - timezone.localdate()).days <= subscription.plan.renewal_lead_days:
                billing_ends_at = latest_due.period_end

        upcoming_events = scoped_to_managed_teams(Event.objects.filter(club=club, start__gte=timezone.now()), user, club).order_by("start").prefetch_related("teams")[:5]

        attention = club_attention(club)
        season = attention["season"]

        # Compared against the season right before this one (not last year on the
        # calendar) -- Season.before, same "adjacent by date" idea as next_after.
        member_count = ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER).count() if season else 0
        previous_season = Season.before(club, season) if season else None
        member_count_change = None
        if previous_season is not None:
            previous_member_count = ClubMembership.objects.filter(club=club, season=previous_season, kind=ClubMembership.Kind.MEMBER).count()
            member_count_change = member_count - previous_member_count

        # Hidden entirely (not just zeroed) for a club that hasn't set up any
        # onboarding requirements -- "0 missing" would otherwise read as "everyone's
        # paperwork is in", when really there's no paperwork being tracked at all.
        requirements_configured = OnboardingRequirement.objects.filter(club=club, is_active=True).exists()
        missing_documentation_count = 0
        if requirements_configured and season is not None:
            memberships_this_season = ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER)
            missing_documentation_count = sum(1 for membership in annotate_onboarding_status(memberships_this_season) if membership.onboarding_open)

        return super().get_context_data(
            attention=attention,
            charts=club_charts(club),
            groups=club_statistics(club),
            upcoming_events=upcoming_events,
            member_count=member_count,
            member_count_change=member_count_change,
            requirements_configured=requirements_configured,
            missing_documentation_count=missing_documentation_count,
            published_news=News.objects.filter(club=club, status=News.Status.PUBLISHED, published_at__lte=timezone.now()).order_by("-published_at")[:5],
            billing_ends_at=billing_ends_at,
            billing_auto_renews=subscription.auto_renew if subscription else False,
            today=timezone.localdate(),
            **kwargs,
        )


# --- Members (full tier) -----------------------------------------------------------


def group_by_family(members):
    """Bucket an already-scoped Member iterable by family: {family, guardians,
    children, others}, plus whatever's left un-grouped.

    Deliberately built from ``members`` rather than ``Family.guardians``/``.children``
    (members/models.py) -- those query a family's *entire* membership unconditionally,
    which would leak people outside whatever visibility scope ``members`` already
    represents (e.g. a coach who only sees their own rostered players).
    """
    memberships = FamilyMembership.objects.filter(member__in=members).select_related("family", "member")

    groups = {}
    for fm in memberships:
        bucket = groups.setdefault(fm.family, {"family": fm.family, "guardians": [], "children": [], "others": []})
        # Attached here, not read later from person.family_memberships.first() --
        # a member can belong to more than one family, so "their role" only means
        # anything once it's scoped to *this* family's membership row.
        fm.member.role_in_family = fm.role
        fm.member.role_in_family_display = fm.get_role_display()
        # A login-less child can be granted one right from the table -- parents/
        # guardians already get one when added (add_parent_to_family/register_family
        # both call get_or_create_login_member), so in practice this is only ever
        # None for a child, but the check is on the actual state, not the role alone.
        if fm.role == FamilyMembership.FamilyRole.CHILD and fm.member.user_id is None:
            fm.member.grant_login_form = GrantLoginForm(initial={"email": fm.member.email})
        else:
            fm.member.grant_login_form = None
        if fm.role in (FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN):
            bucket["guardians"].append(fm.member)
        elif fm.role == FamilyMembership.FamilyRole.CHILD:
            bucket["children"].append(fm.member)
        else:
            bucket["others"].append(fm.member)

    grouped_ids = {fm.member_id for fm in memberships}
    ungrouped = [member for member in members if member.pk not in grouped_ids]

    groups = list(groups.values())
    for group in groups:
        # One flat list for anything that just wants "everyone in this family",
        # regardless of role (e.g. rendering one delete-confirm modal per member).
        group["all"] = group["guardians"] + group["children"] + group["others"]

    return groups, ungrouped


class MemberListView(ClubStaffRequiredMixin, ListView):
    """One flat list, everybody -- family is a column, not a grouping. Each
    member's family/role is attached in Python below (from a single query over
    the already-scoped ``members``), same reasoning as group_by_family: never
    resolve it per-row from the template via Family.guardians/.children, which
    would ignore visibility scoping entirely."""

    template_name = "management/member_list.html"
    context_object_name = "members"
    paginate_by = 25

    def get_queryset(self):
        # A guardian -- a parent linked to the club only through a child, no fee,
        # not counted anywhere (see ClubMembership.Kind) -- simply doesn't show up
        # here by default, with nothing on the page explaining why they seem to
        # have vanished right after being added via "Add family"/"Add parent".
        # ?kind= turns that silent exclusion into an explicit, visible choice:
        # "member" (default) matches the page's original behaviour exactly,
        # "guardian" flips it to show only the excluded guardians, "both" shows
        # everyone. _guardians_only is intersected with members_visible_to (not
        # queried club-wide on its own) so a non-admin still only ever sees
        # guardians of someone already visible to them.
        kind = self.request.GET.get("kind", "member")
        if kind == "guardian":
            members = members_visible_to(self.request.user, self.request.club, include_guardians=True).filter(pk__in=_guardians_only(self.request.club))
        elif kind == "both":
            members = members_visible_to(self.request.user, self.request.club, include_guardians=True)
        else:
            kind = "member"
            members = members_visible_to(self.request.user, self.request.club)
        self.selected_kind = kind

        # Status/fee status/team all key off the *current* season's ClubMembership --
        # a member's status/fee last season (or next) isn't what "filter by Pending"
        # means on a page showing everyone's standing right now.
        season = current_season(self.request.club)
        self.selected_status = self.request.GET.get("status", "")
        self.selected_fee_status = self.request.GET.get("fee_status", "")
        self.selected_team = self.request.GET.get("team", "")
        self.selected_unrostered = self.request.GET.get("unrostered", "")
        self.selected_docs = self.request.GET.get("docs", "")

        if self.selected_status:
            members = members.filter(member_of__season=season, member_of__status=self.selected_status)
        if self.selected_fee_status:
            members = members.filter(member_of__season=season, member_of__fee_status=self.selected_fee_status)
        if self.selected_team:
            members = members.filter(team_memberships__season=season, team_memberships__team_id=self.selected_team)
        if self.selected_unrostered:
            # The dashboard's "Unrostered members" attention row -- same query
            # club_attention() itself counts (controlpanel.services.statistics).
            members = members.filter(pk__in=unrostered_members(self.request.club, season).values("pk"))
        if self.selected_docs == "open":
            # The dashboard's "Missing documentation" KPI/attention row.
            members = members.filter(pk__in=members_with_open_requirements(self.request.club, season).values("pk"))

        search = self.request.GET.get("q", "").strip()
        if search:
            members = members.filter(first_name__icontains=search) | members.filter(last_name__icontains=search) | members.filter(email__icontains=search) | members.filter(user__email__icontains=search)
        return members.distinct()

    def get_context_data(self, **kwargs):
        total_requirements = OnboardingRequirement.objects.filter(club=self.request.club, is_active=True).count()
        context = super().get_context_data(
            search=self.request.GET.get("q", ""),
            selected_kind=self.selected_kind,
            selected_status=self.selected_status,
            selected_fee_status=self.selected_fee_status,
            selected_team=self.selected_team,
            selected_unrostered=self.selected_unrostered,
            selected_docs=self.selected_docs,
            status_choices=ClubMembership.StatusChoices.choices,
            fee_status_choices=ClubMembership.FeeStatus.choices,
            teams=Team.objects.filter(club=self.request.club),
            requirements_configured=total_requirements > 0,
            total_requirements=total_requirements,
            **kwargs,
        )
        members = list(context["members"])

        memberships = FamilyMembership.objects.filter(member__in=members).select_related("family")
        memberships_by_member_id = {}
        for fm in memberships:
            memberships_by_member_id.setdefault(fm.member_id, []).append(fm)
        for member in members:
            member.family_memberships_display = memberships_by_member_id.get(member.pk, [])

        season = current_season(self.request.club)
        club_memberships_by_member_id = {}
        if season is not None:
            club_memberships = ClubMembership.objects.filter(club=self.request.club, season=season, member__in=members)
            if total_requirements:
                club_memberships = annotate_onboarding_status(club_memberships)
                for cm in club_memberships:
                    cm.completed_requirements = total_requirements - cm.onboarding_open
            club_memberships_by_member_id = {cm.member_id: cm for cm in club_memberships}
        for member in members:
            member.current_membership = club_memberships_by_member_id.get(member.pk)

        return context | {"members": members}


def selected_season_from_request(request, club):
    """Which season a page showing season-scoped data should use: ``?season=<pk>``
    if given (and it's actually one of this club's own seasons), else whichever
    season covers today. Shared by every page with a season switcher."""
    season_id = request.GET.get("season")
    if season_id:
        season = Season.objects.filter(club=club, pk=season_id).first()
        if season is not None:
            return season
    return current_season(club)


class MembershipListView(ClubAdminRequiredMixin, ListView):
    """Who's paid for the current season, and who hasn't -- MemberListView's Status
    column can only show this one row at a time. Financial data, so admin-only
    throughout (same line already drawn around the Shop nav section)."""

    template_name = "management/membership_list.html"
    context_object_name = "memberships"

    def get_selected_season(self):
        return selected_season_from_request(self.request, self.request.club)

    def get_queryset(self):
        season = self.get_selected_season()
        if season is None:
            return ClubMembership.objects.none()

        # kind=MEMBER: a guardian holds no membership and owes no fee, so they
        # belong in neither this list nor the KPIs below it.
        memberships = ClubMembership.objects.filter(club=self.request.club, season=season, kind=ClubMembership.Kind.MEMBER).select_related("member").order_by("member__last_name", "member__first_name")

        fee_status = self.request.GET.get("fee_status", "not_paid")
        if fee_status == "not_paid":
            # Literally "does not have a paid status" -- unpaid, partially paid, and
            # waived all qualify; the dropdown can narrow to any single one of those.
            memberships = memberships.exclude(fee_status__in=[ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.PARTIALLY_PAID])
        elif fee_status and fee_status != "all":
            memberships = memberships.filter(fee_status=fee_status)

        status = self.request.GET.get("status", "all")
        if status and status != "all":
            memberships = memberships.filter(status=status)

        team_id = self.request.GET.get("team")
        if team_id:
            memberships = memberships.filter(member__team_memberships__team_id=team_id, member__team_memberships__season=season)

        search = self.request.GET.get("q", "").strip()
        if search:
            # Also matches by family -- searching "Smith" finds every member of a
            # family that has an explicit name of "Smith" or that includes anyone
            # surnamed Smith, not just a member literally named Smith themself.
            memberships = (
                memberships.filter(member__first_name__icontains=search)
                | memberships.filter(member__last_name__icontains=search)
                | memberships.filter(member__email__icontains=search)
                | memberships.filter(member__user__email__icontains=search)
                | memberships.filter(member__family_memberships__family__name__icontains=search)
                | memberships.filter(member__family_memberships__family__memberships__member__last_name__icontains=search)
            )

        return memberships.distinct()

    def get_context_data(self, **kwargs):
        club = self.request.club
        current = current_season(club)

        counts = {}
        if current is not None:
            counts = {row["fee_status"]: row["count"] for row in ClubMembership.objects.filter(club=club, season=current, kind=ClubMembership.Kind.MEMBER).values("fee_status").annotate(count=Count("id"))}
        paid = counts.get(ClubMembership.FeeStatus.PAID, 0)
        partial = counts.get(ClubMembership.FeeStatus.PARTIALLY_PAID, 0)
        unpaid = counts.get(ClubMembership.FeeStatus.UNPAID, 0)
        waived = counts.get(ClubMembership.FeeStatus.WAIVED, 0)
        total = paid + partial + unpaid + waived

        overdue_count = invoices_due_for_reminder(club).count() if current is not None else 0

        context = super().get_context_data(
            current_season=current,
            selected_season=self.get_selected_season(),
            seasons=Season.objects.filter(club=club).order_by("-start_date"),
            teams=Team.objects.filter(club=club).order_by("name"),
            fee_status_choices=ClubMembership.FeeStatus.choices,
            status_choices=ClubMembership.StatusChoices.choices,
            search=self.request.GET.get("q", ""),
            selected_fee_status=self.request.GET.get("fee_status", "not_paid"),
            selected_status=self.request.GET.get("status", "all"),
            selected_team=self.request.GET.get("team", ""),
            kpi_total=total,
            kpi_paid=paid,
            kpi_partial=partial,
            kpi_unpaid=unpaid,
            kpi_waived=waived,
            kpi_paid_rate=round(100 * paid / total) if total else None,
            kpi_overdue_invoices=overdue_count,
            send_invoice_form=SendDuesInvoicesForm(),
            **kwargs,
        )

        # Same reasoning as MemberListView: attached in Python from a single query,
        # never resolved per-row via Family.guardians/.children (which would ignore
        # this page's own club/season/filter scoping entirely).
        memberships = list(context["memberships"])
        members = [membership.member for membership in memberships]
        family_memberships = FamilyMembership.objects.filter(member__in=members).select_related("family")
        family_memberships_by_member_id = {}
        for fm in family_memberships:
            family_memberships_by_member_id.setdefault(fm.member_id, []).append(fm)
        invoices_by_membership_id = {invoice.membership_id: invoice for invoice in DuesInvoice.objects.filter(membership__in=memberships)}
        for membership in memberships:
            membership.member.family_memberships_display = family_memberships_by_member_id.get(membership.member_id, [])
            membership.remaining_balance_display = remaining_balance(membership)
            membership.dues_invoice = invoices_by_membership_id.get(membership.pk)
            # Nothing to collect on an already-settled or deliberately-exempted row.
            if membership.fee_status in (ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.WAIVED):
                membership.record_payment_form = None
            else:
                membership.record_payment_form = RecordFeePaymentForm()

        return context | {"memberships": memberships}


class MembershipMarkPaidView(ClubAdminRequiredMixin, View):
    """Flag a batch of memberships as settled: fee_status -> paid, in one go. There's
    no bank integration, so this is always a manual admin action -- the point of this
    view is to make the manual action fast, not to replace it with automation. Does
    not touch status/activate the membership -- see club.services.fees.mark_as_paid
    and OnboardingRequirement's docstring (club/models.py) for why that's exclusively
    the Sign-up page's Approve step.

    Uses club.services.fees.mark_as_paid per row (a .save() loop under the hood,
    never a bulk .update()), matching the per-row "Mark fully paid" button
    (MembershipMarkFullyPaidView) -- one place decides how a membership becomes paid.
    """

    def post(self, request):
        ids = request.POST.getlist("membership_ids")
        memberships = ClubMembership.objects.filter(pk__in=ids, club=request.club)

        count = 0
        with transaction.atomic():
            for membership in memberships:
                mark_as_paid(membership, recorded_by=request.user)
                count += 1

        notify(request, f"s|{_('Marked as paid')}|{_('%(count)d membership(s) updated.') % {'count': count}}")

        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(next_url)
        return redirect("management:membership_list")


class MembershipMarkFullyPaidView(ClubAdminRequiredMixin, View):
    """The per-row, one-click version of MembershipMarkPaidView's bulk action --
    no confirm modal, matching this app's convention of reserving those for
    destructive deletes, not state changes."""

    def post(self, request, pk):
        membership = get_object_or_404(ClubMembership, pk=pk, club=request.club)
        mark_as_paid(membership, recorded_by=request.user)
        notify(request, f"s|{_('Marked as paid')}|{_('“%(member)s” is now paid.') % {'member': membership.member}}")

        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(next_url)
        return redirect("management:membership_list")


class MembershipRecordPaymentView(ClubAdminRequiredMixin, View):
    """The per-row "Record payment" modal on the Memberships page -- any amount,
    partial or in full, via club.services.fees.record_payment."""

    def post(self, request, pk):
        membership = get_object_or_404(ClubMembership, pk=pk, club=request.club)
        form = RecordFeePaymentForm(request.POST)

        next_url = request.POST.get("next")
        redirect_url = next_url if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()) else reverse("management:membership_list")

        if not form.is_valid():
            for error in form.errors.values():
                notify(request, f"e|{_('Could not record payment')}|{' '.join(error)}")
            return redirect(redirect_url)

        record_payment(
            membership,
            amount=form.cleaned_data["amount"],
            method=form.cleaned_data["method"],
            reference=form.cleaned_data["reference"],
            note=form.cleaned_data["note"],
            recorded_by=request.user,
        )
        notify(request, f"s|{_('Payment recorded')}|{_('%(amount)s recorded for “%(member)s”.') % {'amount': form.cleaned_data['amount'], 'member': membership.member}}")
        return redirect(redirect_url)


class MembershipExportPdfView(MembershipListView):
    """The exact same filtered queryset and KPI numbers as MembershipListView --
    "export this page" means exactly that, never a differently-filtered list.
    Reachable with whatever query string the on-screen list currently has."""

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        context = self.get_context_data(club=request.club, generated_at=timezone.now())

        try:
            pdf = membership_list_pdf(context)
        except PDFExportError as error:
            # The native PDF libraries are missing: say so rather than 500, and
            # land back on the same filtered list rather than a blank one.
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return redirect(f"{reverse('management:membership_list')}?{request.GET.urlencode()}")

        season = context["selected_season"]
        filename = f"memberships-{season}.pdf" if season else "memberships.pdf"
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class MembershipSendInvoicesView(ClubAdminRequiredMixin, View):
    """The bulk "Send invoice" action on Dues & billing -- one invoice per selected
    membership, mailed to the member's own email or a parent/guardian's when they
    have none (see club.services.invoicing.recipient_for). Every membership in the
    batch shares the one due-in-days setting from the form; per-membership tracking
    (sent_at, who it went to, reminders) still lives on each invoice individually."""

    def post(self, request):
        next_url = request.POST.get("next")
        redirect_url = next_url if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()) else reverse("management:membership_list")

        ids = request.POST.getlist("membership_ids")
        if not ids:
            notify(request, f"e|{_('No members selected')}|{_('Select at least one member to invoice.')}")
            return redirect(redirect_url)

        form = SendDuesInvoicesForm(request.POST)
        if not form.is_valid():
            notify(request, f"e|{_('Could not send invoices')}|{_('Enter a valid number of days until due.')}")
            return redirect(redirect_url)

        memberships = ClubMembership.objects.filter(pk__in=ids, club=request.club).select_related("member")
        sent = failed = unreachable = 0
        for membership in memberships:
            email, sent_to_guardian = recipient_for(membership.member)
            if not email:
                unreachable += 1
                continue
            invoice = create_or_resend_invoice(membership, due_in_days=form.cleaned_data["due_in_days"], recipient_email=email, sent_to_guardian=sent_to_guardian)
            if send_invoice_email(invoice, request=request):
                sent += 1
            else:
                failed += 1

        if sent:
            notify(request, f"s|{_('Invoices sent')}|{_('%(count)d invoice(s) sent.') % {'count': sent}}")
        if failed:
            notify(request, f"w|{_('Some invoices could not be emailed')}|{_('%(count)d invoice(s) were recorded but the email could not be sent.') % {'count': failed}}")
        if unreachable:
            notify(request, f"w|{_('Some members have no email on file')}|{_('%(count)d member(s) have no email on file, on themselves or a parent/guardian, so no invoice was sent.') % {'count': unreachable}}")
        return redirect(redirect_url)


class MembershipSendInvoiceRemindersView(ClubAdminRequiredMixin, View):
    """The push-button "remind everyone past due" action -- every sent, unpaid
    invoice whose due date has passed, club-wide, regardless of the current list's
    filters or page. See club.services.invoicing.invoices_due_for_reminder."""

    def post(self, request):
        next_url = request.POST.get("next")
        redirect_url = next_url if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()) else reverse("management:membership_list")

        sent, failed = send_reminders(request.club, request=request)
        if not sent and not failed:
            notify(request, f"s|{_('Nothing to remind')}|{_('No overdue, unpaid invoices right now.')}")
        else:
            if sent:
                notify(request, f"s|{_('Reminders sent')}|{_('%(count)d reminder(s) sent.') % {'count': sent}}")
            if failed:
                notify(request, f"w|{_('Some reminders could not be emailed')}|{_('%(count)d reminder(s) failed to send.') % {'count': failed}}")
        return redirect(redirect_url)


class DuesInvoiceDetailView(ClubAdminRequiredMixin, DetailView):
    """A staff-facing view of one membership's invoice -- the same document the
    member/guardian received, viewable here for reference without re-sending it."""

    template_name = "management/dues_invoice_detail.html"
    context_object_name = "invoice"

    def get_object(self, queryset=None):
        return get_object_or_404(DuesInvoice, membership__pk=self.kwargs["pk"], club=self.request.club)

    def get_context_data(self, **kwargs):
        return super().get_context_data(membership=self.object.membership, member=self.object.membership.member, **kwargs)


class DuesInvoicePdfView(ClubAdminRequiredMixin, View):
    def get(self, request, pk):
        invoice = get_object_or_404(DuesInvoice, membership__pk=pk, club=request.club)

        try:
            pdf = invoice_pdf(invoice)
        except DuesInvoicePDFError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return redirect("management:membership_invoice_detail", pk=pk)

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
        return response


class MemberImportTemplateView(ClubStaffRequiredMixin, View):
    """Anyone with management access can download the template -- filling it in
    doesn't grant any authority, only the upload step (admin-only) does."""

    def get(self, request):
        workbook = build_member_import_template()
        response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = 'attachment; filename="member_import_template.xlsx"'
        workbook.save(response)
        return response


class MemberImportView(MemberAdminRequiredMixin, View):
    """Step 1 of the mass-upload: upload a filled-in template, see exactly what
    will be created before anything actually is. Row data -- already extracted to
    plain values, see bulk_import.read_member_import_workbook -- rides in the
    session to MemberImportConfirmView, which re-validates it rather than trusting
    anything a client could tamper with."""

    def get(self, request):
        return render(request, "management/member_import.html", {"form": MemberImportUploadForm()})

    def post(self, request):
        form = MemberImportUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, "management/member_import.html", {"form": form})

        try:
            rows = read_member_import_workbook(form.cleaned_data["file"])
        except ValueError as exc:
            form.add_error("file", str(exc))
            return render(request, "management/member_import.html", {"form": form})

        request.session["member_import_rows"] = rows
        results = parse_member_import_rows(rows, request.club)
        return render(
            request,
            "management/member_import_preview.html",
            {
                "results": results,
                "valid_count": sum(1 for result in results if result["member"] is not None),
                "skipped_count": sum(1 for result in results if result["member"] is None),
                "season": current_season(request.club),
            },
        )


class MemberImportConfirmView(MemberAdminRequiredMixin, View):
    """Step 2: no row data in the request at all, just a submit button -- there's
    nothing here for a client to tamper with. Re-parses the rows stashed in the
    session by MemberImportView, so what gets created is guaranteed to match what
    the preview showed."""

    def post(self, request):
        rows = request.session.pop("member_import_rows", None)
        if not rows:
            notify(request, f"w|{_('Nothing to import')}|{_('Upload a file first.')}")
            return redirect("management:member_import")

        results = parse_member_import_rows(rows, request.club)
        season = current_season(request.club)

        created = 0
        families_by_group = {}
        with transaction.atomic():
            for result in results:
                member = result["member"]
                if member is None:
                    continue

                family_role = result["family_role"]
                if family_role in (FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN) and member.email:
                    # Give the parent/guardian a login before saving, same as
                    # registering a family by hand -- get_or_create_login_user
                    # reuses an existing account for that email rather than
                    # risking a duplicate.
                    member.user, _unused = get_or_create_login_user(member.email)

                member.save()

                family_group = result["family_group"]
                if family_group:
                    family = families_by_group.get(family_group)
                    if family is None:
                        family = Family.objects.create()
                        families_by_group[family_group] = family
                    FamilyMembership.objects.create(family=family, member=member, role=family_role)
                elif family_role == FamilyMembership.FamilyRole.CHILD:
                    # A child with no family_group: nobody is on file for them yet.
                    # A family of their own is what makes that state visible -- it's
                    # what members.services.claims.families_awaiting_a_parent looks
                    # for, and what an approved claim adds the parent to.
                    FamilyMembership.objects.create(family=Family.objects.create(), member=member, role=family_role)

                if season is not None:
                    ClubMembership.objects.create(club=request.club, member=member, season=season, signed_up_at=timezone.localdate(), **result["membership_kwargs"])
                created += 1

        skipped = len(results) - created
        if season is None and created:
            title = _("%(count)s member(s) created, but not rostered") % {"count": created}
            body = _("There's no active season to sign them up for yet.")
            notify(request, f"w|{title}|{body}")
        else:
            body = _("%(created)s created, %(skipped)s skipped.") % {"created": created, "skipped": skipped}
            notify(request, f"s|{_('Members imported')}|{body}")
        return redirect("management:member_list")


class MemberCreateView(MemberAdminRequiredMixin, CreateView):
    model = Member
    form_class = MemberForm
    template_name = "management/member_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)

        # Adding a member here means adding them to *this* club -- without a
        # ClubMembership they'd never show up in members_visible_to() again.
        season = current_season(self.request.club)
        if season is not None:
            ClubMembership.objects.create(
                club=self.request.club,
                member=self.object,
                season=season,
                status=ClubMembership.StatusChoices.ACTIVE,
                signed_up_at=timezone.localdate(),
            )
            body = _("“%(member)s” added to the club for %(season)s.") % {"member": self.object, "season": season}
            notify(self.request, f"s|{_('Member added')}|{body}")
        else:
            body = _("“%(member)s” was created, but there's no active season to sign them up for yet.") % {"member": self.object}
            notify(self.request, f"w|{_('Member added, but not rostered')}|{body}")

        return response

    def get_success_url(self):
        return reverse("management:member_detail", args=[self.object.pk])


class MemberUpdateView(MemberAdminRequiredMixin, View):
    """Not a generic UpdateView: this page owns two forms on one submit -- the
    member's own fields, and (when they're actually rostered this season) their
    ClubMembership's license/status/fee status. A Member has no club of its own
    without one, so this is the only place to see or change it."""

    template_name = "management/member_form.html"

    def get_member(self):
        return get_object_or_404(members_visible_to(self.request.user, self.request.club, include_guardians=True), pk=self.kwargs["pk"])

    def get_membership(self, member):
        season = current_season(self.request.club)
        if season is None:
            return None
        return ClubMembership.objects.filter(club=self.request.club, member=member, season=season).first() or ClubMembership(club=self.request.club, member=member, season=season, signed_up_at=timezone.localdate())

    def render_form(self, member, form, membership_form):
        return render(self.request, self.template_name, {"object": member, "update_view": True, "form": form, "membership_form": membership_form})

    def get(self, request, pk):
        member = self.get_member()
        membership = self.get_membership(member)
        membership_form = ClubMembershipForm(instance=membership) if membership else None
        return self.render_form(member, MemberForm(instance=member), membership_form)

    def post(self, request, pk):
        member = self.get_member()
        membership = self.get_membership(member)
        form = MemberForm(request.POST, instance=member)
        membership_form = ClubMembershipForm(request.POST, instance=membership) if membership else None

        if form.is_valid() and (membership_form is None or membership_form.is_valid()):
            form.save()
            if membership_form is not None:
                membership_form.save()
            notify(request, f"s|{_('Member updated')}|{_('“%(member)s” updated.') % {'member': member}}")
            return redirect("management:member_detail", pk=member.pk)

        return self.render_form(member, form, membership_form)


class MemberDetailView(ClubStaffRequiredMixin, DetailView):
    template_name = "management/member_detail.html"
    context_object_name = "member"

    def get_queryset(self):
        return members_visible_to(self.request.user, self.request.club, include_guardians=True)

    def get_context_data(self, **kwargs):
        visible = members_visible_to(self.request.user, self.request.club, include_guardians=True)
        my_family_ids = FamilyMembership.objects.filter(member=self.object).values_list("family_id", flat=True)
        family_scoped_members = visible.filter(family_memberships__family_id__in=my_family_ids).distinct()
        family_groups, _ = group_by_family(family_scoped_members)

        is_admin = is_club_admin(self.request.user, self.request.club)
        referee_profile = RefereeProfile.objects.filter(member=self.object).select_related("level").first()
        season = current_season(self.request.club)
        current_membership = ClubMembership.objects.filter(club=self.request.club, member=self.object, season=season).first()

        # A guardian isn't rostered anywhere, so there's never an attendance
        # row to show -- same "member, not guardian" gate the fee-status row
        # above already uses.
        show_attendance = current_membership is not None and not current_membership.is_guardian
        attendance_sparkline = member_attendance_sparkline(self.object, season) if show_attendance else []
        attendance_counts = member_attendance_counts(self.object, season) if show_attendance else None

        return super().get_context_data(
            family_groups=family_groups,
            family_role_choices=FamilyMembership.FamilyRole.choices,
            add_child_form=AddChildForm(),
            add_parent_form=AddParentForm(),
            attach_to_family_form=AttachToFamilyForm(club=self.request.club, member=self.object),
            current_membership=current_membership,
            checklist=checklist_for(current_membership) if current_membership else [],
            membership_history=ClubMembership.objects.filter(club=self.request.club, member=self.object).select_related("season").order_by("-season__start_date"),
            # Non-empty only when `member` is a CHILD in some family -- that's the same
            # signal the Personal information card uses to decide whether to show parent
            # contact numbers at all.
            guardians=self.object.guardians,
            referee_profile=referee_profile,
            referee_eligibility_form=MemberRefereeEligibilityForm(club=self.request.club, member=self.object) if is_admin else None,
            show_attendance=show_attendance,
            attendance_sparkline=attendance_sparkline,
            attendance_counts=attendance_counts,
            **kwargs,
        )


class MemberAttachToFamilyView(MemberAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Add to family" modal on a standalone member's page."""

    form_class = AttachToFamilyForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:member_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def get_form_kwargs(self):
        member = get_object_or_404(members_visible_to(self.request.user, self.request.club, include_guardians=True), pk=self.kwargs["pk"])
        return super().get_form_kwargs() | {"club": self.request.club, "member": member}

    def form_valid(self, form):
        member = get_object_or_404(members_visible_to(self.request.user, self.request.club, include_guardians=True), pk=self.kwargs["pk"])
        family = attach_to_family(member, role=form.cleaned_data["role"], family=form.cleaned_data["family"])
        body = _("“%(member)s” is now part of %(family)s.") % {"member": member, "family": family}
        notify(self.request, f"s|{_('Added to family')}|{body}")
        return redirect("management:member_detail", pk=member.pk)


class MemberRefereeEligibilityUpdateView(MemberAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Referee eligibility" modal on a member's page --
    which teams' home games this member can be assigned to referee
    (teams.RefereeProfile). Admin-only, same as Roles/Positions/Groups."""

    form_class = MemberRefereeEligibilityForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:member_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def get_member(self):
        return get_object_or_404(members_visible_to(self.request.user, self.request.club, include_guardians=True), pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club, "member": self.get_member()}

    def form_valid(self, form):
        member = self.get_member()
        form.save()
        notify(self.request, f"s|{_('Referee eligibility updated')}|" + _("Updated which teams “%(member)s” can referee for.") % {"member": member})
        return redirect("management:member_detail", pk=member.pk)


class MemberGrantLoginView(MemberAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Grant login" modal on a login-less child's row in
    _family_members_table.html. The form itself (management.forms.GrantLoginForm)
    already rejects an email already in use, so form_valid only ever runs with a
    genuinely free one."""

    form_class = GrantLoginForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:member_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def form_valid(self, form):
        member = get_object_or_404(members_visible_to(self.request.user, self.request.club, include_guardians=True), pk=self.kwargs["pk"])
        if member.user_id is not None:
            # Already has one -- the row's button shouldn't have been there at all;
            # a direct POST replay (e.g. a resubmitted form) is the only way here.
            notify(self.request, f"w|{_('Already has a login')}|{_('“%(member)s” can already sign in.') % {'member': member}}")
        else:
            grant_login(member, form.cleaned_data["email"])
            notify(self.request, f"s|{_('Login granted')}|{_('“%(member)s” can now sign in.') % {'member': member}}")
        return redirect("management:member_detail", pk=member.pk)


class MemberDetachFromFamilyView(MemberAdminRequiredMixin, View):
    def post(self, request, pk, family_pk):
        member = get_object_or_404(members_visible_to(request.user, request.club, include_guardians=True), pk=pk)
        family = get_object_or_404(Family, pk=family_pk, memberships__member=member)
        # detach_from_family may delete `family` itself (left empty) -- str() it first,
        # since Family.__str__ queries self.memberships, which needs a pk to still exist.
        family_name = str(family)
        detach_from_family(member, family)
        body = _("“%(member)s” is no longer part of %(family)s.") % {"member": member, "family": family_name}
        notify(request, f"w|{_('Removed from family')}|{body}")
        return redirect("management:member_detail", pk=member.pk)


class FamilyMembershipRoleUpdateView(MemberAdminRequiredMixin, View):
    """Reclassify one person's role within one specific family -- from the inline
    dropdown in _family_members_table.html, reachable from both the member and
    family detail pages since that partial is shared between them. Lands back on
    whichever of those two pages the change came from: member_detail.html sends its
    own URL as `next` so the admin doesn't get bounced off the member they were
    looking at; family_detail.html sends nothing, since staying there is already
    correct."""

    def post(self, request, family_pk, member_pk):
        family = get_object_or_404(families_of_club(request.club), pk=family_pk)
        membership = get_object_or_404(FamilyMembership, family=family, member_id=member_pk)

        role = request.POST.get("role")
        if role not in FamilyMembership.FamilyRole.values:
            title = _("Couldn't update role")
            notify(request, f"e|{title}|{_('Not a valid role.')}")
        else:
            membership.role = role
            membership.save(update_fields=["role"])
            body = _("“%(member)s” is now %(role)s in %(family)s.") % {"member": membership.member, "role": membership.get_role_display(), "family": family}
            notify(request, f"s|{_('Role updated')}|{body}")

        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(next_url)
        return redirect("management:family_detail", pk=family.pk)


class MemberDeleteView(MemberAdminRequiredMixin, View):
    def post(self, request, pk):
        member = get_object_or_404(members_visible_to(request.user, request.club, include_guardians=True), pk=pk)
        name = str(member)
        # FamilyMembership cascades away with the member -- note which families
        # they were in before that happens, so an emptied one can be cleaned up
        # after, same as detach_from_family does.
        family_ids = list(Family.objects.filter(memberships__member=member).values_list("pk", flat=True))

        try:
            member.delete()
        except ProtectedError:
            title = _("Can't delete")
            body = _("“%(member)s” is still referenced by orders or invoices, and can't be deleted.") % {"member": name}
            notify(request, f"e|{title}|{body}")
            return redirect("management:member_detail", pk=pk)

        Family.objects.filter(pk__in=family_ids, memberships__isnull=True).delete()

        body = _("“%(member)s” has been deleted.") % {"member": name}
        notify(request, f"w|{_('Member deleted')}|{body}")
        return redirect("management:member_list")


# --- Teams (full tier) --------------------------------------------------------------


class TeamListView(ClubStaffRequiredMixin, ListView):
    """ADMIN sees every team; everyone else (coach, manager, other staff) only
    the teams they're staffed on this season -- same visibility rule as
    ``members_visible_to``, not the narrower management-only ``teams_managed_by``."""

    template_name = "management/team_list.html"
    context_object_name = "teams"
    paginate_by = 25

    def get_queryset(self):
        club = self.request.club
        teams = Team.objects.filter(club=club) if is_club_admin(self.request.user, club) else teams_staffed_by(self.request.user, club)
        search = self.request.GET.get("q", "").strip()
        if search:
            teams = teams.filter(name__icontains=search)

        season = current_season(club)
        # Explicit, not just Team.Meta's default -- an annotate() that aggregates
        # (the two Counts below) forces a GROUP BY, and Django doesn't apply a
        # model's default ordering to a grouped query (QuerySet.ordered), which
        # would otherwise make pagination's page split nondeterministic.
        return teams.annotate(
            player_count=Count("roster", filter=Q(roster__season=season), distinct=True),
            staff_count=Count("staff_assignments", filter=Q(staff_assignments__season=season), distinct=True),
        ).order_by("name")

    def get_context_data(self, **kwargs):
        return super().get_context_data(search=self.request.GET.get("q", ""), **kwargs)


class TeamCreateView(MemberAdminRequiredMixin, CreateView):
    model = Team
    form_class = TeamForm
    template_name = "management/team_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(team)s” created.") % {"team": self.object}
        notify(self.request, f"s|{_('Team created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:team_detail", args=[self.object.pk])


class TeamUpdateView(MemberAdminRequiredMixin, UpdateView):
    model = Team
    form_class = TeamForm
    template_name = "management/team_form.html"

    def get_queryset(self):
        return Team.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(team)s” updated.") % {"team": self.object}
        notify(self.request, f"s|{_('Team updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:team_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class TeamDeleteView(MemberAdminRequiredMixin, View):
    def post(self, request, pk):
        team = get_object_or_404(Team.objects.filter(club=request.club), pk=pk)
        name = str(team)
        # TeamMembership/StaffAssignment cascade away with the team -- no ProtectedError
        # to catch, unlike a Member (which orders/invoices can still reference).
        team.delete()

        body = _("“%(team)s” has been deleted.") % {"team": name}
        notify(request, f"w|{_('Team deleted')}|{body}")
        return redirect("management:team_list")


class TeamDetailView(ClubStaffRequiredMixin, DetailView):
    """Team, roster and staff for one season, all in one place -- viewing is open
    to any staff (visibility, not authority); managing the roster/staff of *this*
    team is gated by can_manage (TeamManagerRequiredMixin's own rule, computed
    here too since the template needs it to show/hide the add/edit/remove UI)."""

    template_name = "management/team_detail.html"
    context_object_name = "team"

    def get_queryset(self):
        return Team.objects.filter(club=self.request.club)

    def get_context_data(self, **kwargs):
        club = self.request.club
        team = self.object
        season = selected_season_from_request(self.request, club)
        can_manage = is_club_admin(self.request.user, club) or teams_managed_by(self.request.user, club).filter(pk=team.pk).exists()

        roster = TeamMembership.objects.none()
        staff = StaffAssignment.objects.none()
        attendance_rate = None
        top_attenders, bottom_attenders = [], []
        missed_practices = Member.objects.none()
        no_shows = []
        team_photo = TeamPhoto.objects.filter(team=team, season=season).first() if season is not None else None
        if season is not None:
            roster = list(TeamMembership.objects.filter(team=team, season=season).select_related("member", "position").order_by("position__ordering", "member__last_name"))
            staff = list(StaffAssignment.objects.filter(team=team, season=season).select_related("member", "position").order_by("position__ordering", "member__last_name"))
            if can_manage:
                for membership in roster:
                    membership.edit_form = TeamMembershipForm(instance=membership, club=club, team=team, season=season)
                for assignment in staff:
                    assignment.edit_form = StaffAssignmentForm(instance=assignment, club=club, team=team, season=season)

            attendance_rate = team_attendance_rate(team, season)
            rankings = player_attendance_rankings(team, season)
            top_attenders = rankings[:5]
            bottom_attenders = list(reversed(rankings))[:5]
            missed_practices = players_who_missed_recent_practices(team, season)
            no_shows = team_no_shows(team, season)[:10]

        return super().get_context_data(
            seasons=Season.objects.filter(club=club).order_by("-start_date"),
            selected_season=season,
            roster=roster,
            staff=staff,
            can_manage=can_manage,
            roster_form=TeamMembershipForm(club=club, team=team, season=season) if can_manage and season else None,
            staff_form=StaffAssignmentForm(club=club, team=team, season=season) if can_manage and season else None,
            team_photo=team_photo,
            team_photo_form=TeamPhotoForm(instance=team_photo) if can_manage and season else None,
            attendance_rate=attendance_rate,
            top_attenders=top_attenders,
            bottom_attenders=bottom_attenders,
            missed_practices=missed_practices,
            no_shows=no_shows,
            # None (not an empty queryset) signals "federation-managed" to the
            # template, distinct from "club-managed, nobody eligible yet".
            # Levels resolved to ids first, not a flat level__teams=team filter --
            # same reasoning as events.services.referees.eligible_referees, since
            # a level may qualify for this team only via what it inherits from.
            eligible_referees=(
                Member.objects.filter(
                    referee_profile__level_id__in=[level.pk for level in RefereeLevel.objects.filter(club=club) if team.pk in level.eligible_team_ids()], referee_profile__valid_until__gte=timezone.localdate()
                ).order_by("last_name", "first_name")
                if team.referee_management == Team.RefereeManagement.CLUB
                else None
            ),
            **kwargs,
        )


class TeamRosterAddView(TeamManagerRequiredMixin, FormView):
    """Reachable only via the "Add player" modal on the team page. Not
    RedirectOnInvalidMixin: that can't carry ?season= through a plain
    redirect(view_name, **kwargs), and losing the season on a failed add would
    land the admin back looking at a different one than they were editing."""

    form_class = TeamMembershipForm
    http_method_names = ["post"]

    def get_team(self):
        return get_object_or_404(Team.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_season(self):
        return get_object_or_404(Season.objects.filter(club=self.request.club), pk=self.kwargs["season_pk"])

    def get_form_kwargs(self):
        # instance carries team/season *before* validation runs -- TeamMembership.clean()
        # (validate_club_scope) needs self.team_id set to check season/position are the
        # same club's, and form_valid() runs only after that validation already passed.
        return super().get_form_kwargs() | {"club": self.request.club, "team": self.get_team(), "season": self.get_season(), "instance": TeamMembership(team=self.get_team(), season=self.get_season())}

    def team_detail_url(self):
        return f"{reverse('management:team_detail', args=[self.kwargs['pk']])}?season={self.kwargs['season_pk']}"

    def form_invalid(self, form):
        for error in form.errors.values():
            notify(self.request, f"e|{_('Could not add player')}|{' '.join(error)}")
        return redirect(self.team_detail_url())

    def form_valid(self, form):
        try:
            form.save()
        except IntegrityError:
            # Belt and braces: form.clean() already checks jersey-number and
            # member uniqueness by hand (team/season aren't form fields, so
            # Django's own validate_unique() can't see those constraints) --
            # this is the backstop for whatever that doesn't catch.
            notify(self.request, f"e|{_('Could not add player')}|{_('That player could not be added -- please check the details and try again.')}")
            return redirect(self.team_detail_url())

        body = _("“%(member)s” added to the roster.") % {"member": form.instance.member}
        notify(self.request, f"s|{_('Player added')}|{body}")
        return redirect(self.team_detail_url())


class TeamRosterUpdateView(TeamManagerRequiredMixin, FormView):
    form_class = TeamMembershipForm
    http_method_names = ["post"]

    def get_object(self):
        return get_object_or_404(TeamMembership.objects.filter(team__club=self.request.club, team__pk=self.kwargs["pk"]), pk=self.kwargs["membership_pk"])

    def get_team(self):
        return self.get_object().team

    def get_form_kwargs(self):
        membership = self.get_object()
        return super().get_form_kwargs() | {"instance": membership, "club": self.request.club, "team": membership.team, "season": membership.season}

    def team_detail_url(self):
        return f"{reverse('management:team_detail', args=[self.kwargs['pk']])}?season={self.get_object().season_id}"

    def form_invalid(self, form):
        for error in form.errors.values():
            notify(self.request, f"e|{_('Could not update player')}|{' '.join(error)}")
        return redirect(self.team_detail_url())

    def form_valid(self, form):
        try:
            form.save()
        except IntegrityError:
            notify(self.request, f"e|{_('Could not update player')}|{_('That change could not be saved -- please check the details and try again.')}")
            return redirect(self.team_detail_url())

        body = _("“%(member)s” updated.") % {"member": form.instance.member}
        notify(self.request, f"s|{_('Player updated')}|{body}")
        return redirect(self.team_detail_url())


class TeamRosterRemoveView(TeamManagerRequiredMixin, View):
    def get_team(self):
        return get_object_or_404(Team.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk, membership_pk):
        membership = get_object_or_404(TeamMembership.objects.filter(team__club=request.club, team__pk=pk), pk=membership_pk)
        season_id, member = membership.season_id, membership.member
        membership.delete()

        body = _("“%(member)s” removed from the roster.") % {"member": member}
        notify(request, f"w|{_('Player removed')}|{body}")
        return redirect(f"{reverse('management:team_detail', args=[pk])}?season={season_id}")


class TeamStaffAddView(TeamManagerRequiredMixin, FormView):
    form_class = StaffAssignmentForm
    http_method_names = ["post"]

    def get_team(self):
        return get_object_or_404(Team.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_season(self):
        return get_object_or_404(Season.objects.filter(club=self.request.club), pk=self.kwargs["season_pk"])

    def get_form_kwargs(self):
        # See TeamRosterAddView -- StaffAssignment.clean() needs team_id set before
        # validation runs, not after (form_valid() only runs once already valid).
        return super().get_form_kwargs() | {"club": self.request.club, "team": self.get_team(), "season": self.get_season(), "instance": StaffAssignment(team=self.get_team(), season=self.get_season())}

    def team_detail_url(self):
        return f"{reverse('management:team_detail', args=[self.kwargs['pk']])}?season={self.kwargs['season_pk']}"

    def form_invalid(self, form):
        for error in form.errors.values():
            notify(self.request, f"e|{_('Could not assign staff')}|{' '.join(error)}")
        return redirect(self.team_detail_url())

    def form_valid(self, form):
        try:
            form.save()
        except IntegrityError:
            notify(self.request, f"e|{_('Could not assign staff')}|{_('That assignment could not be saved -- please check the details and try again.')}")
            return redirect(self.team_detail_url())

        body = _("“%(member)s” assigned as staff.") % {"member": form.instance.member}
        notify(self.request, f"s|{_('Staff assigned')}|{body}")
        return redirect(self.team_detail_url())


class TeamStaffUpdateView(TeamManagerRequiredMixin, FormView):
    form_class = StaffAssignmentForm
    http_method_names = ["post"]

    def get_object(self):
        return get_object_or_404(StaffAssignment.objects.filter(team__club=self.request.club, team__pk=self.kwargs["pk"]), pk=self.kwargs["assignment_pk"])

    def get_team(self):
        return self.get_object().team

    def get_form_kwargs(self):
        assignment = self.get_object()
        return super().get_form_kwargs() | {"instance": assignment, "club": self.request.club, "team": assignment.team, "season": assignment.season}

    def team_detail_url(self):
        return f"{reverse('management:team_detail', args=[self.kwargs['pk']])}?season={self.get_object().season_id}"

    def form_invalid(self, form):
        for error in form.errors.values():
            notify(self.request, f"e|{_('Could not update staff assignment')}|{' '.join(error)}")
        return redirect(self.team_detail_url())

    def form_valid(self, form):
        try:
            form.save()
        except IntegrityError:
            notify(self.request, f"e|{_('Could not update staff assignment')}|{_('That change could not be saved -- please check the details and try again.')}")
            return redirect(self.team_detail_url())

        body = _("“%(member)s” updated.") % {"member": form.instance.member}
        notify(self.request, f"s|{_('Staff assignment updated')}|{body}")
        return redirect(self.team_detail_url())


class TeamStaffRemoveView(TeamManagerRequiredMixin, View):
    def get_team(self):
        return get_object_or_404(Team.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk, assignment_pk):
        assignment = get_object_or_404(StaffAssignment.objects.filter(team__club=request.club, team__pk=pk), pk=assignment_pk)
        season_id, member = assignment.season_id, assignment.member
        assignment.delete()

        body = _("“%(member)s” removed from staff.") % {"member": member}
        notify(request, f"w|{_('Staff removed')}|{body}")
        return redirect(f"{reverse('management:team_detail', args=[pk])}?season={season_id}")


class TeamBulkAddView(TeamManagerRequiredMixin, View):
    """Add many people to a team's roster and/or staff in one go -- the one-by-one
    modals (TeamRosterAddView / TeamStaffAddView) don't scale past a handful of
    names.

    One row per assignment: pick a person (searchable), pick a position, and the
    *position* decides what the row means -- a staff-flagged Position becomes a
    StaffAssignment, anything else a TeamMembership with an optional jersey
    number. Someone joining as both a player and staff (a playing coach) is two
    rows. Rows are added client-side from the formset's ``empty_form``; the
    earlier layout gave *every* eligible member a table row, which a club with a
    hundred-plus members can't realistically use.

    All-or-nothing: one bad row re-renders the page with every row the user typed
    still filled in and the offending field flagged, rather than saving the good
    rows and silently dropping the rest. Eligibility is recomputed server-side
    from eligible_roster_members -- a submitted member id that isn't actually
    eligible fails the field's own queryset lookup, never reaching the database.
    """

    template_name = "management/team_bulk_add.html"

    def get_team(self):
        return get_object_or_404(Team.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_season(self):
        return get_object_or_404(Season.objects.filter(club=self.request.club), pk=self.kwargs["season_pk"])

    def get_form_kwargs(self, team, season):
        club = self.request.club
        rostered_ids = set(TeamMembership.objects.filter(team=team, season=season).values_list("member_id", flat=True))
        staffed_ids = set(StaffAssignment.objects.filter(team=team, season=season).values_list("member_id", flat=True))

        # Built once here rather than per row: a ModelChoiceField re-runs its own
        # queryset for every form in the formset, so a twenty-row submit would
        # otherwise mean twenty full member queries.
        members = eligible_roster_members(club).order_by("last_name", "first_name")
        member_choices = [("", "---------")] + [(member.pk, bulk_add_member_label(member, rostered_ids=rostered_ids, staffed_ids=staffed_ids)) for member in members]

        return {
            "club": club,
            "team": team,
            "season": season,
            "member_choices": member_choices,
            "position_queryset": Position.objects.filter(club=club),
            "rostered_ids": rostered_ids,
            "staffed_ids": staffed_ids,
        }

    def render_form(self, request, team, season, formset):
        return render(request, self.template_name, {"team": team, "season": season, "formset": formset})

    def get(self, request, *args, **kwargs):
        team, season = self.get_team(), self.get_season()
        formset = TeamBulkAddFormSet(form_kwargs=self.get_form_kwargs(team, season))
        return self.render_form(request, team, season, formset)

    def post(self, request, *args, **kwargs):
        team, season = self.get_team(), self.get_season()
        formset = TeamBulkAddFormSet(request.POST, form_kwargs=self.get_form_kwargs(team, season))

        if not formset.is_valid():
            notify(request, f"e|{_('Could not add')}|{_('Some rows need attention -- see the errors below.')}")
            return self.render_form(request, team, season, formset)

        rows = [form.cleaned_data for form in formset.forms if form.cleaned_data.get("member") and form.cleaned_data.get("position")]
        if not rows:
            notify(request, f"i|{_('Nothing to add')}|{_('No one was selected.')}")
            return redirect(f"{reverse('management:team_detail', args=[team.pk])}?season={season.pk}")

        players_added = staff_added = 0
        try:
            with transaction.atomic():
                for row in rows:
                    if row["position"].staff_position:
                        StaffAssignment.objects.create(team=team, season=season, member=row["member"], position=row["position"])
                        staff_added += 1
                    else:
                        TeamMembership.objects.create(
                            team=team,
                            season=season,
                            member=row["member"],
                            position=row["position"],
                            jersey_number=row.get("jersey_number"),
                            is_captain=row.get("is_captain", False),
                            is_alternate_captain=row.get("is_alternate_captain", False),
                        )
                        players_added += 1
        except IntegrityError:
            # Someone else claimed a jersey number, or added the same person, between
            # validation and the write. The atomic block means nothing was saved, so
            # hand the whole form back rather than reporting a half-finished add.
            notify(request, f"e|{_('Could not add')}|{_('Someone changed this team while you were filling in the form. Nothing was saved -- please check the rows and try again.')}")
            return self.render_form(request, team, season, formset)

        body = _("%(players)s added as player(s), %(staff)s added as staff.") % {"players": players_added, "staff": staff_added}
        notify(request, f"s|{_('Team updated')}|{body}")
        return redirect(f"{reverse('management:team_detail', args=[team.pk])}?season={season.pk}")


class TeamPhotoSetView(TeamManagerRequiredMixin, FormView):
    """Reachable only via the "Upload"/"Replace" modal on the team page. Binds
    to the existing TeamPhoto for this team+season (if any) so re-uploading
    replaces it in place -- same "create or update the one row for this
    scope" pattern as controlpanel.views.ClubHomeLocationSetView. Not
    RedirectOnInvalidMixin, same reasoning as TeamRosterAddView: that can't
    carry ?season= through a plain redirect(view_name, **kwargs)."""

    form_class = TeamPhotoForm
    http_method_names = ["post"]

    def get_team(self):
        return get_object_or_404(Team.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_season(self):
        return get_object_or_404(Season.objects.filter(club=self.request.club), pk=self.kwargs["season_pk"])

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"instance": TeamPhoto.objects.filter(team=self.get_team(), season=self.get_season()).first()}

    def team_detail_url(self):
        return f"{reverse('management:team_detail', args=[self.kwargs['pk']])}?season={self.kwargs['season_pk']}"

    def form_invalid(self, form):
        for error in form.errors.values():
            notify(self.request, f"e|{_('Could not upload photo')}|{' '.join(error)}")
        return redirect(self.team_detail_url())

    def form_valid(self, form):
        photo = form.save(commit=False)
        photo.team = self.get_team()
        photo.season = self.get_season()
        photo.save()

        notify(self.request, f"s|{_('Photo uploaded')}|{_('Team photo updated.')}")
        return redirect(self.team_detail_url())


class TeamPhotoDeleteView(TeamManagerRequiredMixin, View):
    def get_team(self):
        return get_object_or_404(Team.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk, season_pk):
        team = self.get_team()
        TeamPhoto.objects.filter(team=team, season_id=season_pk).delete()

        notify(request, f"w|{_('Photo removed')}|{_('Team photo removed.')}")
        return redirect(f"{reverse('management:team_detail', args=[pk])}?season={season_pk}")


# --- Club roles (full tier: assign / revoke, no update -- a role isn't edited, just
# granted or taken away) -------------------------------------------------------------


#: What each non-default role actually grants -- shown on the roles overview so an
#: admin granting one knows what they're handing out. See club/services/access.py.
ROLE_DESCRIPTIONS = {
    ClubRole.Roles.ADMIN: _("Full control over the club: memberships, positions, roles, teams, shop, every event, and news."),
    ClubRole.Roles.EDITOR: _("Can create and edit events, and publish news items, but not memberships, positions, roles, or shop settings."),
}


class ClubRoleListView(ClubAdminRequiredMixin, ListView):
    """Every non-default role holder, grouped by role. The plain MEMBER role is
    excluded entirely -- every active club member holds it automatically
    (club/signals.py), so listing it here would just be noise; this page is for
    the roles someone was actually *granted*. ClubRole.Roles has few enough
    values that a section per role reads better than one flat table."""

    template_name = "management/role_list.html"
    context_object_name = "roles"

    def get_queryset(self):
        return ClubRole.objects.filter(club=self.request.club).exclude(role=ClubRole.Roles.MEMBER).select_related("member")

    def get_context_data(self, **kwargs):
        sections = [(value, label, ROLE_DESCRIPTIONS.get(value, ""), [role for role in self.object_list if role.role == value]) for value, label in ClubRole.Roles.choices if value != ClubRole.Roles.MEMBER]
        return super().get_context_data(sections=sections, role_form=ClubRoleAssignForm(club=self.request.club), **kwargs)


class ClubRoleCreateView(ClubAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Grant role" modal on the roles overview."""

    form_class = ClubRoleAssignForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:role_list"

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club}

    def form_valid(self, form):
        # A member holds at most one ClubRole per club (the membership-status sync in
        # club/signals.py already gave any active member an implicit MEMBER role) --
        # so granting ADMIN/EDITOR promotes that existing row rather than inserting a
        # second one, exactly like controlpanel.services.admins.grant_club_admin.
        member, role = form.cleaned_data["member"], form.cleaned_data["role"]
        role_obj, created = ClubRole.objects.get_or_create(club=self.request.club, member=member, defaults={"role": role})
        if not created and role_obj.role != role:
            role_obj.role = role
            role_obj.save(update_fields=["role"])

        body = _("“%(member)s” is now %(role)s.") % {"member": member, "role": role_obj.get_role_display()}
        notify(self.request, f"s|{_('Role granted')}|{body}")
        return redirect("management:role_list")


class ClubRoleRevokeView(ClubAdminRequiredMixin, View):
    def post(self, request, pk):
        role = get_object_or_404(ClubRole, pk=pk, club=request.club)
        member, role_label = role.member, role.get_role_display()
        role.delete()
        body = _("“%(member)s” is no longer %(role)s.") % {"member": member, "role": role_label}
        notify(request, f"w|{_('Role revoked')}|{body}")
        return redirect("management:role_list")


# --- Everything else: correctly scoped and gated, but list-only for now ------------


class StubListMixin:
    """Shared shape for a placeholder list: proves out the query scoping and the
    permission gate for an entity that doesn't have full CRUD yet -- the actual
    create/edit UI is a follow-up, not part of this scaffold."""

    template_name = "management/_generic_list.html"
    page_title = ""

    def get_context_data(self, **kwargs):
        return super().get_context_data(page_title=self.page_title, **kwargs)


def families_of_club(club):
    return Family.objects.filter(memberships__member__member_of__club=club).distinct()


class FamilyListView(ClubStaffRequiredMixin, ListView):
    """One row per family -- parents/guardians in one column, children in
    another -- for browsing what's on file rather than reaching a family only
    by clicking through a member. Same visibility rule as FamilyDetailView's
    own guardians/children (group_by_family over members_visible_to), so a
    non-admin never sees a family, or a family-mate within one, they couldn't
    already reach some other way; the family list itself is narrowed to only
    families with at least one such visible member, rather than showing empty
    rows for the rest."""

    template_name = "management/family_list.html"
    context_object_name = "families"
    paginate_by = 25

    def get_queryset(self):
        visible = members_visible_to(self.request.user, self.request.club, include_guardians=True)

        # ?q= matches a first or last name of any member on the family --
        # parent/guardian or child alike, since the person being searched for
        # could be either. Narrowing the already-visible set first (rather than
        # filtering families on a second, independent membership join) means a
        # match still respects the same visibility rule as the unfiltered list.
        search = self.request.GET.get("q", "").strip()
        if search:
            visible = visible.filter(Q(first_name__icontains=search) | Q(last_name__icontains=search))

        return families_of_club(self.request.club).filter(memberships__member__in=visible).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(search=self.request.GET.get("q", ""), **kwargs)
        families = list(context["families"])

        visible = members_visible_to(self.request.user, self.request.club, include_guardians=True)
        groups, _ungrouped = group_by_family(visible.filter(family_memberships__family__in=families))
        groups_by_family = {group["family"]: group for group in groups}
        for family in families:
            group = groups_by_family.get(family, {"guardians": [], "children": []})
            family.guardians_display = group["guardians"]
            family.children_display = group["children"]

        return context | {"families": families}


class FamilyCreateView(MemberAdminRequiredMixin, FormView):
    """One new family in one go: a parent (who gets a login) and a child (who
    doesn't) -- see members.services.family.register_family."""

    form_class = FamilyCreateForm
    template_name = "management/family_form.html"

    def form_valid(self, form):
        cd = form.cleaned_data
        season = current_season(self.request.club)
        self.family = register_family(
            self.request.club,
            season,
            parent_email=cd["parent_email"],
            parent_first_name=cd["parent_first_name"],
            parent_last_name=cd["parent_last_name"],
            child_first_name=cd["child_first_name"],
            child_last_name=cd["child_last_name"],
            child_date_of_birth=cd["child_date_of_birth"],
            parent_is_member=cd["parent_is_member"],
        )

        if season is not None:
            body = _("%(family)s added to the club for %(season)s.") % {"family": self.family, "season": season}
            notify(self.request, f"s|{_('Family added')}|{body}")
        else:
            body = _("%(family)s was created, but there's no active season to sign them up for yet.") % {"family": self.family}
            notify(self.request, f"w|{_('Family added, but not rostered')}|{body}")

        return super().form_valid(form)

    def get_success_url(self):
        return reverse("management:family_detail", args=[self.family.pk])


class FamilyDetailView(ClubStaffRequiredMixin, DetailView):
    """The family overview: everyone in it, plus the add-parent/add-child actions.
    Reached by clicking a family's name wherever one is shown (the member list's
    Family column, another member's own Family panel) -- there's no standalone
    "Families" nav entry; family is something you see through Members."""

    template_name = "management/family_detail.html"
    context_object_name = "family"

    def get_queryset(self):
        return families_of_club(self.request.club)

    def get_context_data(self, **kwargs):
        visible = members_visible_to(self.request.user, self.request.club, include_guardians=True)
        members = visible.filter(family_memberships__family=self.object).distinct()
        # group_by_family scopes by member, not family -- a member visible here
        # because they're in *this* family can also belong to another one, in
        # which case groups has more than one entry. groups[0] would then pick
        # whichever family happened to sort first, not necessarily this page's own.
        groups, _ = group_by_family(members)
        group = next((g for g in groups if g["family"] == self.object), None) or {"family": self.object, "guardians": [], "children": [], "others": [], "all": []}

        return super().get_context_data(
            group=group,
            family_role_choices=FamilyMembership.FamilyRole.choices,
            add_child_form=AddChildForm(),
            add_parent_form=AddParentForm(),
            **kwargs,
        )


class FamilyAddChildView(MemberAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Add child" modal on the family overview / member
    detail pages -- a family that needs one more child registered, most often a
    sibling joining."""

    form_class = AddChildForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:family_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def form_valid(self, form):
        family = get_object_or_404(families_of_club(self.request.club), pk=self.kwargs["pk"])
        child = add_child_to_family(self.request.club, current_season(self.request.club), family, **form.cleaned_data)
        body = _("“%(child)s” added to %(family)s.") % {"child": child, "family": family}
        notify(self.request, f"s|{_('Child registered')}|{body}")
        return redirect("management:family_detail", pk=family.pk)


class FamilyAddParentView(MemberAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Add parent" modal on the family overview / member
    detail pages -- a family that needs one more parent/guardian registered."""

    form_class = AddParentForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:family_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def form_valid(self, form):
        family = get_object_or_404(families_of_club(self.request.club), pk=self.kwargs["pk"])
        parent = add_parent_to_family(self.request.club, current_season(self.request.club), family, **form.cleaned_data)
        body = _("“%(parent)s” added to %(family)s.") % {"parent": parent, "family": family}
        notify(self.request, f"s|{_('Parent registered')}|{body}")
        return redirect("management:family_detail", pk=family.pk)


class ParentClaimListView(MemberAdminRequiredMixin, ListView):
    """The review queue for parents asking to be linked to a child.

    Approving is a human decision on purpose -- see members.models.ParentClaim.
    Each pending claim is shown with what the parent typed *and* a shortlist of
    children who have nobody on file, so the admin matches rather than searches.
    """

    template_name = "management/parent_claim_list.html"
    context_object_name = "claims"

    def get_queryset(self):
        return ParentClaim.objects.filter(club=self.request.club).select_related("child", "reviewed_by")

    def get_context_data(self, **kwargs):
        claims = list(self.get_queryset())
        pending = [claim for claim in claims if claim.is_pending]
        for claim in pending:
            candidates = suggested_children(claim)
            initial = {"child": candidates[0].pk} if candidates else None
            claim.review_form = ClaimReviewForm(candidates=Member.objects.filter(pk__in=[child.pk for child in candidates]), initial=initial)
            claim.has_candidates = bool(candidates)
            claim.reject_form = ClaimRejectForm()

        # Last season's history is clutter, not context -- only what was reviewed
        # within the club's current season stays in view. No current season (a
        # club between seasons) means nothing qualifies, rather than erroring.
        season = current_season(self.request.club)
        reviewed = self.get_queryset().exclude(status=ParentClaim.Status.PENDING).filter(reviewed_at__date__gte=season.start_date) if season is not None else ParentClaim.objects.none()

        return super().get_context_data(
            pending=pending,
            reviewed=reviewed,
            awaiting_a_parent=children_awaiting_a_parent(self.request.club).order_by("last_name", "first_name"),
            **kwargs,
        )


class ParentClaimApproveView(MemberAdminRequiredMixin, View):
    """Link the claim's parent to the chosen child. The parent lands as a
    guardian, not a member -- see members.services.claims.approve_claim."""

    def post(self, request, pk):
        claim = get_object_or_404(ParentClaim.objects.filter(club=request.club), pk=pk)
        form = ClaimReviewForm(request.POST, candidates=children_awaiting_a_parent(request.club))
        if not form.is_valid():
            notify(request, f"e|{_('Could not approve')}|{_('Choose which child this claim refers to.')}")
            return redirect("management:parent_claim_list")

        reviewer = Member.objects.filter(user=request.user).first()
        try:
            approve_claim(claim, child=form.cleaned_data["child"], season=current_season(request.club), reviewed_by=reviewer)
        except ClaimError as error:
            notify(request, f"e|{_('Could not approve')}|{error}")
        else:
            child = form.cleaned_data["child"]
            emailed = send_claim_approved_email(claim, child=child, request=request)
            if emailed:
                body = _("“%(parent)s” is now linked to %(child)s. They've been emailed a link to set their password.") % {"parent": claim.parent_name, "child": child}
                notify(request, f"s|{_('Claim approved')}|{body}")
            else:
                # The link is made either way -- say so plainly rather than letting
                # the club assume the parent has been told.
                body = _("“%(parent)s” is now linked to %(child)s, but the email could not be sent. Ask them to use “Forgot your password?” on the sign-in page.") % {"parent": claim.parent_name, "child": child}
                notify(request, f"w|{_('Claim approved, email not sent')}|{body}")
        return redirect("management:parent_claim_list")


class ParentClaimRejectView(MemberAdminRequiredMixin, View):
    def post(self, request, pk):
        claim = get_object_or_404(ParentClaim.objects.filter(club=request.club), pk=pk)
        reviewer = Member.objects.filter(user=request.user).first()
        try:
            reject_claim(claim, reviewed_by=reviewer, note=request.POST.get("note", "").strip())
        except ClaimError as error:
            notify(request, f"e|{_('Could not reject')}|{error}")
        else:
            notify(request, f"w|{_('Claim rejected')}|" + _("“%(parent)s” was not linked.") % {"parent": claim.parent_name})
        return redirect("management:parent_claim_list")


class PositionListView(ClubStaffRequiredMixin, ListView):
    """Visible to any staff (coaches need to see positions to make sense of a
    roster); creating/editing positions is still ADMIN-only, gated in the
    template and on PositionCreateView/PositionUpdateView themselves."""

    template_name = "management/position_list.html"
    context_object_name = "positions"

    def get_queryset(self):
        return Position.objects.filter(club=self.request.club)


class PositionCreateView(ClubAdminRequiredMixin, CreateView):
    model = Position
    form_class = PositionForm
    template_name = "management/position_form.html"

    def form_valid(self, form):
        form.instance.club = self.request.club
        response = super().form_valid(form)
        body = _("“%(position)s” created.") % {"position": self.object}
        notify(self.request, f"s|{_('Position created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:position_list")


class PositionUpdateView(ClubAdminRequiredMixin, UpdateView):
    model = Position
    form_class = PositionForm
    template_name = "management/position_form.html"

    def get_queryset(self):
        return Position.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(position)s” updated.") % {"position": self.object}
        notify(self.request, f"s|{_('Position updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:position_list")

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class RefereeLevelListView(ClubStaffRequiredMixin, ListView):
    """Visible to any staff, same reasoning as PositionListView; creating/
    editing a level is admin-only."""

    template_name = "management/referee_level_list.html"
    context_object_name = "levels"

    def get_queryset(self):
        return RefereeLevel.objects.filter(club=self.request.club).select_related("inherits_from").prefetch_related("teams")


class RefereeLevelCreateView(MemberAdminRequiredMixin, CreateView):
    model = RefereeLevel
    form_class = RefereeLevelForm
    template_name = "management/referee_level_form.html"

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club}

    def form_valid(self, form):
        form.instance.club = self.request.club
        response = super().form_valid(form)
        body = _("“%(level)s” created.") % {"level": self.object}
        notify(self.request, f"s|{_('Referee level created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:referee_level_list")


class RefereeLevelUpdateView(MemberAdminRequiredMixin, UpdateView):
    model = RefereeLevel
    form_class = RefereeLevelForm
    template_name = "management/referee_level_form.html"

    def get_queryset(self):
        return RefereeLevel.objects.filter(club=self.request.club)

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club}

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(level)s” updated.") % {"level": self.object}
        notify(self.request, f"s|{_('Referee level updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:referee_level_list")

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class RefereeListView(ClubStaffRequiredMixin, ListView):
    """Every referee in the club, at a glance: level, eligible teams, validity
    -- see teams.RefereeProfile. Read-only; editing happens on the member's
    own page (MemberRefereeEligibilityUpdateView)."""

    template_name = "management/referee_list.html"
    context_object_name = "referees"

    def get_queryset(self):
        # No prefetch for eligible_teams below select_related's level: it walks the
        # level's own inherits_from chain (RefereeLevel.eligible_team_ids), which a
        # single prefetch_related path can't cover anyway.
        members = members_visible_to(self.request.user, self.request.club, include_guardians=True).filter(referee_profile__isnull=False)
        return members.select_related("referee_profile", "referee_profile__level").order_by("last_name", "first_name")


# --- Groups: a generic named collection of members (all coaches, all team managers,
# a referee pool, ...) -- admin-only, like Positions/Roles above -------------------


class GroupListView(MemberAdminRequiredMixin, ListView):
    template_name = "management/group_list.html"
    context_object_name = "groups"
    paginate_by = 25

    def get_queryset(self):
        # order_by explicit for the same reason as TeamListView: the Count()
        # annotation forces a GROUP BY, which Django doesn't apply the model's
        # default ordering to -- pagination needs a real order to split on.
        return Group.objects.filter(club=self.request.club).annotate(member_count=Count("memberships", distinct=True)).order_by("name")


class GroupCreateView(MemberAdminRequiredMixin, CreateView):
    model = Group
    form_class = GroupForm
    template_name = "management/group_form.html"

    def form_valid(self, form):
        form.instance.club = self.request.club
        response = super().form_valid(form)
        body = _("“%(group)s” created.") % {"group": self.object}
        notify(self.request, f"s|{_('Group created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:group_detail", args=[self.object.pk])


class GroupUpdateView(MemberAdminRequiredMixin, UpdateView):
    model = Group
    form_class = GroupForm
    template_name = "management/group_form.html"

    def get_queryset(self):
        return Group.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(group)s” updated.") % {"group": self.object}
        notify(self.request, f"s|{_('Group updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:group_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class GroupDeleteView(MemberAdminRequiredMixin, View):
    def post(self, request, pk):
        group = get_object_or_404(Group.objects.filter(club=request.club), pk=pk)
        name = str(group)
        group.delete()
        notify(request, f"w|{_('Group deleted')}|" + _("“%(group)s” deleted.") % {"group": name})
        return redirect("management:group_list")


class GroupDetailView(MemberAdminRequiredMixin, DetailView):
    template_name = "management/group_detail.html"
    context_object_name = "group"

    def get_queryset(self):
        return Group.objects.filter(club=self.request.club)

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            memberships=GroupMembership.objects.filter(group=self.object).select_related("member"),
            **kwargs,
        )


class GroupBulkAddView(MemberAdminRequiredMixin, View):
    """Add many members to a group in one go -- mirrors TeamBulkAddView's
    searchable-row formset, minus the position/jersey columns (group membership
    carries no per-member attributes)."""

    template_name = "management/group_bulk_add.html"

    def get_group(self):
        return get_object_or_404(Group.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_form_kwargs(self, group):
        existing_ids = set(GroupMembership.objects.filter(group=group).values_list("member_id", flat=True))
        members = members_visible_to(self.request.user, self.request.club, include_guardians=True).order_by("last_name", "first_name")
        # One member query for the whole formset -- see TeamBulkAddView.get_form_kwargs.
        member_choices = [("", "---------")] + [(member.pk, _("%(member)s — already in this group") % {"member": member} if member.pk in existing_ids else str(member)) for member in members]

        return {"member_queryset": members, "member_choices": member_choices, "existing_ids": existing_ids}

    def render_form(self, request, group, formset):
        return render(request, self.template_name, {"group": group, "formset": formset})

    def get(self, request, *args, **kwargs):
        group = self.get_group()
        formset = GroupBulkAddFormSet(form_kwargs=self.get_form_kwargs(group))
        return self.render_form(request, group, formset)

    def post(self, request, *args, **kwargs):
        group = self.get_group()
        formset = GroupBulkAddFormSet(request.POST, form_kwargs=self.get_form_kwargs(group))

        if not formset.is_valid():
            notify(request, f"e|{_('Could not add')}|{_('Some rows need attention -- see the errors below.')}")
            return self.render_form(request, group, formset)

        members = [form.cleaned_data["member"] for form in formset.forms if form.cleaned_data.get("member")]
        if not members:
            notify(request, f"i|{_('Nothing to add')}|{_('No one was selected.')}")
            return redirect("management:group_detail", pk=group.pk)

        with transaction.atomic():
            GroupMembership.objects.bulk_create([GroupMembership(group=group, member=member) for member in members])

        notify(request, f"s|{_('Group updated')}|" + _("%(count)s member(s) added to “%(group)s”.") % {"count": len(members), "group": group})
        return redirect("management:group_detail", pk=group.pk)


class GroupMemberRemoveView(MemberAdminRequiredMixin, View):
    def post(self, request, pk, membership_pk):
        group = get_object_or_404(Group.objects.filter(club=request.club), pk=pk)
        membership = get_object_or_404(GroupMembership.objects.filter(group=group), pk=membership_pk)
        member = membership.member
        membership.delete()
        notify(request, f"w|{_('Removed from group')}|" + _("“%(member)s” removed from “%(group)s”.") % {"member": member, "group": group})
        return redirect("management:group_detail", pk=group.pk)


# --- News: draft/edit is broad (any coach_manager/editor/admin), but only EDITOR/ADMIN
# may publish -- the release flow the news app exists for ---------------------------


class NewsListView(ClubStaffRequiredMixin, ListView):
    """The D8 three-pane page: a filterable list on the left (status chips --
    all/draft/scheduled/published) and, on the right, a preview of whichever
    item is selected (``?selected=<pk>``, defaulting to the first row of
    whatever's currently listed so the pane is never empty). news_detail.html
    stays a separate, unchanged permalink page for anywhere else that links
    straight to one news item; both share _news_preview.html so the actual
    article/photos/publish markup exists in exactly one place."""

    template_name = "management/news_list.html"
    context_object_name = "news_items"
    paginate_by = 20

    def get_queryset(self):
        queryset = News.objects.filter(club=self.request.club).select_related("created_by").prefetch_related("teams")
        status_filter = self.request.GET.get("status", "all")
        now = timezone.now()
        if status_filter == "draft":
            queryset = queryset.filter(status=News.Status.DRAFT)
        elif status_filter == "scheduled":
            queryset = queryset.filter(status=News.Status.PUBLISHED, published_at__gt=now)
        elif status_filter == "published":
            queryset = queryset.filter(status=News.Status.PUBLISHED, published_at__lte=now)
        return queryset

    def get_context_data(self, **kwargs):
        club, user = self.request.club, self.request.user
        base = News.objects.filter(club=club)
        now = timezone.now()

        for news_item in self.object_list:
            news_item.can_edit = can_edit_news(user, news_item)

        selected_pk = self.request.GET.get("selected")
        selected_item = None
        if selected_pk:
            selected_item = News.objects.filter(club=club, pk=selected_pk).select_related("created_by").prefetch_related("teams", "photos").first()
        if selected_item is None and self.object_list:
            selected_item = self.object_list[0]
        if selected_item is not None:
            selected_item.can_edit = can_edit_news(user, selected_item)

        return super().get_context_data(
            status_filter=self.request.GET.get("status", "all"),
            counts={
                "all": base.count(),
                "draft": base.filter(status=News.Status.DRAFT).count(),
                "scheduled": base.filter(status=News.Status.PUBLISHED, published_at__gt=now).count(),
                "published": base.filter(status=News.Status.PUBLISHED, published_at__lte=now).count(),
            },
            news_item=selected_item,
            can_edit=selected_item.can_edit if selected_item else False,
            can_publish=can_publish_news(user, club),
            publish_form=NewsPublishForm(),
            photo_upload_form=NewsPhotoUploadForm(),
            **kwargs,
        )


class NewsCreateView(NewsAuthorRequiredMixin, CreateView):
    model = News
    form_class = NewsForm
    template_name = "management/news_form.html"

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club}

    def form_valid(self, form):
        form.instance.club = self.request.club
        form.instance.created_by = Member.objects.filter(user=self.request.user).first()
        response = super().form_valid(form)
        body = _("“%(news)s” created.") % {"news": self.object}
        notify(self.request, f"s|{_('News item created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:news_detail", args=[self.object.pk])


class NewsDetailView(ClubStaffRequiredMixin, DetailView):
    template_name = "management/news_detail.html"
    context_object_name = "news_item"

    def get_queryset(self):
        return News.objects.filter(club=self.request.club).prefetch_related("teams", "photos")

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            can_edit=can_edit_news(self.request.user, self.object),
            can_publish=can_publish_news(self.request.user, self.request.club),
            publish_form=NewsPublishForm(),
            photo_upload_form=NewsPhotoUploadForm(),
            **kwargs,
        )


class NewsUpdateView(NewsEditRequiredMixin, UpdateView):
    model = News
    form_class = NewsForm
    template_name = "management/news_form.html"

    def get_news_item(self):
        return get_object_or_404(News.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_queryset(self):
        return News.objects.filter(club=self.request.club)

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club}

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(news)s” updated.") % {"news": self.object}
        notify(self.request, f"s|{_('News item updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:news_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class NewsDeleteView(NewsEditRequiredMixin, View):
    def get_news_item(self):
        return get_object_or_404(News.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk):
        news_item = self.get_news_item()
        title = str(news_item)
        news_item.delete()

        body = _("“%(news)s” has been deleted.") % {"news": title}
        notify(request, f"w|{_('News item deleted')}|{body}")
        return redirect("management:news_list")


class NewsPublishView(NewsPublisherRequiredMixin, RedirectOnInvalidMixin, FormView):
    form_class = NewsPublishForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:news_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def form_valid(self, form):
        news_item = get_object_or_404(News.objects.filter(club=self.request.club), pk=self.kwargs["pk"])
        news_item.publish(at=form.cleaned_data["published_at"])

        if form.cleaned_data["notify_members"]:
            # eta in the past (the common, "publish now" case) just runs right
            # away -- see news.tasks' own module docstring for why there's no
            # separate immediate/scheduled branch here.
            notify_news_published.apply_async(args=[str(news_item.pk)], eta=news_item.published_at)

        if news_item.is_scheduled:
            body = _("“%(news)s” is scheduled to go live on %(date)s.") % {"news": news_item, "date": news_item.published_at}
        else:
            body = _("“%(news)s” is now live.") % {"news": news_item}
        notify(self.request, f"s|{_('News item published')}|{body}")
        return redirect("management:news_detail", pk=news_item.pk)


class NewsUnpublishView(NewsPublisherRequiredMixin, View):
    def post(self, request, pk):
        news_item = get_object_or_404(News.objects.filter(club=request.club), pk=pk)
        news_item.unpublish()
        body = _("“%(news)s” is back to a draft.") % {"news": news_item}
        notify(request, f"w|{_('News item unpublished')}|{body}")
        return redirect("management:news_detail", pk=news_item.pk)


class NewsPhotoUploadView(NewsEditRequiredMixin, RedirectOnInvalidMixin, FormView):
    """One NewsPhoto per uploaded file; if the item had none yet, the first one
    in this batch is auto-flagged main so there's always one once any photo exists."""

    form_class = NewsPhotoUploadForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:news_detail"

    def get_news_item(self):
        return get_object_or_404(News.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def form_valid(self, form):
        news_item = self.get_news_item()
        has_main = news_item.photos.filter(is_main=True).exists()

        count = 0
        for image in form.cleaned_data["images"]:
            NewsPhoto.objects.create(news_item=news_item, image=image, is_main=not has_main)
            has_main = True
            count += 1

        body = ngettext("%(count)d photo added.", "%(count)d photos added.", count) % {"count": count}
        notify(self.request, f"s|{_('Photos added')}|{body}")
        return redirect("management:news_detail", pk=news_item.pk)


class NewsPhotoSetMainView(NewsEditRequiredMixin, View):
    def get_news_item(self):
        return get_object_or_404(News.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk, photo_pk):
        news_item = self.get_news_item()
        photo = get_object_or_404(NewsPhoto, pk=photo_pk, news_item=news_item)

        with transaction.atomic():
            NewsPhoto.objects.filter(news_item=news_item).update(is_main=False)
            photo.is_main = True
            photo.save(update_fields=["is_main"])

        return redirect("management:news_detail", pk=news_item.pk)


class NewsPhotoDeleteView(NewsEditRequiredMixin, View):
    def get_news_item(self):
        return get_object_or_404(News.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk, photo_pk):
        news_item = self.get_news_item()
        photo = get_object_or_404(NewsPhoto, pk=photo_pk, news_item=news_item)
        was_main = photo.is_main

        with transaction.atomic():
            photo.delete()
            if was_main:
                replacement = news_item.photos.first()
                if replacement is not None:
                    replacement.is_main = True
                    replacement.save(update_fields=["is_main"])

        notify(request, f"w|{_('Photo removed')}|{_('The photo was removed.')}")
        return redirect("management:news_detail", pk=news_item.pk)


def scoped_to_managed_teams(queryset, user, club):
    """Non-admin: only rows for a team they manage or a group they belong to,
    plus rows tied to neither (a club-wide social, an AGM) -- there's nothing
    to scope those to, so they stay visible to everyone. Same "manages"/
    "belongs to" rules as who can create against a given team/group
    (teams_managed_by/groups_manageable_by), not the broader "staffed on any
    role" one. Works for any queryset whose model has `teams`/`groups` M2Ms --
    Event and EventSeries both do -- so it backs the events list, the
    dashboard's upcoming-events widget, and both detail views (an
    out-of-scope one 404s if opened directly, same as any other
    queryset-scoped detail view here, not just unlisted)."""
    if is_club_admin(user, club):
        return queryset
    managed_team_ids = teams_managed_by(user, club).values_list("pk", flat=True)
    member_group_ids = groups_manageable_by(user, club).values_list("pk", flat=True)
    return queryset.filter(Q(teams__in=managed_team_ids) | Q(groups__in=member_group_ids) | Q(teams__isnull=True, groups__isnull=True)).distinct()


class EventListView(ClubStaffRequiredMixin, ListView):
    """The D5-alike calendar (``?view=calendar``, the default) plus the
    original table (``?view=list``) this page used to be exclusively --
    the List toggle in the template switches between the two without
    changing URL. Calendar mode further switches between Week/Month/Season
    via ``?range=``, navigated with ``?date=<iso date>`` (week/month) or the
    existing ``?season=<pk>`` selector (season range, matching every other
    season-scoped page).

    List mode is upcoming-by-default (what a coach actually opens this page
    to check); ``?show_past=1`` flips it to the most recent past events
    instead, and it alone paginates -- a calendar view always shows its whole
    window at once, see get_paginate_by. Calendar mode's season filtering
    mirrors Event.season's own "explicit, else derived from start date" rule
    (events/models.py) rather than requiring a stored season on every row."""

    template_name = "management/event_list.html"
    context_object_name = "events"
    paginate_by = 25

    def get_paginate_by(self, queryset):
        return None if self.request.GET.get("view", "calendar") == "calendar" else self.paginate_by

    def _anchor_date(self):
        raw = self.request.GET.get("date")
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                pass
        return timezone.localdate()

    def _base_queryset(self):
        club, user = self.request.club, self.request.user
        events = Event.objects.filter(club=club).select_related("location", "opponent").prefetch_related("teams")
        kind = self.request.GET.get("kind", "")
        if kind:
            events = events.filter(kind=kind)
        return scoped_to_managed_teams(events, user, club)

    def get_queryset(self):
        club, user = self.request.club, self.request.user
        events = self._base_queryset()

        if self.request.GET.get("view", "calendar") == "calendar":
            range_kind = self.request.GET.get("range", "week")
            if range_kind == "week":
                start, end = week_bounds(self._anchor_date())
            elif range_kind == "season":
                season = selected_season_from_request(self.request, club)
                start, end = (season.start_date, season.end_date) if season else month_bounds(self._anchor_date())
            else:
                start, end = month_bounds(self._anchor_date())
            events = list(events.filter(start__date__gte=start, start__date__lte=end).order_by("start"))
        else:
            season = selected_season_from_request(self.request, club)
            if season is not None:
                events = events.filter(Q(season=season) | Q(season__isnull=True, start__date__gte=season.start_date, start__date__lte=season.end_date))
            now = timezone.now()
            if self.request.GET.get("show_past") == "1":
                events = list(events.filter(start__lt=now).order_by("-start"))
            else:
                events = list(events.filter(start__gte=now).order_by("start"))

        # Attached per row so the template can show/hide Edit/Delete per event --
        # computed once here rather than a query per row (teams is already
        # prefetched above).
        is_admin = is_club_admin(user, club)
        managed_team_ids = set() if is_admin else set(teams_managed_by(user, club).values_list("pk", flat=True))
        for event in events:
            event.can_manage = is_admin or any(team.pk in managed_team_ids for team in event.teams.all())
        return events

    def _calendar_context(self, range_kind, anchor, selected_season):
        """The grid + prev/next/today nav for whichever range is active --
        events is self.object_list, already windowed to it by get_queryset
        above. Season range has no anchor-based nav: it steps between actual
        Season rows (seasons, already in context) via next(start_date) since
        that list is sorted newest-first."""
        events = self.object_list
        if range_kind == "week":
            grid = week_grid(events, week_bounds(anchor)[0])
            calendar_nav = {"prev": anchor - timedelta(days=7), "next": anchor + timedelta(days=7)}
        elif range_kind == "season":
            grid = {"months": season_grid(events, selected_season)} if selected_season else None
            calendar_nav = {}
        else:
            grid = month_grid(events, anchor)
            calendar_nav = {"prev": add_months(anchor, -1), "next": add_months(anchor, 1)}
        return grid, calendar_nav

    def get_context_data(self, **kwargs):
        club, user = self.request.club, self.request.user
        view_mode = self.request.GET.get("view", "calendar")
        range_kind = self.request.GET.get("range", "week")
        anchor = self._anchor_date()
        selected_season = selected_season_from_request(self.request, club)

        calendar, calendar_nav = (None, None)
        if view_mode == "calendar":
            calendar, calendar_nav = self._calendar_context(range_kind, anchor, selected_season)

        return super().get_context_data(
            seasons=Season.objects.filter(club=club).order_by("-start_date"),
            selected_season=selected_season,
            selected_kind=self.request.GET.get("kind", ""),
            show_past=self.request.GET.get("show_past") == "1",
            event_kinds=Event.EventKind.choices,
            can_create=is_club_admin(user, club) or teams_managed_by(user, club).exists(),
            view_mode=view_mode,
            calendar_range=range_kind,
            anchor=anchor,
            today=timezone.localdate(),
            calendar=calendar,
            # Not "nav" -- that key belongs to management.context_processors.
            # active_nav_section (the sidebar's own active-item marker, set from
            # the URL name and used app-wide by _nav_items.html); reusing it here
            # for the calendar's prev/next pair silently shadowed the sidebar's
            # value and broke the Events sub-item's highlight on this page only.
            calendar_nav=calendar_nav,
            **kwargs,
        )


class EventDetailView(ClubStaffRequiredMixin, DetailView):
    template_name = "management/event_detail.html"
    context_object_name = "event"

    def get_queryset(self):
        events = Event.objects.filter(club=self.request.club).select_related("series", "location", "opponent", "season").prefetch_related("teams", "invited_members", "excluded_members")
        return scoped_to_managed_teams(events, self.request.user, self.request.club)

    def get_context_data(self, **kwargs):
        club, user, event = self.request.club, self.request.user, self.object
        can_manage = is_club_admin(user, club) or teams_managed_by(user, club).filter(pk__in=event.teams.values_list("pk", flat=True)).exists()

        rows_by_status = {}
        for row in event.attendances.select_related("member").order_by("member__last_name", "member__first_name"):
            rows_by_status.setdefault(row.status, []).append(row)

        # Every status, in its declared order -- not just the ones that happen to have
        # a response yet, or "0 people excused" silently disappears instead of reading
        # as good news. Same grouping backs both the breakdown counts and the modal's
        # per-status sections, so there's exactly one query, not two.
        attendance_groups = [{"value": value, "label": label, "rows": rows_by_status.get(value, [])} for value, label in Attendance.AttendanceStatus.choices]

        # Admin-only for now, unlike can_manage's other actions (edit, fetch info, ...) --
        # a team manager/coach still sees the Referees panel (who's assigned, capacity),
        # just not the assign/remove controls. See EventRefereeAssignView/RemoveView.
        can_manage_referees = is_club_admin(user, club)

        referee_management_needed = needs_referee_management(event)
        referees = []
        referee_candidates = []
        referees_full = False
        if referee_management_needed:
            referees = list(event.referees.select_related("member", "assigned_by").order_by("member__last_name", "member__first_name"))
            referees_full = len(referees) >= event.max_referees
            if can_manage_referees and not referees_full:
                for candidate in eligible_referees(event):
                    conflicts = conflicting_events(candidate, event)
                    candidate.has_conflict = bool(conflicts)
                    candidate.conflict_titles = ", ".join(conflict.title for conflict in conflicts)
                    referee_candidates.append(candidate)

        return super().get_context_data(
            can_manage=can_manage,
            can_manage_referees=can_manage_referees,
            referee_management_needed=referee_management_needed,
            attendance_groups=attendance_groups,
            has_attendance_rows=any(group["rows"] for group in attendance_groups),
            attendance_row_count=sum(len(group["rows"]) for group in attendance_groups),
            referees=referees,
            referee_candidates=referee_candidates,
            referees_full=referees_full,
            **kwargs,
        )


def _redirect_next_or(request, fallback_url):
    """`next` (POST body, or the query string -- the fee-edit modal posts to a
    plain action_url with no room to inject a hidden field, so it carries
    `next` there instead) if it's safe to redirect to, else `fallback_url`.
    Lets the same assign/remove/fee endpoints be posted to from more than one
    page (the event detail page, and the referee management dashboard) and
    return the visitor to wherever they actually came from."""
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(next_url)
    return redirect(fallback_url)


class EventRefereeAssignView(ClubAdminRequiredMixin, View):
    """Reachable via the assign control on the event detail page's Referees
    panel, or the referee management dashboard -- POST-only, no standalone
    template. Admin-only for now (unlike most event actions, which a team
    manager can also do) -- see EventDetailView's can_manage_referees; team
    managers/coaches still see the panel, just not the assign/remove
    controls."""

    def get_event(self):
        return get_object_or_404(Event.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk):
        event = self.get_event()
        member = get_object_or_404(eligible_referees(event), pk=request.POST.get("member"))
        assigned_by = Member.objects.filter(user=request.user).first()

        try:
            assign_referee(event, member, assigned_by=assigned_by)
        except RefereeAssignmentError as error:
            notify(request, f"e|{_('Could not assign referee')}|{error}")
        else:
            notify(request, f"s|{_('Referee assigned')}|" + _("“%(member)s” will referee this game.") % {"member": member})

        return _redirect_next_or(request, reverse("management:event_detail", args=[event.pk]))


class EventRefereeRemoveView(ClubAdminRequiredMixin, View):
    def get_event(self):
        return get_object_or_404(Event.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk, referee_pk):
        event = self.get_event()
        referee = get_object_or_404(EventReferee.objects.filter(event=event), pk=referee_pk)
        name = referee.display_name
        remove_referee(referee)
        notify(request, f"w|{_('Referee removed')}|" + _("“%(name)s” is no longer refereeing this game.") % {"name": name})
        return _redirect_next_or(request, reverse("management:event_detail", args=[event.pk]))


class EventRefereeAddExternalView(ClubAdminRequiredMixin, View):
    """Log a non-member referee (federation-appointed, most often) against a
    game -- reachable from the same Referees panel as EventRefereeAssignView,
    on both the event detail page and the referee management dashboard."""

    def get_event(self):
        return get_object_or_404(Event.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk):
        event = self.get_event()
        form = ExternalRefereeForm(request.POST)
        fallback = _redirect_next_or(request, reverse("management:event_detail", args=[event.pk]))

        if not form.is_valid():
            notify(request, f"e|{_('Could not add referee')}|{_('A name is required.')}")
            return fallback

        assigned_by = Member.objects.filter(user=request.user).first()
        try:
            add_external_referee(event, form.cleaned_data["name"], assigned_by=assigned_by)
        except RefereeAssignmentError as error:
            notify(request, f"e|{_('Could not add referee')}|{error}")
        else:
            notify(request, f"s|{_('Referee added')}|" + _("“%(name)s” will referee this game.") % {"name": form.cleaned_data["name"]})

        return fallback


class EventRefereeFeeUpdateView(ClubAdminRequiredMixin, FormView):
    """Set one referee assignment's fee/km/rate -- reachable via the "Fee"
    modal on the Referees panel. POST-only, no standalone template. Not
    RedirectOnInvalidMixin: that redirects to a fixed url name, but this view
    (like the assign/remove ones) needs to honour the next-aware fallback so
    it works from both the event detail page and the dashboard."""

    form_class = EventRefereeFeeForm
    http_method_names = ["post"]

    def get_event(self):
        return get_object_or_404(Event.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_referee(self):
        return get_object_or_404(EventReferee.objects.filter(event=self.get_event()), pk=self.kwargs["referee_pk"])

    def get_fallback(self):
        return _redirect_next_or(self.request, reverse("management:event_detail", args=[self.kwargs["pk"]]))

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"instance": self.get_referee()}

    def form_invalid(self, form):
        for error in form.errors.values():
            notify(self.request, f"e|{_('Could not update fee')}|{' '.join(error)}")
        return self.get_fallback()

    def form_valid(self, form):
        referee = self.get_referee()
        set_referee_fee(referee, fee=form.cleaned_data["fee"], km=form.cleaned_data["km"], km_rate=form.cleaned_data["km_rate"])
        notify(self.request, f"s|{_('Fee updated')}|" + _("Updated the fee for “%(name)s”.") % {"name": referee.display_name})
        return self.get_fallback()


class EventRefereeFormPdfView(ClubAdminRequiredMixin, View):
    """Downloadable PDF of the referee payment form for one game, modeled on
    the club's existing paper form -- club header (legal name if set, else
    plain name; address from the club's home Location, not this specific
    event's, so the form still reads right even if called from a page where
    the event's own location happens to be blank) plus this game's details,
    referees and their fee/km breakdown, and blank signature lines."""

    def get(self, request, pk):
        event = get_object_or_404(Event.objects.filter(club=request.club).prefetch_related("teams", "referees__member"), pk=pk)
        home_location = Location.objects.filter(club=request.club, is_home=True).first()
        referees = list(event.referees.all())
        context = {"club": request.club, "event": event, "referees": referees, "home_location": home_location, "grand_total": sum((referee.total_payable for referee in referees), Decimal("0"))} | referee_form_colors(request.club)

        try:
            pdf = event_referee_form_pdf(context)
        except PDFExportError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return redirect(reverse("management:event_detail", args=[event.pk]))

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="referee-form-{event.pk}.pdf"'
        return response


def upcoming_games_needing_referee_management(club):
    """Upcoming home games a club-arranged referee is needed for -- federation-
    managed teams never appear here, see events.services.referees.needs_referee_management.

    The base query behind RefereeManagementDashboardView's own list, factored out
    so the nav's Referee management badge (games_missing_referees_count below,
    used by management.context_processors.sidebar_counters) counts from exactly
    the same set of games rather than a second, potentially-drifting definition
    of "needs a referee"."""
    return (
        Event.objects.filter(
            club=club,
            kind=Event.EventKind.GAME,
            cancelled=False,
            location__is_home=True,
            start__gte=timezone.now(),
            teams__referee_management=Team.RefereeManagement.CLUB,
        )
        .distinct()
        .order_by("start")
    )


def games_missing_referees_count(club, limit=10):
    """How many of the next `limit` upcoming club-managed home games have nobody
    assigned yet -- the same games RefereeManagementDashboardView's own
    kpi_no_referee counts for its default "next 10" range, but via one annotated
    query rather than the per-game referee_rows/eligible_referees loop the
    dashboard builds for rendering (which a nav badge has no use for)."""
    games = upcoming_games_needing_referee_management(club).annotate(referee_count=Count("referees", distinct=True))[:limit]
    return sum(1 for game in games if game.referee_count == 0)


class RefereeManagementDashboardView(MemberAdminRequiredMixin, TemplateView):
    """One-stop admin view of every upcoming home game that needs a
    club-arranged referee (federation-managed teams never appear here, see
    events.services.referees.needs_referee_management), with inline
    assign/remove -- posts to the same EventRefereeAssignView/RemoveView the
    event detail page uses, via the shared _referee_assignment_panel include,
    and returns here afterwards rather than to the event detail page.

    The `range` GET param picks either a calendar window ("week"/"two_weeks",
    both anchored on the ISO week so "this week" always means Mon-Sun of the
    current week regardless of what weekday it is today) or a flat count of
    upcoming games -- buttons in the template, not a dropdown, since there
    are only a handful of sensible choices."""

    template_name = "management/referee_management.html"
    RANGE_CHOICES = ["week", "two_weeks", "10", "25", "50"]
    DEFAULT_RANGE = "10"

    def get_range(self):
        value = self.request.GET.get("range", self.DEFAULT_RANGE)
        return value if value in self.RANGE_CHOICES else self.DEFAULT_RANGE

    def get_context_data(self, **kwargs):
        club = self.request.club
        range_choice = self.get_range()

        queryset = upcoming_games_needing_referee_management(club).select_related("location", "opponent").prefetch_related("teams", "referees__member", "referees__assigned_by")

        if range_choice in ("week", "two_weeks"):
            today = timezone.localdate()
            end_of_this_week = today + timedelta(days=6 - today.weekday())
            end_date = end_of_this_week + timedelta(days=7) if range_choice == "two_weeks" else end_of_this_week
            games = list(queryset.filter(start__date__lte=end_date))
        else:
            games = list(queryset[: int(range_choice)])

        kpi_no_referee = 0
        kpi_understaffed = 0
        kpi_fully_staffed = 0
        kpi_fees_pending = 0

        for game in games:
            game.referee_rows = list(game.referees.all())
            game.referees_full = len(game.referee_rows) >= game.max_referees
            game.referee_candidates = []
            game.fees_pending = any(not referee.fee for referee in game.referee_rows)
            if not game.referee_rows:
                kpi_no_referee += 1
            elif not game.referees_full:
                kpi_understaffed += 1
            else:
                kpi_fully_staffed += 1
            if game.fees_pending:
                kpi_fees_pending += 1
            if not game.referees_full:
                for candidate in eligible_referees(game):
                    conflicts = conflicting_events(candidate, game)
                    candidate.has_conflict = bool(conflicts)
                    candidate.conflict_titles = ", ".join(conflict.title for conflict in conflicts)
                    game.referee_candidates.append(candidate)

        return super().get_context_data(
            games=games,
            range_choice=range_choice,
            kpi_total=len(games),
            kpi_no_referee=kpi_no_referee,
            kpi_understaffed=kpi_understaffed,
            kpi_fully_staffed=kpi_fully_staffed,
            kpi_fees_pending=kpi_fees_pending,
            **kwargs,
        )


class EventCreateView(ClubStaffRequiredMixin, CreateView):
    """Broader than EventManagerRequiredMixin's own gate (no object yet to check
    teams/groups against): anyone managing at least one team, belonging to at
    least one group, or an admin. EventForm itself then restricts *which*
    teams/groups a non-admin can pick and requires at least one, so a
    club-wide event stays admin-only."""

    model = Event
    form_class = EventForm
    template_name = "management/event_form.html"

    def test_func(self):
        user, club = self.request.user, self.request.club
        return is_club_admin(user, club) or teams_managed_by(user, club).exists() or groups_manageable_by(user, club).exists()

    def get_form_kwargs(self):
        # Event.clean() rejects a location/opponent from another club by comparing
        # against self.club_id -- on a brand-new instance that's still None until
        # ClubScopedModel.save() auto-assigns it, which only happens *after*
        # validation. Set it here so full_clean() sees the real club, not None.
        return super().get_form_kwargs() | {"club": self.request.club, "user": self.request.user, "instance": Event(club=self.request.club)}

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(event)s” created.") % {"event": self.object}
        notify(self.request, f"s|{_('Event created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:event_detail", args=[self.object.pk])


class EventUpdateView(EventManagerRequiredMixin, UpdateView):
    model = Event
    form_class = EventForm
    template_name = "management/event_form.html"

    def get_queryset(self):
        return Event.objects.filter(club=self.request.club)

    def get_teams(self):
        return get_object_or_404(Event.objects.filter(club=self.request.club), pk=self.kwargs["pk"]).teams.all()

    def get_groups(self):
        return get_object_or_404(Event.objects.filter(club=self.request.club), pk=self.kwargs["pk"]).groups.all()

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club, "user": self.request.user, "editing": True}

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(event)s” updated.") % {"event": self.object}
        notify(self.request, f"s|{_('Event updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:event_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class EventDeleteView(EventManagerRequiredMixin, View):
    """A series occurrence is cancelled (keeps the series' excluded_dates in
    sync -- see cancel_occurrence), never just deleted outright; a one-off
    event is deleted outright. One button, the branch is internal."""

    def get_event(self):
        return get_object_or_404(Event.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_teams(self):
        return self.get_event().teams.all()

    def get_groups(self):
        return self.get_event().groups.all()

    def post(self, request, pk):
        event = self.get_event()
        title = str(event)
        if event.series_id:
            cancel_occurrence(event, hard_delete=request.POST.get("keep_record") != "on")
            notify(request, f"w|{_('Occurrence cancelled')}|{_('“%(event)s” was cancelled.') % {'event': title}}")
        else:
            event.delete()
            notify(request, f"w|{_('Event deleted')}|{_('“%(event)s” has been deleted.') % {'event': title}}")
        return redirect("management:event_list")


class EventDetachView(EventManagerRequiredMixin, View):
    """Stop this occurrence from being touched by future series-wide edits --
    see detach_occurrence. Editing a still-attached occurrence directly would
    otherwise be silently overwritten by the next propagate_series() call."""

    def get_event(self):
        return get_object_or_404(Event.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_teams(self):
        return self.get_event().teams.all()

    def get_groups(self):
        return self.get_event().groups.all()

    def post(self, request, pk):
        event = self.get_event()
        detach_occurrence(event)
        notify(request, f"s|{_('Detached from series')}|{_('“%(event)s” is now edited independently and will not be touched by future series-wide changes.') % {'event': event}}")
        return redirect("management:event_detail", pk=event.pk)


class EventFetchGameInfoView(EventManagerRequiredMixin, View):
    """Refresh a game's score/status from its competition -- see
    events.services.competitions.fetch_game_info, which gates on the
    competition's feature flag being active for this club and otherwise
    no-ops. No data source is wired up yet either way, so an actual fetch
    attempt always reports the same honest "not configured" error; the
    button/view exist so a real integration only has to replace that function."""

    def get_event(self):
        return get_object_or_404(Event.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_teams(self):
        return self.get_event().teams.all()

    def get_groups(self):
        return self.get_event().groups.all()

    def post(self, request, pk):
        event = self.get_event()
        try:
            if fetch_game_info(event):
                notify(request, f"s|{_('Game info updated')}|{_('“%(event)s” was refreshed from its competition.') % {'event': event}}")
            else:
                notify(request, f"i|{_('Nothing to fetch')}|{_('“%(competition)s” is not enabled for this club.') % {'competition': event.competition}}")
        except CompetitionFetchError as error:
            notify(request, f"e|{_('Could not fetch game info')}|{error}")
        return redirect("management:event_detail", pk=event.pk)


class RBIHFImportView(FeatureRequiredMixin, View):
    """Step 1: paste an RBIHF team page URL, pick which of the club's teams it's
    for. Fetches and parses the page server-side, stashes the raw HTML (not
    client-trusted parsed data) in the session, and renders a create/update/
    delete preview -- see events.services.rbihf_import and
    RBIHFImportConfirmView, which mirrors MemberImportView/
    MemberImportConfirmView's session-stash-and-reparse shape."""

    feature_flag = "RBIHF"

    def get(self, request):
        return render(request, "management/rbihf_import_form.html", {"form": RBIHFImportForm(club=request.club)})

    def post(self, request):
        form = RBIHFImportForm(request.POST, club=request.club)
        if not form.is_valid():
            return render(request, "management/rbihf_import_form.html", {"form": form})

        url = form.cleaned_data["url"]
        team = form.cleaned_data["team"]

        rbihf_team_id = extract_team_id(url)
        try:
            html = fetch_html(url)
            plan = build_plan(request.club, team, rbihf_team_id, html)
        except RBIHFImportError as error:
            form.add_error("url", str(error))
            return render(request, "management/rbihf_import_form.html", {"form": form})

        request.session["rbihf_import_html"] = html
        request.session["rbihf_import_team_id"] = str(team.pk)
        request.session["rbihf_import_rbihf_team_id"] = rbihf_team_id
        return render(request, "management/rbihf_import_preview.html", {"plan": plan})


class RBIHFImportConfirmView(FeatureRequiredMixin, View):
    """Step 2: re-parses and re-diffs the HTML stashed by RBIHFImportView
    against the *current* DB state (catching anything that changed since the
    preview was shown), reads each row's chosen location/opponent back from
    the preview form, and applies the result in one transaction."""

    feature_flag = "RBIHF"

    def post(self, request):
        html = request.session.pop("rbihf_import_html", None)
        team_id = request.session.pop("rbihf_import_team_id", None)
        rbihf_team_id = request.session.pop("rbihf_import_rbihf_team_id", None)
        if not html or not team_id or not rbihf_team_id:
            notify(request, f"w|{_('Nothing to import')}|{_('Start over by pasting the RBIHF team URL again.')}")
            return redirect("management:rbihf_import")

        team = get_object_or_404(Team.objects.filter(club=request.club), pk=team_id)

        try:
            plan = build_plan(request.club, team, rbihf_team_id, html)
        except RBIHFImportError:
            notify(request, f"e|{_('Could not import')}|{_('Something went wrong re-reading the fetched page. Try again.')}")
            return redirect("management:rbihf_import")

        locations_by_game_id = {}
        opponents_by_game_id = {}
        for planned in [*plan.to_create, *plan.to_update]:
            game_id = planned.fixture.external_game_id
            locations_by_game_id[game_id] = request.POST.get(f"location_{game_id}", "")
            opponents_by_game_id[game_id] = request.POST.get(f"opponent_{game_id}", "")

        result = apply_plan(plan, locations_by_game_id, opponents_by_game_id)

        body = _("%(created)s created, %(updated)s updated, %(deleted)s deleted.") % result
        notify(request, f"s|{_('Fixtures imported')}|{body}")
        return redirect("management:event_list")


class EventSeriesDetailView(ClubStaffRequiredMixin, DetailView):
    template_name = "management/event_series_detail.html"
    context_object_name = "series"

    def get_queryset(self):
        series = EventSeries.objects.filter(club=self.request.club).select_related("location", "opponent").prefetch_related("teams", "invited_members", "excluded_members")
        return scoped_to_managed_teams(series, self.request.user, self.request.club)

    def get_context_data(self, **kwargs):
        club, user, series = self.request.club, self.request.user, self.object
        can_manage = is_club_admin(user, club) or teams_managed_by(user, club).filter(pk__in=series.teams.values_list("pk", flat=True)).exists()
        now = timezone.now()
        occurrences = list(series.occurrences.order_by("start"))
        for occurrence in occurrences:
            occurrence.is_past = occurrence.start < now
        return super().get_context_data(
            can_manage=can_manage,
            recurrence_summary=describe_rrule(series.rrule),
            occurrences=occurrences,
            **kwargs,
        )


class EventSeriesCreateView(ClubStaffRequiredMixin, CreateView):
    model = EventSeries
    form_class = EventSeriesForm
    template_name = "management/event_series_form.html"

    def test_func(self):
        user, club = self.request.user, self.request.club
        return is_club_admin(user, club) or teams_managed_by(user, club).exists() or groups_manageable_by(user, club).exists()

    def get_form_kwargs(self):
        # Same reasoning as EventCreateView: EventSeries.clean() needs a real
        # club_id on the instance before full_clean() runs, not the None a
        # brand-new instance starts with.
        return super().get_form_kwargs() | {"club": self.request.club, "user": self.request.user, "instance": EventSeries(club=self.request.club)}

    def form_valid(self, form):
        response = super().form_valid(form)
        # Not automatic on save -- without this the series would exist with zero
        # occurrences until the extend_event_series cron command next runs.
        created = generate_occurrences(self.object)
        body = ngettext("“%(series)s” created, with %(count)d occurrence scheduled.", "“%(series)s” created, with %(count)d occurrences scheduled.", len(created)) % {"series": self.object, "count": len(created)}
        notify(self.request, f"s|{_('Series created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:event_series_detail", args=[self.object.pk])


class EventSeriesUpdateView(EventManagerRequiredMixin, UpdateView):
    model = EventSeries
    form_class = EventSeriesForm
    template_name = "management/event_series_form.html"

    def get_queryset(self):
        return EventSeries.objects.filter(club=self.request.club)

    def get_teams(self):
        return get_object_or_404(EventSeries.objects.filter(club=self.request.club), pk=self.kwargs["pk"]).teams.all()

    def get_groups(self):
        return get_object_or_404(EventSeries.objects.filter(club=self.request.club), pk=self.kwargs["pk"]).groups.all()

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club, "user": self.request.user}

    def form_valid(self, form):
        response = super().form_valid(form)
        # Push the template change to future, non-detached occurrences, then fill
        # in any further-out dates the (possibly changed) pattern now implies.
        # Occurrences that no longer match a changed pattern are NOT auto-removed
        # -- reconciling that is ambiguous (which to drop vs. keep attendance
        # history for) and is left as a manual "Cancel" per stale occurrence.
        propagate_series(self.object)
        generate_occurrences(self.object)
        body = _("“%(series)s” updated.") % {"series": self.object}
        notify(self.request, f"s|{_('Series updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:event_series_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class EventSeriesDeleteView(EventManagerRequiredMixin, View):
    """series is on_delete=CASCADE -- this also deletes every occurrence and its
    attendance history. The confirm modal must say so; "Stop repeating"
    (EventSeriesStopView) is the non-destructive alternative."""

    def get_series(self):
        return get_object_or_404(EventSeries.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_teams(self):
        return self.get_series().teams.all()

    def get_groups(self):
        return self.get_series().groups.all()

    def post(self, request, pk):
        series = self.get_series()
        title = str(series)
        series.delete()
        notify(request, f"w|{_('Series deleted')}|{_('“%(series)s” and all of its occurrences have been deleted.') % {'series': title}}")
        return redirect("management:event_list")


class EventSeriesStopView(EventManagerRequiredMixin, View):
    """Stop future generation without touching any existing occurrence or its
    attendance history -- the non-destructive alternative to deleting the
    series outright."""

    def get_series(self):
        return get_object_or_404(EventSeries.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_teams(self):
        return self.get_series().teams.all()

    def get_groups(self):
        return self.get_series().groups.all()

    def post(self, request, pk):
        series = self.get_series()
        series.until = timezone.now()
        series.save(update_fields=["until"])
        notify(request, f"s|{_('Series stopped')}|{_('“%(series)s” will no longer generate new occurrences. Existing ones are untouched.') % {'series': series}}")
        return redirect("management:event_series_detail", pk=series.pk)


class EventSeriesGenerateView(EventManagerRequiredMixin, View):
    def get_series(self):
        return get_object_or_404(EventSeries.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_teams(self):
        return self.get_series().teams.all()

    def get_groups(self):
        return self.get_series().groups.all()

    def post(self, request, pk):
        series = self.get_series()
        created = generate_occurrences(series)
        body = ngettext("%(count)d new occurrence generated.", "%(count)d new occurrences generated.", len(created)) % {"count": len(created)}
        notify(request, f"s|{_('Occurrences generated')}|{body}")
        return redirect("management:event_series_detail", pk=series.pk)


class LocationListView(ManagementPositionRequiredMixin, ListView):
    template_name = "management/location_list.html"
    context_object_name = "locations"

    def get_queryset(self):
        return Location.objects.filter(club=self.request.club)


class LocationCreateView(ManagementPositionRequiredMixin, CreateView):
    model = Location
    form_class = LocationForm
    template_name = "management/location_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(location)s” created.") % {"location": self.object}
        notify(self.request, f"s|{_('Location created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:location_list")


class LocationUpdateView(ManagementPositionRequiredMixin, UpdateView):
    model = Location
    form_class = LocationForm
    template_name = "management/location_form.html"

    def get_queryset(self):
        return Location.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(location)s” updated.") % {"location": self.object}
        notify(self.request, f"s|{_('Location updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:location_list")

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class LocationDeleteView(ManagementPositionRequiredMixin, View):
    def post(self, request, pk):
        location = get_object_or_404(Location.objects.filter(club=request.club), pk=pk)
        name = str(location)
        # Event/EventSeries.location is SET_NULL -- no ProtectedError to catch.
        location.delete()

        body = _("“%(location)s” has been deleted.") % {"location": name}
        notify(request, f"w|{_('Location deleted')}|{body}")
        return redirect("management:location_list")


class OpponentListView(ManagementPositionRequiredMixin, ListView):
    template_name = "management/opponent_list.html"
    context_object_name = "opponents"

    def get_queryset(self):
        return Opponent.objects.filter(club=self.request.club)


class OpponentCreateView(ManagementPositionRequiredMixin, CreateView):
    model = Opponent
    form_class = OpponentForm
    template_name = "management/opponent_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(opponent)s” created.") % {"opponent": self.object}
        notify(self.request, f"s|{_('Opponent created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:opponent_list")


class OpponentUpdateView(ManagementPositionRequiredMixin, UpdateView):
    model = Opponent
    form_class = OpponentForm
    template_name = "management/opponent_form.html"

    def get_queryset(self):
        return Opponent.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(opponent)s” updated.") % {"opponent": self.object}
        notify(self.request, f"s|{_('Opponent updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:opponent_list")

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class OpponentDeleteView(ManagementPositionRequiredMixin, View):
    def post(self, request, pk):
        opponent = get_object_or_404(Opponent.objects.filter(club=request.club), pk=pk)
        name = str(opponent)
        # Event/EventSeries.opponent is SET_NULL -- no ProtectedError to catch.
        opponent.delete()

        body = _("“%(opponent)s” has been deleted.") % {"opponent": name}
        notify(request, f"w|{_('Opponent deleted')}|{body}")
        return redirect("management:opponent_list")


class SponsorListView(ClubAdminRequiredMixin, ListView):
    """Sponsors are a business/revenue relationship, same bucket as
    memberships/roles/shop -- admin-only, unlike Location/Opponent which any
    management position can maintain."""

    template_name = "management/sponsor_list.html"
    context_object_name = "sponsors"

    def get_queryset(self):
        return Sponsor.objects.filter(club=self.request.club)


class SponsorCreateView(ClubAdminRequiredMixin, CreateView):
    model = Sponsor
    form_class = SponsorForm
    template_name = "management/sponsor_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(sponsor)s” created.") % {"sponsor": self.object}
        notify(self.request, f"s|{_('Sponsor created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:sponsor_list")


class SponsorUpdateView(ClubAdminRequiredMixin, UpdateView):
    model = Sponsor
    form_class = SponsorForm
    template_name = "management/sponsor_form.html"

    def get_queryset(self):
        return Sponsor.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(sponsor)s” updated.") % {"sponsor": self.object}
        notify(self.request, f"s|{_('Sponsor updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:sponsor_list")

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class SponsorDeleteView(ClubAdminRequiredMixin, View):
    def post(self, request, pk):
        sponsor = get_object_or_404(Sponsor.objects.filter(club=request.club), pk=pk)
        name = str(sponsor)
        sponsor.delete()

        body = _("“%(sponsor)s” has been deleted.") % {"sponsor": name}
        notify(request, f"w|{_('Sponsor deleted')}|{body}")
        return redirect("management:sponsor_list")


class ProductListView(FeatureRequiredMixin, StubListMixin, ListView):
    feature_flag = "shop"
    page_title = _("Products")

    def get_queryset(self):
        return Product.objects.filter(club=self.request.club)


class OrderListView(FeatureRequiredMixin, StubListMixin, ListView):
    feature_flag = "shop"
    page_title = _("Orders")

    def get_queryset(self):
        return Order.objects.filter(club=self.request.club)


class DiscountListView(FeatureRequiredMixin, StubListMixin, ListView):
    feature_flag = "shop"
    page_title = _("Discounts")

    def get_queryset(self):
        return Discount.objects.filter(club=self.request.club)


class InvoiceListView(FeatureRequiredMixin, StubListMixin, ListView):
    feature_flag = "shop"
    page_title = _("Invoices")

    def get_queryset(self):
        return Invoice.objects.filter(club=self.request.club)


class FormListView(FeatureRequiredMixin, StubListMixin, ListView):
    feature_flag = "formbuilder"
    page_title = _("Forms")

    def get_queryset(self):
        return FormBuilderForm.objects.filter(club=self.request.club)


class SubmissionListView(FeatureRequiredMixin, StubListMixin, ListView):
    feature_flag = "formbuilder"
    page_title = _("Submissions")

    def get_queryset(self):
        return Submission.objects.filter(form__club=self.request.club, form_id=self.kwargs["pk"])


class ClubSettingsView(ClubAdminRequiredMixin, UpdateView):
    """A club's own self-service identity/branding editor -- the "Club identity"
    settings sub-item. Singleton by construction: always edits request.club, never
    a pk from the URL (there is exactly one club to edit here, unlike controlpanel's
    ClubForm which picks one out of every club on the platform)."""

    form_class = ClubSettingsForm
    template_name = "management/club_settings.html"

    def get_object(self, queryset=None):
        return self.request.club

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Settings saved')}|{_('Club identity updated.')}")
        return response

    def get_success_url(self):
        return reverse("management:club_settings")

    def get_context_data(self, **kwargs):
        club = self.request.club
        email_previews = []
        for preview in EMAIL_PREVIEWS:
            rendered = render_preview(preview, club=club, request=self.request)
            email_previews.append({"key": preview.key, "label": preview.label, "description": preview.description, "subject": rendered["subject"], "text": rendered["text"]})
        pdf_previews = [{"key": preview.key, "label": preview.label, "description": preview.description} for preview in PDF_PREVIEWS]
        return super().get_context_data(email_previews=email_previews, pdf_previews=pdf_previews, **kwargs)


@method_decorator(xframe_options_sameorigin, name="get")
class EmailPreviewRenderView(ClubAdminRequiredMixin, View):
    """The actual HTML document for one email preview, served at its own URL
    so the Email tab's iframe can `src` it directly rather than smuggling it
    through a srcdoc="..." attribute. An email's markup is full of its own
    double-quoted style="..." attributes; dropping that whole document into
    a srcdoc="..." attribute relies on Django's autoescaping to get every one
    of those quotes right, and on browser-specific handling of an escaped,
    inherited-CSP srcdoc document that turned out not to render reliably. A
    same-origin sub-request sidesteps all of that -- ordinary HTML delivered
    as an ordinary response.

    xframe_options_sameorigin overrides the site-wide X-Frame-Options: DENY
    (settings.py has no X_FRAME_OPTIONS override, so SecurityMiddleware's
    default applies everywhere else): this response only ever needs to be
    framed by the page that links to it, on the same origin."""

    def get(self, request, key):
        preview = EMAIL_PREVIEWS_BY_KEY.get(key)
        if preview is None:
            raise Http404

        rendered = render_preview(preview, club=request.club, request=request)
        return HttpResponse(rendered["html"], content_type="text/html; charset=utf-8")


@method_decorator(xframe_options_sameorigin, name="get")
class PDFPreviewRenderView(ClubAdminRequiredMixin, View):
    """The PDF tab's sibling of EmailPreviewRenderView -- the underlying HTML
    WeasyPrint would turn into a PDF (see pdf_previews.PDF_PREVIEWS's own
    docstring for why that's shown directly rather than actually running
    WeasyPrint), served the same same-origin-iframe-friendly way."""

    def get(self, request, key):
        preview = PDF_PREVIEWS_BY_KEY.get(key)
        if preview is None:
            raise Http404

        html = render_pdf_preview(preview, club=request.club, request=request)
        return HttpResponse(html, content_type="text/html; charset=utf-8")


class OnboardingRequirementListView(MemberAdminRequiredMixin, ListView):
    """What a club requires from every member after they sign up or renew (a
    photo, a medical certificate, ...) -- see club/models.py's OnboardingRequirement
    docstring for why this is tracked separately from status/fee_status."""

    template_name = "management/onboarding_requirement_list.html"
    context_object_name = "requirements"

    def get_queryset(self):
        return OnboardingRequirement.objects.filter(club=self.request.club)

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs)


class OnboardingRequirementCreateView(MemberAdminRequiredMixin, CreateView):
    model = OnboardingRequirement
    form_class = OnboardingRequirementForm
    template_name = "management/onboarding_requirement_form.html"

    def form_valid(self, form):
        form.instance.club = self.request.club
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Requirement added')}|" + _("“%(name)s” is now required for every member.") % {"name": self.object.name})
        return response

    def get_success_url(self):
        return reverse("management:onboarding_requirement_list")

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs)


class OnboardingRequirementUpdateView(MemberAdminRequiredMixin, UpdateView):
    model = OnboardingRequirement
    form_class = OnboardingRequirementForm
    template_name = "management/onboarding_requirement_form.html"

    def get_queryset(self):
        return OnboardingRequirement.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Requirement updated')}|" + _("“%(name)s” updated.") % {"name": self.object.name})
        return response

    def get_success_url(self):
        return reverse("management:onboarding_requirement_list")

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class OnboardingRequirementDeleteView(MemberAdminRequiredMixin, View):
    def post(self, request, pk):
        requirement = get_object_or_404(OnboardingRequirement.objects.filter(club=request.club), pk=pk)
        name = requirement.name
        requirement.delete()

        notify(request, f"w|{_('Requirement deleted')}|" + _("“%(name)s” is no longer required. Existing checklists keep whatever was already recorded for it.") % {"name": name})
        return redirect("management:onboarding_requirement_list")


def _current_membership_or_404(request, member_pk):
    """The signed-in club's current-season ClubMembership for member_pk -- same
    lookup MemberDetailView itself uses for `current_membership`, since the
    Documents tab these three views serve lives on that same page and a
    checklist item only ever makes sense against a member's *current* signup."""
    membership = ClubMembership.objects.filter(club=request.club, member_id=member_pk, season=current_season(request.club)).first()
    if membership is None:
        raise Http404("No current-season membership for this member.")
    return membership


class MemberRequirementCompleteView(MemberAdminRequiredMixin, View):
    """Mark one checklist item done for one membership -- admin/MEMBER_ADMIN
    only, same gate as the rest of people management (a plain coach can see a
    member's checklist on their profile, per ClubStaffRequiredMixin's read
    access there, but not touch it). Reachable from the member detail page's
    Documents tab (the default fallback) and from the admin-only Sign-up page
    (via `next`)."""

    def post(self, request, pk, requirement_pk):
        membership = _current_membership_or_404(request, pk)
        requirement = get_object_or_404(OnboardingRequirement.objects.filter(club=request.club), pk=requirement_pk)
        form = RequirementCompletionForm(request.POST, request.FILES)

        if form.is_valid():
            mark_complete(membership, requirement, user=request.user, document=form.cleaned_data.get("document") or None, note=form.cleaned_data["note"])
            notify(request, f"s|{_('Marked complete')}|" + _("“%(name)s” marked complete for %(member)s.") % {"name": requirement.name, "member": membership.member})
        else:
            notify(request, f"e|{_('Could not save')}|{' '.join(str(error) for errors in form.errors.values() for error in errors)}")

        return _redirect_next_or(request, reverse("management:member_detail", args=[membership.member_id]))


class MemberRequirementBypassView(MemberAdminRequiredMixin, View):
    """Confirm one checklist item isn't needed for this member (e.g. they already
    have a recent photo on file) -- see club.services.onboarding.mark_bypassed.
    Same gate as MemberRequirementCompleteView; bypassing isn't a bigger deal
    than completing, it's just a different reason the item stops blocking
    anything."""

    def post(self, request, pk, requirement_pk):
        membership = _current_membership_or_404(request, pk)
        requirement = get_object_or_404(OnboardingRequirement.objects.filter(club=request.club), pk=requirement_pk)
        form = RequirementBypassForm(request.POST)

        if form.is_valid():
            mark_bypassed(membership, requirement, user=request.user, note=form.cleaned_data["note"])
            notify(request, f"s|{_('Marked as not needed')}|" + _("“%(name)s” bypassed for %(member)s.") % {"name": requirement.name, "member": membership.member})
        else:
            notify(request, f"e|{_('Could not save')}|{' '.join(str(error) for errors in form.errors.values() for error in errors)}")

        return _redirect_next_or(request, reverse("management:member_detail", args=[membership.member_id]))


class MemberRequirementIncompleteView(MemberAdminRequiredMixin, View):
    """Reopen a checklist item -- same admin/MEMBER_ADMIN gate as the other
    two mutating requirement views above."""

    def post(self, request, pk, requirement_pk):
        membership = _current_membership_or_404(request, pk)
        requirement = get_object_or_404(OnboardingRequirement.objects.filter(club=request.club), pk=requirement_pk)

        mark_incomplete(membership, requirement)
        notify(request, f"w|{_('Marked incomplete')}|" + _("“%(name)s” reopened for %(member)s.") % {"name": requirement.name, "member": membership.member})

        return _redirect_next_or(request, reverse("management:member_detail", args=[membership.member_id]))


class MemberRequirementDocumentView(ClubStaffRequiredMixin, View):
    """Streams a checklist document (see MemberRequirementStatus.document) to any
    signed-in staff -- same visibility as the rest of a member's profile. The file
    itself lives on rosterchief.storage.private_storage, which has no public URL at
    all; this view is the only way to read one."""

    def get(self, request, pk, requirement_pk):
        membership = _current_membership_or_404(request, pk)
        status = get_object_or_404(MemberRequirementStatus.objects.filter(membership=membership, requirement_id=requirement_pk))

        if not status.document:
            raise Http404("No document has been uploaded for this requirement.")

        return FileResponse(status.document.open("rb"), as_attachment=True, filename=status.document.name.rsplit("/", 1)[-1])


# --- Sign-up (admin only) ----------------------------------------------------------


class SignupDashboardView(ClubAdminRequiredMixin, TemplateView):
    """Every current-season MEMBER-kind membership, checklist status and fee status
    side by side, plus which teams they've been placed on so far -- the D3-inspired
    admin queue for processing this season's sign-ups end to end. Admin-only: fee
    status feeds this page directly (see MembershipListView, its own separate
    Finance-side page for actually recording payment), so it sits on the same side
    of that boundary, unlike the rest of the Members section which MEMBER_ADMIN can
    also reach.

    Team placement (SignupPlaceInTeamView) creates the roster row immediately,
    regardless of status -- see events.services.attendance.effective_members and
    club.services.onboarding.blocked_member_ids_for_event for how an event's
    invitations/selection still exclude a member per event kind until whatever
    blocks that kind clears, so a provisional player is visible to their coach
    without being invited to a game their paperwork isn't ready for."""

    template_name = "management/signup_list.html"

    def get_context_data(self, **kwargs):
        club = self.request.club
        season = current_season(club)
        memberships = []
        teams = Team.objects.filter(club=club)

        if season is not None:
            # PENDING first and oldest-first within that -- same "the queue's own
            # priority order" reasoning as D3's own longest-waiting-on-top table,
            # with anyone already ACTIVE but not yet clean (unpaid, or an open
            # checklist item) trailing behind since it's lower priority, not done.
            memberships = list(ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER).select_related("member"))
            # Sorted in Python, not via .order_by("-status", ...): StatusChoices'
            # string values only put PENDING first alphabetically by accident, and
            # that would silently break the moment a choice's value changes.
            memberships.sort(key=lambda membership: (membership.status != ClubMembership.StatusChoices.PENDING, membership.created))
            annotate_onboarding_status(memberships)
            oldest_pending_id = next((membership.pk for membership in memberships if membership.status == ClubMembership.StatusChoices.PENDING), None)
            for membership in memberships:
                membership.checklist = checklist_for(membership)
                membership.blocked_kinds = blocking_event_kinds(membership)
                membership.teams_registered = teams.filter(roster__member_id=membership.member_id, roster__season=season).distinct()
                membership.placement_form = SignupTeamPlacementForm(club=club, season=season, member=membership.member)
                membership.is_clean = is_signup_clean(membership)
                membership.is_oldest_pending = membership.pk == oldest_pending_id
            # Already-ACTIVE *and* clean (paid, checklist fully resolved) memberships
            # have nothing left for this queue to do -- drop them rather than leaving
            # them trailing at the bottom, where they read as still needing attention.
            memberships = [membership for membership in memberships if not (membership.status == ClubMembership.StatusChoices.ACTIVE and membership.is_clean)]

        return super().get_context_data(
            season=season,
            memberships=memberships,
            teams=teams,
            requirements_configured=OnboardingRequirement.objects.filter(club=club, is_active=True).exists(),
            **kwargs,
        )


class SignupApproveAllCleanView(ClubAdminRequiredMixin, View):
    """Bulk-activates every membership that's both paid up and has resolved its
    whole checklist -- see club.services.onboarding.approve_all_clean for exactly
    what "clean" means; it's the only path to ACTIVE, deliberately manual even
    for a fully-paid membership."""

    def post(self, request):
        season = current_season(request.club)
        if season is None:
            notify(request, f"e|{_('No active season')}|{_('There is no season covering today to approve sign-ups for.')}")
            return redirect("management:signup_list")

        activated = approve_all_clean(request.club, season)
        if activated:
            notify(request, f"s|{_('Approved')}|" + _("%(count)d membership moved to active.") % {"count": activated})
        else:
            notify(request, f"w|{_('Nothing to approve')}|{_('No pending membership is both paid up and fully checked off yet.')}")

        return redirect("management:signup_list")


class SignupApproveOneView(ClubAdminRequiredMixin, View):
    """The detail panel's own "Approve" button -- club.services.onboarding.approve_one
    for a single membership, same rule as the bulk version above."""

    def post(self, request, pk):
        club = request.club
        season = current_season(club)
        if season is None:
            raise Http404("No active season to approve this member in.")
        membership = get_object_or_404(ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER), member_id=pk)

        if approve_one(membership):
            notify(request, f"s|{_('Approved')}|" + _("%(member)s is now active.") % {"member": membership.member})
        else:
            notify(request, f"w|{_('Not ready yet')}|" + _("%(member)s isn't both paid up and fully checked off yet.") % {"member": membership.member})

        return redirect("management:signup_list")


class SignupPlaceInTeamView(ClubAdminRequiredMixin, View):
    """Rosters one member from the Sign-up page onto a team for the current season
    -- see SignupTeamPlacementForm for why this bypasses eligible_roster_members'
    active-only filter on purpose.

    signup_list.html posts to this in the background (fetch, X-Requested-With) and
    just toggles the clicked button's own colour on success -- no full-page reload
    for what's a one-field, one-click action. That's progressive enhancement, not
    the only way in: a plain (non-JS) POST still works exactly as before, redirected
    back to the page with a normal Django message."""

    def post(self, request, pk):
        club = request.club
        season = current_season(club)
        if season is None:
            raise Http404("No active season to place this member in.")
        membership = get_object_or_404(ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER), member_id=pk)
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        form = SignupTeamPlacementForm(request.POST, club=club, season=season, member=membership.member)
        # Team's the only real form field -- season/member are pre-seeded straight
        # onto the instance (not form fields), so ModelForm.validate_unique() skips
        # unique_member_per_team_per_season entirely (Django excludes a unique
        # check the moment any field it covers isn't part of the form). The
        # already-placed team's own button renders disabled, but a resent POST
        # still has to be handled here rather than 500ing on the DB constraint.
        if not form.is_valid():
            ok = False
            level, title, body = "e", _("Could not place on team"), " ".join(str(error) for errors in form.errors.values() for error in errors)
        elif TeamMembership.objects.filter(team=form.cleaned_data["team"], member=membership.member, season=season).exists():
            ok = False
            level, title, body = "w", _("Already placed"), _("%(member)s is already on %(team)s.") % {"member": membership.member, "team": form.cleaned_data["team"]}
        else:
            form.save()
            ok = True
            level, title, body = "s", _("Placed on team"), _("%(member)s added to %(team)s.") % {"member": membership.member, "team": form.cleaned_data["team"]}

        if is_ajax:
            return JsonResponse({"ok": ok, "title": str(title), "body": str(body)})
        notify(request, f"{level}|{title}|{body}")
        return redirect("management:signup_list")
