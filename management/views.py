import csv
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, ProtectedError, Q, Sum
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
    FormManagerRequiredMixin,
    ManagementPositionRequiredMixin,
    MemberAdminRequiredMixin,
    NewsAuthorRequiredMixin,
    NewsEditRequiredMixin,
    NewsPublisherRequiredMixin,
    ShopManagerRequiredMixin,
    TeamManagerRequiredMixin,
)
from club.models import ClubMembership, ClubRole, DuesInvoice, MemberRequirementStatus, OnboardingRequirement, Season, ShopManager, Sponsor
from club.services.access import _guardians_only, can_edit_news, can_publish_news, current_season, groups_manageable_by, is_club_admin, members_visible_to, teams_managed_by, teams_staffed_by
from club.services.cancellation import cancel_membership
from club.services.fees import mark_as_paid, record_payment, remaining_balance
from club.services.invoicing import DuesInvoicePDFError, create_or_resend_invoice, invoice_pdf, invoices_due_for_reminder, recipient_for, resolve_document_address, send_invoice_email, send_reminders
from club.services.onboarding import annotate_onboarding_status, approve_all_clean, approve_one, blocking_event_kinds, checklist_for, is_signup_clean, mark_bypassed, mark_complete, mark_incomplete, members_with_open_requirements
from club.services.signup_linking import link_to_existing_member
from controlpanel.messages import notify
from controlpanel.mixins import RedirectOnInvalidMixin
from controlpanel.services.statistics import club_attention, club_charts, club_statistics, unrostered_members
from events.models import Attendance, Event, EventReferee, EventSeries, Location, Opponent, RefereeSignup
from events.services.attendance import member_attendance_counts, member_attendance_sparkline, player_attendance_rankings, players_who_missed_recent_practices, team_attendance_rate, team_no_shows
from events.services.calendar import add_months, agenda_groups, month_bounds, month_grid, season_grid, week_bounds, week_grid
from events.services.competitions import CompetitionFetchError, fetch_game_info
from events.services.notifications import dispatch_notify_new_event
from events.services.rbihf_import import RBIHFImportError, apply_plan, build_plan, extract_team_id, fetch_html
from events.services.recurrence import cancel_occurrence, detach_occurrence, generate_occurrences, propagate_series
from events.services.referees import RefereeAssignmentError, add_external_referee, assign_referee, conflicting_events, eligible_referees, needs_referee_management, remove_referee, set_referee_fee
from formbuilder.models import Field as FormBuilderField
from formbuilder.models import Form as FormBuilderForm
from formbuilder.models import FormSend
from formbuilder.services.audience import effective_members as form_effective_members
from formbuilder.services.notifications import dispatch_notify_form_send
from formbuilder.services.reporting import form_report
from members.forms import ClaimRejectForm, ClaimReviewForm
from members.models import Family, FamilyMembership, Group, GroupMembership, Member, ParentClaim
from members.services.claims import ClaimError, approve_claim, children_awaiting_a_parent, reject_claim, send_claim_approved_email, suggested_children
from members.services.family import add_child_to_family, add_parent_to_family, attach_to_family, detach_from_family, get_or_create_login_user, grant_login, register_family
from news.models import News, NewsPhoto
from news.services import dispatch_send_publish_notification, notify_editors_of_pending_review, render_body_html
from notifications.models import Notification
from registration.models import RegistrationDetails
from shop.models import Discount, Invoice, Order, OrderLine, Payment, Product, ProductCategory, ProductionStatus, ProductRegistrantDiscountTier, ProductVariant, Voucher, VoucherConsumption
from shop.services.invoices import ShopInvoicePDFError, render_invoice_pdf
from shop.services.notifications import dispatch_order_ready_for_pickup_notification
from shop.services.payments import PaymentError, amount_due, sync_payment_status
from shop.services.payments import record_payment as record_shop_payment
from shop.services.pricing import order_total
from shop.services.production import in_production_lines, mark_line_received, mark_lines_in_production, pending_production_lines, sync_production_status
from shop.services.stats import order_kpis, quantity_sold_by_product, quantity_sold_by_variant
from shop.services.vouchers import delete_manual_consumption, record_manual_consumption, voucher_history
from teams.models import NumberPool, NumberReservation, Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership, TeamPhoto
from teams.services import eligible_roster_members

from .bulk_import import build_member_import_template, parse_member_import_rows, read_member_import_workbook
from .email_previews import EMAIL_PREVIEWS, EMAIL_PREVIEWS_BY_KEY, render_preview
from .forms import (
    AddChildForm,
    AddParentForm,
    AddPaymentForm,
    AttachToFamilyForm,
    ClubMembershipForm,
    ClubRoleAssignForm,
    ClubSettingsForm,
    DiscountForm,
    EventForm,
    EventRefereeFeeForm,
    EventSeriesForm,
    ExternalRefereeForm,
    FamilyCreateForm,
    FieldFormSet,
    FormBuilderFieldForm,
    FormForm,
    FormSendForm,
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
    NumberPoolForm,
    NumberReservationForm,
    OnboardingRequirementForm,
    OpponentForm,
    OrderBulkMarkPaidForm,
    OrderBulkMarkReadyForPickupForm,
    OrderLineEditForm,
    OrderMarkPaidForm,
    OrderMarkReadyForPickupForm,
    PaymentEditForm,
    PositionForm,
    ProductCategoryForm,
    ProductForm,
    ProductRegistrantDiscountTierFormSet,
    ProductVariantForm,
    ProductVariantFormSet,
    RBIHFImportForm,
    RecordFeePaymentForm,
    RefereeLevelForm,
    RequirementBypassForm,
    RequirementCompletionForm,
    SendDuesInvoicesForm,
    SignupLinkMemberForm,
    SignupTeamPlacementForm,
    SponsorForm,
    StaffAssignmentForm,
    TeamBulkAddFormSet,
    TeamForm,
    TeamMembershipForm,
    TeamPhotoForm,
    VolunteerPlacementForm,
    VoucherConsumptionForm,
    VoucherForm,
    bulk_add_member_label,
)
from .pdf import PDFExportError, event_referee_form_pdf, membership_list_pdf, referee_form_colors
from .pdf_previews import PDF_PREVIEWS, PDF_PREVIEWS_BY_KEY, render_pdf_preview
from .recurrence_ui import describe_rrule
from .shop_export import build_production_export, pop_production_export, stash_production_export


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


class NotificationMarkAllReadView(ClubStaffRequiredMixin, View):
    """The topbar bell dropdown's "Mark all read" action -- every one of the
    signed-in staff member's own unread notifications in this club, not just
    the handful the dropdown actually shows (see
    management.context_processors.notification_bell's [:8] slice)."""

    def post(self, request):
        member = Member.objects.filter(user=request.user).first()
        if member is not None:
            Notification.objects.filter(club=request.club, member=member, read_at__isnull=True).update(read_at=timezone.now())

        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(next_url)
        return redirect("management:home")


class NotificationClearAllView(ClubStaffRequiredMixin, View):
    """The topbar bell dropdown's "Clear all" action -- deletes every one of
    the signed-in staff member's own notifications in this club (read or
    not), not just marks them read. A harder reset than "Mark all read" for
    someone who wants the dropdown actually empty."""

    def post(self, request):
        member = Member.objects.filter(user=request.user).first()
        if member is not None:
            Notification.objects.filter(club=request.club, member=member).delete()

        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(next_url)
        return redirect("management:home")


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

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club}

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

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club}

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
                # A number picked at registration is only ever a request
                # (RegistrationDetails.requested_jersey_number) until staff
                # actually places it here -- pre-filled (still fully
                # editable) only while nothing's been typed in yet, so
                # editing the field afterwards can't be silently overwritten
                # by revisiting this page.
                requested_numbers = dict(
                    RegistrationDetails.objects.filter(membership__club=club, membership__season=season, requested_team=team).exclude(requested_jersey_number=None).values_list("membership__member_id", "requested_jersey_number")
                )
                for membership in roster:
                    initial = {"jersey_number": requested_numbers[membership.member_id]} if membership.jersey_number is None and membership.member_id in requested_numbers else None
                    membership.edit_form = TeamMembershipForm(instance=membership, club=club, team=team, season=season, initial=initial)
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
    ClubRole.Roles.MEMBER_ADMIN: _("Full read/write on people: members, families, groups, parent claims, teams, referee setup, and onboarding requirements — without Finance/Shop, Club identity, Sponsors, or granting/revoking roles."),
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
        shop_admins = ShopManager.objects.filter(club=self.request.club).select_related("member")
        return super().get_context_data(
            sections=sections,
            role_form=ClubRoleAssignForm(club=self.request.club),
            shop_admins=shop_admins,
            **kwargs,
        )


class ClubRoleCreateView(ClubAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the single "Grant role" modal on the roles overview --
    its dropdown includes "Shop admin" alongside the real ClubRole values (see
    ClubRoleAssignForm), so there's one grant button/one dropdown/one flow on
    this page, not a second, separately-triggered mechanism just for shop
    admin. Branches here rather than in the form: ShopManager isn't a
    ClubRole row at all (see its own docstring), so "granting" it is a
    different operation, not a different value of the same one."""

    form_class = ClubRoleAssignForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:role_list"

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club}

    def form_valid(self, form):
        member, role = form.cleaned_data["member"], form.cleaned_data["role"]

        if role == ClubRoleAssignForm.SHOP_ADMIN:
            _grant, created = ShopManager.objects.get_or_create(club=self.request.club, member=member)
            if created:
                notify(self.request, f"s|{_('Shop admin granted')}|{_('“%(member)s” can now manage the shop.') % {'member': member}}")
            else:
                notify(self.request, f"s|{_('Already a shop admin')}|{_('“%(member)s” already manages the shop.') % {'member': member}}")
            return redirect("management:role_list")

        # A member holds at most one ClubRole per club (the membership-status sync in
        # club/signals.py already gave any active member an implicit MEMBER role) --
        # so granting ADMIN/EDITOR promotes that existing row rather than inserting a
        # second one, exactly like controlpanel.services.admins.grant_club_admin.
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


class ShopManagerRevokeView(ClubAdminRequiredMixin, View):
    """Granting shop admin goes through ClubRoleCreateView (the roles page's
    one "Grant role" flow, "Shop admin" is just one of its dropdown values) --
    but revoking still needs its own view: ShopManager isn't a ClubRole row,
    so ClubRoleRevokeView's delete-this-pk-from-ClubRole logic doesn't apply."""

    def post(self, request, pk):
        grant = get_object_or_404(ShopManager, pk=pk, club=request.club)
        member = grant.member
        grant.delete()
        notify(request, f"w|{_('Shop admin revoked')}|{_('“%(member)s” no longer manages the shop.') % {'member': member}}")
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


class PositionDeleteView(ClubAdminRequiredMixin, View):
    def post(self, request, pk):
        position = get_object_or_404(Position.objects.filter(club=request.club), pk=pk)
        name = str(position)
        try:
            position.delete()
        except ProtectedError:
            title = _("Can't delete")
            body = _("“%(position)s” is still assigned on a team roster, and can't be deleted.") % {"position": name}
            notify(request, f"e|{title}|{body}")
            return redirect("management:position_list")

        body = _("“%(position)s” has been deleted.") % {"position": name}
        notify(request, f"w|{_('Position deleted')}|{body}")
        return redirect("management:position_list")


class NumberPoolListView(ClubStaffRequiredMixin, ListView):
    """Visible to any staff, same reasoning as PositionListView; creating/
    editing a pool is admin-only. See teams.models.NumberPool -- assigned to
    a team from that team's own edit page (management.forms.TeamForm), used
    by the Numbers page (management.views.NumberListView)."""

    template_name = "management/number_pool_list.html"
    context_object_name = "pools"

    def get_queryset(self):
        return NumberPool.objects.filter(club=self.request.club)


class NumberPoolCreateView(ClubAdminRequiredMixin, CreateView):
    model = NumberPool
    form_class = NumberPoolForm
    template_name = "management/number_pool_form.html"

    def form_valid(self, form):
        form.instance.club = self.request.club
        response = super().form_valid(form)
        body = _("“%(pool)s” created.") % {"pool": self.object}
        notify(self.request, f"s|{_('Number pool created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:number_pool_list")


class NumberPoolUpdateView(ClubAdminRequiredMixin, UpdateView):
    model = NumberPool
    form_class = NumberPoolForm
    template_name = "management/number_pool_form.html"

    def get_queryset(self):
        return NumberPool.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(pool)s” updated.") % {"pool": self.object}
        notify(self.request, f"s|{_('Number pool updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:number_pool_list")

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class NumberPoolDeleteView(ClubAdminRequiredMixin, View):
    def post(self, request, pk):
        pool = get_object_or_404(NumberPool.objects.filter(club=request.club), pk=pk)
        name = str(pool)
        # No ProtectedError to catch: Team.pool is SET_NULL (a team just loses
        # its pool) and NumberReservation.pool is CASCADE (its reservations go
        # with it) -- both a deliberate consequence of deleting the pool
        # itself, not something to block on.
        pool.delete()

        body = _("“%(pool)s” has been deleted. Any team assigned to it now has no pool, and its reservations are gone too.") % {"pool": name}
        notify(request, f"w|{_('Number pool deleted')}|{body}")
        return redirect("management:number_pool_list")


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


class RefereeLevelDeleteView(MemberAdminRequiredMixin, View):
    def post(self, request, pk):
        level = get_object_or_404(RefereeLevel.objects.filter(club=request.club), pk=pk)
        name = str(level)
        try:
            level.delete()
        except ProtectedError:
            title = _("Can't delete")
            body = _("“%(level)s” is still held by a referee or inherited by another level, and can't be deleted.") % {"level": name}
            notify(request, f"e|{title}|{body}")
            return redirect("management:referee_level_list")

        body = _("“%(level)s” has been deleted.") % {"level": name}
        notify(request, f"w|{_('Referee level deleted')}|{body}")
        return redirect("management:referee_level_list")


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


class NumberListView(ClubStaffRequiredMixin, TemplateView):
    """One pool's full number range at a glance -- five states (see
    ``_tile_state`` below), building on top of what teams.services.numbers
    already knows how to check, but broken down per-tile rather than the
    single true/false is_number_available answers that service gives.
    Viewing (like the rest of Teams) is open to any staff; reserving/
    releasing a number posts to NumberReservationCreateView/
    NumberReservationReleaseView, gated the same way."""

    template_name = "management/number_list.html"

    def get_pool(self, club, pools):
        pool_id = self.request.GET.get("pool")
        if pool_id:
            pool = pools.filter(pk=pool_id).first()
            if pool is not None:
                return pool
        return pools.first()

    def get_context_data(self, **kwargs):
        club = self.request.club
        pools = NumberPool.objects.filter(club=club).order_by("name")
        pool = self.get_pool(club, pools)
        season = selected_season_from_request(self.request, club)

        return super().get_context_data(
            pools=pools,
            pool=pool,
            seasons=Season.objects.filter(club=club).order_by("-start_date"),
            season=season,
            tiles=self.build_tiles(pool, season) if pool is not None and season is not None else [],
            reservation_form=NumberReservationForm(),
            **kwargs,
        )

    def build_tiles(self, pool, season):
        previous = Season.objects.filter(club=pool.club, start_date__lt=season.start_date).order_by("-start_date").first()

        reservations = {reservation.number: reservation for reservation in NumberReservation.objects.filter(pool=pool)}
        placed_this_season = {}
        for membership in TeamMembership.objects.filter(team__pool=pool, season=season).exclude(jersey_number=None).select_related("member"):
            placed_this_season.setdefault(membership.jersey_number, []).append(membership.member)
        placed_previous_season = {}
        if previous is not None:
            for membership in TeamMembership.objects.filter(team__pool=pool, season=previous).exclude(jersey_number=None).select_related("member"):
                placed_previous_season.setdefault(membership.jersey_number, []).append(membership.member)
        pending_this_season = {}
        for details in RegistrationDetails.objects.filter(requested_team__pool=pool, membership__season=season).exclude(requested_jersey_number=None).select_related("membership__member"):
            pending_this_season.setdefault(details.requested_jersey_number, []).append(details.membership.member)

        tiles = []
        for number in range(pool.min_number, pool.max_number + 1):
            reservation = reservations.get(number)
            if reservation is not None:
                tiles.append({"number": number, "state": "reserved", "holders": [], "note": reservation.note, "reservation": reservation})
            elif number in placed_this_season:
                tiles.append({"number": number, "state": "taken", "holders": placed_this_season[number], "note": "", "reservation": None})
            elif number in pending_this_season:
                tiles.append({"number": number, "state": "pending", "holders": pending_this_season[number], "note": "", "reservation": None})
            elif number in placed_previous_season:
                tiles.append({"number": number, "state": "previous", "holders": placed_previous_season[number], "note": "", "reservation": None})
            else:
                tiles.append({"number": number, "state": "available", "holders": [], "note": "", "reservation": None})
        return tiles


class NumberReservationCreateView(ClubStaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        pool = get_object_or_404(NumberPool.objects.filter(club=request.club), pk=request.POST.get("pool"))
        number = request.POST.get("number")

        form = NumberReservationForm(request.POST)
        if not form.is_valid() or not number or not number.isdigit() or not (pool.min_number <= int(number) <= pool.max_number):
            notify(request, f"e|{_('Could not reserve')}|{_('That number is not valid for this pool.')}")
            return redirect(f"{reverse('management:number_list')}?pool={pool.pk}")

        if NumberReservation.objects.filter(pool=pool, number=int(number)).exists():
            notify(request, f"e|{_('Could not reserve')}|{_('#%(number)s is already reserved.') % {'number': number}}")
            return redirect(f"{reverse('management:number_list')}?pool={pool.pk}")

        reservation = form.save(commit=False)
        reservation.club = request.club
        reservation.pool = pool
        reservation.number = int(number)
        reservation.reserved_by = request.user
        reservation.save()

        notify(request, f"s|{_('Number reserved')}|{_('#%(number)s in “%(pool)s” has been reserved.') % {'number': number, 'pool': pool.name}}")
        return redirect(f"{reverse('management:number_list')}?pool={pool.pk}")


class NumberReservationReleaseView(ClubStaffRequiredMixin, View):
    def post(self, request, pk):
        reservation = get_object_or_404(NumberReservation.objects.filter(club=request.club), pk=pk)
        pool = reservation.pool
        number = reservation.number
        reservation.delete()

        notify(request, f"s|{_('Reservation released')}|{_('#%(number)s in “%(pool)s” is available again.') % {'number': number, 'pool': pool.name}}")
        return redirect(f"{reverse('management:number_list')}?pool={pool.pk}")


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
            # Pending review rolls into the Drafts chip -- see _news_preview.html
            # for how it's still told apart there (its own badge colour), rather
            # than adding a fifth, rarely-used chip next to the four D8 already
            # fits on one line.
            queryset = queryset.filter(status__in=[News.Status.DRAFT, News.Status.PENDING_REVIEW])
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
                "draft": base.filter(status__in=[News.Status.DRAFT, News.Status.PENDING_REVIEW]).count(),
                "scheduled": base.filter(status=News.Status.PUBLISHED, published_at__gt=now).count(),
                "published": base.filter(status=News.Status.PUBLISHED, published_at__lte=now).count(),
            },
            news_item=selected_item,
            can_edit=selected_item.can_edit if selected_item else False,
            can_publish=can_publish_news(user, club),
            publish_form=NewsPublishForm(),
            photo_upload_form=NewsPhotoUploadForm(),
            # Markdown source -> sanitised HTML, same renderer the public API/
            # mobile's own article page use (news.services.render_body_html)
            # -- see _news_preview.html's own comment for why this can't just
            # render news_item.body directly.
            article_body_nl=render_body_html(selected_item.body) if selected_item else "",
            article_body_en=render_body_html(selected_item.effective_body_en) if selected_item else "",
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
            article_body_nl=render_body_html(self.object.body),
            article_body_en=render_body_html(self.object.effective_body_en),
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


class NewsSubmitForReviewView(NewsEditRequiredMixin, View):
    """A non-editor author's hand-off to an editor/admin -- the button
    _news_preview.html shows instead of Publish when can_publish is False.
    Gated the same as editing (can_edit_news, broad while it's a draft), not
    can_publish_news -- that's exactly who this exists for."""

    def get_news_item(self):
        return get_object_or_404(News.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk):
        news_item = self.get_news_item()
        news_item.submit_for_review()
        notify_editors_of_pending_review(news_item)

        body = _("“%(news)s” is ready for review.") % {"news": news_item}
        notify(request, f"s|{_('Sent for review')}|{body}")
        return redirect("management:news_detail", pk=news_item.pk)


class NewsPublishView(NewsPublisherRequiredMixin, RedirectOnInvalidMixin, FormView):
    form_class = NewsPublishForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:news_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def form_valid(self, form):
        news_item = get_object_or_404(News.objects.filter(club=self.request.club), pk=self.kwargs["pk"])
        news_item.publish(at=form.cleaned_data["published_at"])

        if not form.cleaned_data["notify_members"]:
            # Opted out entirely, not just delayed -- news.management.commands.
            # notify_published_news's periodic sweep skips anything with notified_at
            # already set, whatever the reason, so marking it now keeps it from ever
            # picking this one up. publish() above already saved with its own
            # update_fields=["status", "published_at"], so this needs its own save.
            news_item.notified_at = timezone.now()
            news_item.save(update_fields=["notified_at"])
        elif not news_item.is_scheduled:
            # Publish now, not ahead of time -- the common case. Dispatch right away
            # rather than leaving it to notify_published_news's periodic sweep (every
            # 15 minutes): the old Celery dispatch's own "eta in the past just runs
            # right away" behavior meant this case was already effectively immediate,
            # and waiting up to 15 minutes to notify anyone about a post that's live
            # *right now* would read as broken, not just slower.
            dispatch_send_publish_notification(str(news_item.pk))
        # else: genuinely scheduled ahead (published_at in the future) -- leave
        # notified_at null and let the sweep pick it up once published_at has passed.

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


class NewsPhotoSetFocalPointView(NewsEditRequiredMixin, View):
    """Where object-fit: cover centres the crop for this photo -- see NewsPhoto.
    focal_x/focal_y's own docstring. ``_news_preview.html``'s own click-to-position
    overlay posts the percentages here; validated and clamped server-side too,
    since nothing stops a handcrafted request from sending nonsense."""

    def get_news_item(self):
        return get_object_or_404(News.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def post(self, request, pk, photo_pk):
        news_item = self.get_news_item()
        photo = get_object_or_404(NewsPhoto, pk=photo_pk, news_item=news_item)

        try:
            focal_x = int(request.POST["focal_x"])
            focal_y = int(request.POST["focal_y"])
        except (KeyError, ValueError):
            notify(request, f"e|{_('Could not set focal point')}|{_('That was not a valid position.')}")
            return redirect("management:news_detail", pk=news_item.pk)

        photo.focal_x = max(0, min(100, focal_x))
        photo.focal_y = max(0, min(100, focal_y))
        photo.save(update_fields=["focal_x", "focal_y"])

        notify(request, f"s|{_('Focal point set')}|{_('The photo will crop around that point wherever it shows up cropped.')}")
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

    def _list_groups(self, events, show_past):
        """The shared This week/Next week/by-month agenda grouping (events.
        services.calendar.agenda_groups, also behind mobile.views.CalendarView
        and mobile.coach_views.CoachScheduleView) -- applied to whichever page
        of `events` is actually being shown, so pagination and grouping don't
        fight each other. Past mode (show_past=1, already descending) skips
        the this/next-week special-casing -- those labels only make sense for
        what's ahead -- and just groups straight into months, most recent
        first."""
        return agenda_groups(events, show_past=show_past)

    def get_context_data(self, **kwargs):
        club, user = self.request.club, self.request.user
        view_mode = self.request.GET.get("view", "calendar")
        range_kind = self.request.GET.get("range", "week")
        anchor = self._anchor_date()
        selected_season = selected_season_from_request(self.request, club)
        show_past = self.request.GET.get("show_past") == "1"

        calendar, calendar_nav = (None, None)
        if view_mode == "calendar":
            calendar, calendar_nav = self._calendar_context(range_kind, anchor, selected_season)

        context = super().get_context_data(
            seasons=Season.objects.filter(club=club).order_by("-start_date"),
            selected_season=selected_season,
            selected_kind=self.request.GET.get("kind", ""),
            show_past=show_past,
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

        if view_mode == "list":
            context["list_this_week"], context["list_next_week"], context["list_months"] = self._list_groups(context["events"], show_past)

        return context


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
        pending_signups = []
        if referee_management_needed:
            referees = list(event.referees.select_related("member", "assigned_by").order_by("member__last_name", "member__first_name"))
            referees_full = len(referees) >= event.max_referees
            if can_manage_referees and not referees_full:
                for candidate in eligible_referees(event):
                    conflicts = conflicting_events(candidate, event)
                    candidate.has_conflict = bool(conflicts)
                    candidate.conflict_titles = ", ".join(conflict.title for conflict in conflicts)
                    referee_candidates.append(candidate)
            if can_manage_referees:
                pending_signups = list(event.referee_signups.filter(status=RefereeSignup.Status.INVITED).select_related("member"))

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
            pending_signups=pending_signups,
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
    plain name; address from the club's own legal address, or its home
    Location when that's blank -- see club.services.invoicing.
    resolve_document_address -- never this specific event's, so the form
    still reads right even if called from a page where the event's own
    location happens to be blank) plus this game's details, referees and
    their fee/km breakdown, and blank signature lines."""

    def get(self, request, pk):
        event = get_object_or_404(Event.objects.filter(club=request.club).prefetch_related("teams", "referees__member"), pk=pk)
        document_address = resolve_document_address(request.club)
        referees = list(event.referees.all())
        context = {"club": request.club, "event": event, "referees": referees, "document_address": document_address, "grand_total": sum((referee.total_payable for referee in referees), Decimal("0"))} | referee_form_colors(request.club)

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


def referee_workload_stats(club):
    """Games refereed (and fees paid) per member this season -- external
    referees excluded (member is None for those, nothing to group by).
    Current season only, same scope as everything else on this dashboard
    being "the season we're in", not all-time history. Ordered by games
    descending -- the dashboard's own bar chart and table both read off
    this directly, no separate sort."""
    season = current_season(club)
    queryset = EventReferee.objects.filter(event__club=club, member__isnull=False)
    if season is not None:
        # By date range, not event__season=season -- most events are created
        # with that field left blank (help_text: "derived from the start
        # date when left blank"), so matching the FK directly would silently
        # exclude almost everything.
        queryset = queryset.filter(event__start__date__gte=season.start_date, event__start__date__lte=season.end_date)
    return list(
        queryset.values("member__id", "member__first_name", "member__last_name")
        .annotate(games=Count("id"), total_fees=Sum("fee"))
        .order_by("-games", "member__last_name")
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
            game.pending_signups = list(game.referee_signups.filter(status=RefereeSignup.Status.INVITED).select_related("member")) if not game.referees_full else []
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

        stats = referee_workload_stats(club)
        kpi_active_referees = len(stats)
        kpi_total_assignments = sum(row["games"] for row in stats)
        kpi_avg_games_per_referee = round(kpi_total_assignments / kpi_active_referees, 1) if kpi_active_referees else 0
        # Top 15 for the chart's own readability -- fees ride along per bar
        # for the tooltip (see referee_management.html's extra_body) rather
        # than a separate list underneath, to keep this a compact single row
        # next to the KPIs, not a second tall section pushing the actual
        # games-needing-a-referee list off screen.
        charts = {
            "referee_games": {
                "labels": [f"{row['member__first_name']} {row['member__last_name']}" for row in stats[:15]],
                "games": [row["games"] for row in stats[:15]],
                "fees": [float(row["total_fees"] or 0) for row in stats[:15]],
            }
        }

        return super().get_context_data(
            games=games,
            range_choice=range_choice,
            kpi_total=len(games),
            kpi_no_referee=kpi_no_referee,
            kpi_understaffed=kpi_understaffed,
            kpi_fully_staffed=kpi_fully_staffed,
            kpi_fees_pending=kpi_fees_pending,
            referee_stats=stats,
            kpi_active_referees=kpi_active_referees,
            kpi_total_assignments=kpi_total_assignments,
            kpi_avg_games_per_referee=kpi_avg_games_per_referee,
            charts=charts,
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
        # A deliberately-planned single event, not every Event row that ends up
        # created (see notify_new_event's own docstring for why a recurring
        # series' occurrences and bulk fixture imports aren't wired to this).
        dispatch_notify_new_event(str(self.object.pk))
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


# --- Shop (ShopManagerRequiredMixin: a club ADMIN, or anyone holding a
# ShopManager grant -- see club.mixins/club.services.access) -----------------


class ShopToggleView(ShopManagerRequiredMixin, View):
    """The shop open/closed switch on the Products page -- see Club.shop_open,
    checked by shop.services.checkout.place_order before it lets anyone order."""

    def post(self, request):
        club = request.club
        club.shop_open = not club.shop_open
        club.save(update_fields=["shop_open"])
        if club.shop_open:
            notify(request, f"s|{_('Shop opened')}|{_('Members can now place orders.')}")
        else:
            notify(request, f"w|{_('Shop closed')}|{_('Members can no longer place orders.')}")
        return redirect("management:product_list")


class ProductListView(ShopManagerRequiredMixin, ListView):
    template_name = "management/product_list.html"
    context_object_name = "products"

    def get_queryset(self):
        return Product.objects.filter(club=self.request.club).select_related("category", "season")

    def get_context_data(self, **kwargs):
        categories = list(ProductCategory.objects.filter(club=self.request.club))
        for category in categories:
            category.edit_form = ProductCategoryForm(instance=category)

        context = super().get_context_data(categories=categories, category_form=ProductCategoryForm(), **kwargs)
        sold = quantity_sold_by_product(self.request.club)
        for product in context["products"]:
            product.sold_count = sold.get(product.pk, 0)
        return context


class ProductCreateView(ShopManagerRequiredMixin, View):
    """Product + its first batch of variants and registrant-discount tiers
    in one submit -- the standalone "Add variant" modal
    (ProductVariantCreateView) still exists for adding more variants later,
    but making someone save the product first just to give it sizes (or
    tiers) was needless friction. A plain View, not CreateView: this needs
    two more independent forms (the variant and tier formsets) alongside
    ProductForm, which CreateView's single-form flow doesn't have room
    for."""

    template_name = "management/product_form.html"

    def get_forms(self, data=None):
        # Product.clean() rejects a category from another club by comparing
        # against self.club_id -- on a brand-new instance that's still None
        # until ClubScopedModel.save() auto-assigns it, which only happens
        # *after* validation. Pre-set it here so full_clean() sees the real
        # club, not None -- same fix as EventCreateView.get_form_kwargs.
        return (
            ProductForm(data, files=self.request.FILES or None, club=self.request.club, instance=Product(club=self.request.club)),
            ProductVariantFormSet(data, prefix="variants"),
            ProductRegistrantDiscountTierFormSet(data, prefix="tiers"),
        )

    def render_form(self, form, variant_formset, tier_formset):
        return render(self.request, self.template_name, {"form": form, "variant_formset": variant_formset, "tier_formset": tier_formset})

    def get(self, request, *args, **kwargs):
        form, variant_formset, tier_formset = self.get_forms()
        return self.render_form(form, variant_formset, tier_formset)

    def post(self, request, *args, **kwargs):
        form, variant_formset, tier_formset = self.get_forms(request.POST)
        if not form.is_valid() or not variant_formset.is_valid() or not tier_formset.is_valid():
            return self.render_form(form, variant_formset, tier_formset)

        with transaction.atomic():
            product = form.save(commit=False)
            product.club = request.club
            product.save()
            ProductVariant.objects.bulk_create(
                [ProductVariant(product=product, name=row["name"], price=row.get("price")) for row in variant_formset.cleaned_data if row.get("name")]
            )
            ProductRegistrantDiscountTier.objects.bulk_create(
                [ProductRegistrantDiscountTier(product=product, min_registrants=row["min_registrants"], discount_type=row["discount_type"], discount_amount=row["discount_amount"]) for row in tier_formset.cleaned_data if row.get("min_registrants")]
            )

        notify(request, f"s|{_('Product created')}|{_('“%(product)s” created.') % {'product': product}}")
        return redirect("management:product_list")


class ProductUpdateView(ShopManagerRequiredMixin, View):
    """A plain View, not UpdateView, for the same reason as ProductCreateView
    above -- the registrant-discount-tier formset needs to be replaced in
    one atomic step alongside ProductForm's own save, on both create and
    edit (unlike variants, which only get their bulk-add formset on create;
    editing one is a separate modal-based flow, since a variant has its own
    identity OrderLine can reference -- a tier has no such external
    reference, so an edit just discards and recreates the whole set)."""

    template_name = "management/product_form.html"

    def get_object(self):
        return get_object_or_404(Product.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_forms(self, product, data=None):
        tier_kwargs = {"prefix": "tiers"}
        if data is None:
            tier_kwargs["initial"] = [{"min_registrants": tier.min_registrants, "discount_type": tier.discount_type, "discount_amount": tier.discount_amount} for tier in product.registrant_discount_tiers.all()]
        return (
            ProductForm(data, files=self.request.FILES or None, club=self.request.club, instance=product),
            ProductRegistrantDiscountTierFormSet(data, **tier_kwargs),
        )

    def render_form(self, product, form, tier_formset):
        variants = list(product.variants.all())
        sold_by_variant = quantity_sold_by_variant(product)
        for variant in variants:
            variant.edit_form = ProductVariantForm(instance=variant)
            variant.sold_count = sold_by_variant.get(variant.pk, 0)
        context = {
            "object": product,
            "form": form,
            "tier_formset": tier_formset,
            "update_view": True,
            "variants": variants,
            "variant_form": ProductVariantForm(),
            "product_sold_count": quantity_sold_by_product(self.request.club).get(product.pk, 0),
        }
        return render(self.request, self.template_name, context)

    def get(self, request, *args, **kwargs):
        product = self.get_object()
        form, tier_formset = self.get_forms(product)
        return self.render_form(product, form, tier_formset)

    def post(self, request, *args, **kwargs):
        product = self.get_object()
        form, tier_formset = self.get_forms(product, request.POST)
        if not form.is_valid() or not tier_formset.is_valid():
            return self.render_form(product, form, tier_formset)

        with transaction.atomic():
            form.save()
            product.registrant_discount_tiers.all().delete()
            ProductRegistrantDiscountTier.objects.bulk_create(
                [ProductRegistrantDiscountTier(product=product, min_registrants=row["min_registrants"], discount_type=row["discount_type"], discount_amount=row["discount_amount"]) for row in tier_formset.cleaned_data if row.get("min_registrants")]
            )

        notify(request, f"s|{_('Product updated')}|{_('“%(product)s” updated.') % {'product': product}}")
        return redirect("management:product_list")


class ProductCategoryCreateView(ShopManagerRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Add category" modal on the products page --
    no standalone template, same shape as ProductVariantCreateView."""

    form_class = ProductCategoryForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:product_list"

    def form_valid(self, form):
        form.instance.club = self.request.club
        form.save()
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Category added')}|{_('“%(category)s” added.') % {'category': form.instance.name}}")
        return response

    def get_success_url(self):
        return reverse("management:product_list")


class ProductCategoryUpdateView(ShopManagerRequiredMixin, RedirectOnInvalidMixin, UpdateView):
    """Reachable only via a category's own "Edit" modal on the products page --
    same reasoning as ProductCategoryCreateView."""

    model = ProductCategory
    form_class = ProductCategoryForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:product_list"

    def get_queryset(self):
        return ProductCategory.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Category updated')}|{_('“%(category)s” updated.') % {'category': self.object.name}}")
        return response

    def get_success_url(self):
        return reverse("management:product_list")


class ProductCategoryDeleteView(ShopManagerRequiredMixin, View):
    """No PROTECT to work around -- Product.category is SET_NULL, so deleting
    a category just un-categorises whatever it held (unlike Product/Variant,
    which only ever get deactivated). The two system categories (Player/
    Volunteer, registration_kind set) are the exception -- shop.signals'
    own pre_delete receiver would block the .delete() call regardless, but
    checking here first means the modal that's reachable at all only shows
    for an ordinary category (see product_list.html), and a stray direct
    POST gets a friendly notice instead of a raw exception."""

    def post(self, request, pk):
        category = get_object_or_404(ProductCategory.objects.filter(club=request.club), pk=pk)
        if category.registration_kind:
            notify(request, f"w|{_('Cannot delete')}|{_('“%(category)s” is a system category used by registration and cannot be deleted.') % {'category': category.name}}")
            return redirect("management:product_list")
        name = category.name
        category.delete()
        notify(request, f"s|{_('Category deleted')}|{_('“%(category)s” deleted.') % {'category': name}}")
        return redirect("management:product_list")


class ProductToggleActiveView(ShopManagerRequiredMixin, View):
    """No delete view -- Product is PROTECTed by OrderLine, so a product that's
    ever been ordered can't be hard-deleted. Deactivating hides it from members
    instead."""

    def post(self, request, pk):
        product = get_object_or_404(Product.objects.filter(club=request.club), pk=pk)
        product.is_active = not product.is_active
        product.save(update_fields=["is_active"])
        if product.is_active:
            notify(request, f"s|{_('Product activated')}|{_('“%(product)s” is now active.') % {'product': product}}")
        else:
            notify(request, f"w|{_('Product deactivated')}|{_('“%(product)s” is no longer active.') % {'product': product}}")
        return redirect("management:product_list")


class ProductVariantCreateView(ShopManagerRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Add variant" modal on the product's own edit
    page -- no standalone template, same shape as NewsPhotoUploadView."""

    form_class = ProductVariantForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:product_update"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def get_product(self):
        return get_object_or_404(Product.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def form_valid(self, form):
        product = self.get_product()
        form.instance.product = product
        form.save()
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Variant added')}|{_('“%(variant)s” added to %(product)s.') % {'variant': form.instance.name, 'product': product}}")
        return response

    def get_success_url(self):
        return reverse("management:product_update", kwargs={"pk": self.kwargs["pk"]})


class ProductVariantUpdateView(ShopManagerRequiredMixin, RedirectOnInvalidMixin, UpdateView):
    """Reachable only via a variant's own "Edit" modal on the product's edit
    page -- same reasoning as ProductVariantCreateView."""

    model = ProductVariant
    form_class = ProductVariantForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:product_update"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["product_pk"]}

    def get_queryset(self):
        return ProductVariant.objects.filter(product__club=self.request.club, product__pk=self.kwargs["product_pk"])

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Variant updated')}|{_('“%(variant)s” updated.') % {'variant': self.object.name}}")
        return response

    def get_success_url(self):
        return reverse("management:product_update", kwargs={"pk": self.kwargs["product_pk"]})


class ProductVariantToggleActiveView(ShopManagerRequiredMixin, View):
    """No delete view -- same PROTECT-by-OrderLine reasoning as
    ProductToggleActiveView, one level down."""

    def post(self, request, product_pk, pk):
        variant = get_object_or_404(ProductVariant.objects.filter(product__club=request.club, product__pk=product_pk), pk=pk)
        variant.is_active = not variant.is_active
        variant.save(update_fields=["is_active"])
        if variant.is_active:
            notify(request, f"s|{_('Variant activated')}|{_('“%(variant)s” is now active.') % {'variant': variant.name}}")
        else:
            notify(request, f"w|{_('Variant deactivated')}|{_('“%(variant)s” is no longer active.') % {'variant': variant.name}}")
        return redirect("management:product_update", pk=product_pk)


class DiscountListView(ShopManagerRequiredMixin, ListView):
    template_name = "management/discount_list.html"
    context_object_name = "discounts"

    def get_queryset(self):
        return Discount.objects.filter(club=self.request.club)


class DiscountCreateView(ShopManagerRequiredMixin, CreateView):
    model = Discount
    form_class = DiscountForm
    template_name = "management/discount_form.html"

    def form_valid(self, form):
        form.instance.club = self.request.club
        response = super().form_valid(form)
        body = _("“%(discount)s” created.") % {"discount": self.object}
        notify(self.request, f"s|{_('Discount created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:discount_list")


class DiscountUpdateView(ShopManagerRequiredMixin, UpdateView):
    model = Discount
    form_class = DiscountForm
    template_name = "management/discount_form.html"

    def get_queryset(self):
        return Discount.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(discount)s” updated.") % {"discount": self.object}
        notify(self.request, f"s|{_('Discount updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:discount_list")

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class DiscountToggleActiveView(ShopManagerRequiredMixin, View):
    def post(self, request, pk):
        discount = get_object_or_404(Discount.objects.filter(club=request.club), pk=pk)
        discount.is_active = not discount.is_active
        discount.save(update_fields=["is_active"])
        if discount.is_active:
            notify(request, f"s|{_('Discount activated')}|{_('“%(discount)s” is now active.') % {'discount': discount}}")
        else:
            notify(request, f"w|{_('Discount deactivated')}|{_('“%(discount)s” is no longer active.') % {'discount': discount}}")
        return redirect("management:discount_list")


class VoucherListView(ShopManagerRequiredMixin, ListView):
    template_name = "management/voucher_list.html"
    context_object_name = "vouchers"

    def get_queryset(self):
        return Voucher.objects.filter(club=self.request.club).select_related("issued_to")


class VoucherCreateView(ShopManagerRequiredMixin, CreateView):
    model = Voucher
    form_class = VoucherForm
    template_name = "management/voucher_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["club"] = self.request.club
        return kwargs

    def get_form(self, form_class=None):
        # Set before is_valid() runs, not in form_valid() -- Voucher.clean()'s
        # member_fields check (issued_to) needs self.club_id already resolved
        # at full_clean() time, which happens inside is_valid(), earlier than
        # form_valid() ever runs.
        form = super().get_form(form_class)
        form.instance.club = self.request.club
        return form

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(voucher)s” created.") % {"voucher": self.object}
        notify(self.request, f"s|{_('Voucher created')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:voucher_detail", args=[self.object.pk])


class VoucherUpdateView(ShopManagerRequiredMixin, UpdateView):
    model = Voucher
    form_class = VoucherForm
    template_name = "management/voucher_form.html"

    def get_queryset(self):
        return Voucher.objects.filter(club=self.request.club)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["club"] = self.request.club
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        body = _("“%(voucher)s” updated.") % {"voucher": self.object}
        notify(self.request, f"s|{_('Voucher updated')}|{body}")
        return response

    def get_success_url(self):
        return reverse("management:voucher_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class VoucherDetailView(ShopManagerRequiredMixin, DetailView):
    model = Voucher
    template_name = "management/voucher_detail.html"
    context_object_name = "voucher"

    def get_queryset(self):
        return Voucher.objects.filter(club=self.request.club).select_related("issued_to")

    def get_context_data(self, **kwargs):
        return super().get_context_data(consumption_form=VoucherConsumptionForm(), history=voucher_history(self.object), **kwargs)


class VoucherConsumptionCreateView(ShopManagerRequiredMixin, RedirectOnInvalidMixin, FormView):
    """The "Record consumption" modal on a voucher's own detail page -- see
    shop.services.vouchers.record_manual_consumption. No standalone template,
    same shape as ProductVariantCreateView."""

    form_class = VoucherConsumptionForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:voucher_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def get_voucher(self):
        return get_object_or_404(Voucher.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def form_valid(self, form):
        voucher = self.get_voucher()
        try:
            record_manual_consumption(voucher, amount=form.cleaned_data["amount"], note=form.cleaned_data["note"], recorded_by=self.request.user)
        except PaymentError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Consumption recorded')}|{_('“%(voucher)s” recorded.') % {'voucher': voucher}}")
        return response

    def get_success_url(self):
        return reverse("management:voucher_detail", args=[self.kwargs["pk"]])


class VoucherConsumptionDeleteView(ShopManagerRequiredMixin, View):
    def post(self, request, pk, consumption_pk):
        consumption = get_object_or_404(VoucherConsumption.objects.filter(voucher__club=request.club, voucher__pk=pk).select_related("voucher"), pk=consumption_pk)
        voucher = consumption.voucher
        delete_manual_consumption(consumption)
        notify(request, f"s|{_('Consumption removed')}|{_('Consumption removed from “%(voucher)s”.') % {'voucher': voucher}}")
        return redirect("management:voucher_detail", pk=pk)


class OrderListView(ShopManagerRequiredMixin, ListView):
    """Default view hides closed orders (Order.is_closed -- delivered *and*
    paid, see its own docstring for why both) so the list reads as "what
    still needs attention" rather than the club's entire order history.
    ?show=all includes them too. ?payment_status=/?fulfillment_status=
    narrow independently -- once either is set, the closed-hiding above is
    skipped entirely (picking a specific fulfillment status and *also*
    silently dropping paid orders from it would be surprising). ?q=
    searches by purchaser first/last/family name or the order's own number,
    same search shape as MembershipListView/InvoiceListView's own ?q=."""

    template_name = "management/order_list.html"
    context_object_name = "orders"

    def get_queryset(self):
        orders = Order.objects.filter(club=self.request.club).select_related("purchaser")

        payment_status = self.request.GET.get("payment_status", "")
        fulfillment_status = self.request.GET.get("fulfillment_status", "")
        if payment_status:
            orders = orders.filter(payment_status=payment_status)
        if fulfillment_status:
            orders = orders.filter(fulfillment_status=fulfillment_status)
        if not payment_status and not fulfillment_status and self.request.GET.get("show") != "all":
            orders = orders.exclude(fulfillment_status=Order.FulfillmentStatus.DELIVERED, payment_status=Order.PaymentStatus.PAID)

        search = self.request.GET.get("q", "").strip()
        if search:
            orders = (
                orders.filter(purchaser__first_name__icontains=search)
                | orders.filter(purchaser__last_name__icontains=search)
                | orders.filter(purchaser__family_memberships__family__name__icontains=search)
                | orders.filter(purchaser__family_memberships__family__memberships__member__last_name__icontains=search)
                | orders.filter(number__icontains=search)
            )
            orders = orders.distinct()

        return orders

    def get_context_data(self, **kwargs):
        # order_kpis reads every order for the club regardless of the filters
        # above -- the KPI strip is "how's the shop doing overall", not "how
        # many of the filtered rows below".
        # Every merchandise product in the club, with how many of its lines are
        # still waiting to be sent to a manufacturer -- the "Download order
        # list" modal's own checklist (order_list.html).
        production_products = (
            Product.objects.filter(club=self.request.club, product_type=Product.ProductType.MERCHANDISE)
            .annotate(
                pending_count=Count("order_items", filter=Q(order_items__production_status=ProductionStatus.PENDING) & ~Q(order_items__order__fulfillment_status=Order.FulfillmentStatus.CANCELLED)),
                in_production_count=Count("order_items", filter=Q(order_items__production_status=ProductionStatus.IN_PRODUCTION) & ~Q(order_items__order__fulfillment_status=Order.FulfillmentStatus.CANCELLED)),
            )
            .order_by("name")
        )
        return super().get_context_data(
            payment_status_choices=Order.PaymentStatus.choices,
            fulfillment_status_choices=Order.FulfillmentStatus.choices,
            selected_payment_status=self.request.GET.get("payment_status", ""),
            selected_fulfillment_status=self.request.GET.get("fulfillment_status", ""),
            show_all=self.request.GET.get("show") == "all",
            search=self.request.GET.get("q", "").strip(),
            kpis=order_kpis(self.request.club),
            bulk_mark_paid_form=OrderBulkMarkPaidForm(),
            bulk_ready_for_pickup_form=OrderBulkMarkReadyForPickupForm(),
            production_products=production_products,
            **kwargs,
        )


def _selected_merchandise_products(request):
    """Product queryset for the "Download order list" modal's two actions
    below -- club-scoped, merchandise-only, resolved from ?product_ids=.
    None (with a flashed error already sent) if nothing was selected."""
    product_ids = request.POST.getlist("product_ids")
    if not product_ids:
        notify(request, f"e|{_('No products selected')}|{_('Select at least one product.')}")
        return None
    return Product.objects.filter(club=request.club, product_type=Product.ProductType.MERCHANDISE, pk__in=product_ids)


class OrderProductionExportView(ShopManagerRequiredMixin, View):
    """The Orders list's own "Download & mark in production" action -- exports
    every not-yet-submitted OrderLine (shop.services.production.
    pending_production_lines) for the selected merchandise products to one
    .xlsx (one sheet per product, management.shop_export.build_production_export)
    and marks them IN_PRODUCTION so a repeat download doesn't resend what's
    already gone out. The file is built before anything is marked, so a
    failed export never marks lines sent that weren't. Manual correction
    lives on the line item's own edit modal (OrderLineEditForm); to get a
    fresh copy of what's already out without sending anything new, see
    OrderProductionReprintView below.

    Redirects to a reloaded order_list rather than returning the file
    directly -- a browser doesn't navigate away from a page when a form
    POST's response is a file download, so the Production column would
    otherwise look stale even though the marking succeeded. The file itself
    is stashed (management.shop_export.stash_production_export) and fetched
    by OrderProductionDownloadView, which the reloaded page's own script
    triggers automatically."""

    def post(self, request):
        products = _selected_merchandise_products(request)
        if products is None:
            return redirect("management:order_list")

        lines = list(pending_production_lines(products))
        if not lines:
            notify(request, f"e|{_('Nothing to export')}|{_('No pending items for the selected products.')}")
            return redirect("management:order_list")

        workbook = build_production_export(lines)
        mark_lines_in_production(lines)

        token = stash_production_export(request.club, workbook, "order-production-list.xlsx")
        notify(request, f"s|{_('Marked in production')}|{_('%(count)d item(s) marked in production. Your download should start automatically.') % {'count': len(lines)}}")
        return redirect(f"{reverse('management:order_list')}?production_download={token}")


class OrderProductionDownloadView(ShopManagerRequiredMixin, View):
    """Serves the file OrderProductionExportView just stashed -- split into
    its own GET step for the reason explained in that view's own docstring.
    One-time use: the token is popped (not just read) so refreshing the
    order_list page a second time doesn't re-trigger a stale download."""

    def get(self, request, token):
        stashed = pop_production_export(request.club, token)
        if stashed is None:
            raise Http404
        filename, content = stashed
        response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class OrderProductionReprintView(ShopManagerRequiredMixin, View):
    """The Orders list's own "Redownload in production" action -- a fresh
    copy of whatever's currently IN_PRODUCTION (shop.services.production.
    in_production_lines) for the selected merchandise products, unchanged:
    no line gets marked anything by this, it's purely for when the original
    export is lost or a manufacturer needs it resent."""

    def post(self, request):
        products = _selected_merchandise_products(request)
        if products is None:
            return redirect("management:order_list")

        lines = list(in_production_lines(products))
        if not lines:
            notify(request, f"e|{_('Nothing to download')}|{_('No items currently in production for the selected products.')}")
            return redirect("management:order_list")

        workbook = build_production_export(lines)

        response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = 'attachment; filename="order-in-production-list.xlsx"'
        workbook.save(response)
        return response


class OrderBulkMarkPaidView(ShopManagerRequiredMixin, View):
    """The Orders list's own "Mark selected paid" -- one shared method
    (OrderBulkMarkPaidForm), no reference: there's no one reference that
    would meaningfully apply across a whole batch of distinct orders, and no
    reasonable place to enter 50 different ones in the same submit -- use the
    single-order modal (OrderMarkPaidView) for that. Cancelled orders are
    excluded rather than erroring -- their own checkbox isn't even rendered
    (order_list.html), so this only matters for a stale selection left over
    from before a page reload. Settles whatever's actually still due
    (shop.services.payments.amount_due), not blindly the full total -- an
    order with an existing partial payment (e.g. a voucher already applied
    via OrderAddPaymentView) only gets topped up, never double-charged.
    Already-fully-paid orders are silently skipped."""

    def post(self, request):
        next_url = request.POST.get("next")
        redirect_url = next_url if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()) else reverse("management:order_list")

        ids = request.POST.getlist("order_ids")
        if not ids:
            notify(request, f"e|{_('No orders selected')}|{_('Select at least one order.')}")
            return redirect(redirect_url)

        form = OrderBulkMarkPaidForm(request.POST)
        if not form.is_valid():
            notify(request, f"e|{_('Could not record payment')}|{_('Choose a valid payment method.')}")
            return redirect(redirect_url)

        method = form.cleaned_data["method"]
        orders = Order.objects.filter(pk__in=ids, club=request.club).exclude(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
        count = 0
        with transaction.atomic():
            for order in orders:
                due = amount_due(order)
                if due <= 0:
                    continue
                record_shop_payment(order, amount=due, method=method)
                count += 1

        notify(request, f"s|{_('Orders marked paid')}|{_('%(count)d order(s) marked paid.') % {'count': count}}")
        return redirect(redirect_url)


class OrderBulkMarkReadyForPickupView(ShopManagerRequiredMixin, View):
    """The Orders list's own "Mark selected ready for pickup" -- one shared
    pickup_instructions (OrderBulkMarkReadyForPickupForm) applied to every
    selected order, exactly the "not retyping the same note 50 times" this
    exists for. Notifies each purchaser individually, same as the
    single-order action -- there's no batched "one email to everyone", every
    purchaser only ever hears about their own order."""

    def post(self, request):
        next_url = request.POST.get("next")
        redirect_url = next_url if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()) else reverse("management:order_list")

        ids = request.POST.getlist("order_ids")
        if not ids:
            notify(request, f"e|{_('No orders selected')}|{_('Select at least one order.')}")
            return redirect(redirect_url)

        form = OrderBulkMarkReadyForPickupForm(request.POST)
        if not form.is_valid():
            notify(request, f"e|{_('Could not mark ready for pickup')}|{_('Check the pickup instructions and try again.')}")
            return redirect(redirect_url)

        pickup_instructions = form.cleaned_data["pickup_instructions"]
        orders = Order.objects.filter(pk__in=ids, club=request.club).exclude(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
        count = 0
        for order in orders:
            order.pickup_instructions = pickup_instructions
            order.fulfillment_status = Order.FulfillmentStatus.READY_FOR_PICKUP
            order.save(update_fields=["fulfillment_status", "pickup_instructions"])
            dispatch_order_ready_for_pickup_notification(order)
            count += 1

        notify(request, f"s|{_('Orders ready for pickup')}|{_('%(count)d order(s) marked ready — purchasers notified.') % {'count': count}}")
        return redirect(redirect_url)


class OrderDetailView(ShopManagerRequiredMixin, DetailView):
    template_name = "management/order_detail.html"
    context_object_name = "order"

    def get_queryset(self):
        return Order.objects.filter(club=self.request.club).select_related("purchaser")

    def get_context_data(self, **kwargs):
        order = self.object
        lines = list(order.order_items.select_related("product", "variant", "beneficiary", "team"))
        for line in lines:
            line.edit_form = OrderLineEditForm(instance=line, club=self.request.club)
        applied_discounts = order.applied_discounts.select_related("discount")
        # Bound per-row so each payment's own "Edit" modal can render its own
        # form -- same reasoning as ProductUpdateView's variants.
        payments = list(order.payments.select_related("voucher").all())
        for payment in payments:
            payment.edit_form = PaymentEditForm(instance=payment)
        return super().get_context_data(
            lines=lines,
            applied_discounts=applied_discounts,
            payments=payments,
            amount_due=amount_due(order),
            mark_paid_form=OrderMarkPaidForm(),
            mark_ready_for_pickup_form=OrderMarkReadyForPickupForm(initial={"pickup_instructions": order.pickup_instructions}),
            add_payment_form=AddPaymentForm(club=self.request.club, order=order, prefix="add_payment", initial={"amount": amount_due(order)}),
            **kwargs,
        )


class OrderLineUpdateView(ShopManagerRequiredMixin, RedirectOnInvalidMixin, UpdateView):
    """The "Edit" modal on a line item row (order detail page) -- fixing a
    mistake after the fact (wrong size, wrong beneficiary, a forgotten
    number/name, or by-hand production_status correction), not a general
    line-item editor: there's no add/delete here, and product itself isn't
    editable (see OrderLineEditForm's own docstring). unit_price/line_total
    are recomputed from quantity and the (possibly changed) variant's own
    price rather than taken from the form, and the order's own total,
    payment_status and production_status are kept in sync with whatever
    changed."""

    model = OrderLine
    form_class = OrderLineEditForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:order_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["order_pk"]}

    def get_queryset(self):
        return OrderLine.objects.filter(order__club=self.request.club, order__pk=self.kwargs["order_pk"]).select_related("product", "order")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["club"] = self.request.club
        return kwargs

    def form_valid(self, form):
        line = form.save(commit=False)
        line.unit_price = line.variant.effective_price if line.variant_id else line.product.price
        line.line_total = line.unit_price * line.quantity
        line.save()

        order = line.order
        order.total = order_total(order)
        order.save(update_fields=["total"])
        sync_payment_status(order)
        sync_production_status(order)

        notify(self.request, f"s|{_('Line item updated')}|{_('“%(product)s” updated.') % {'product': line.product}}")
        return redirect("management:order_detail", pk=order.pk)

    def get_success_url(self):
        return reverse("management:order_detail", kwargs={"pk": self.kwargs["order_pk"]})


class OrderLineMarkReceivedView(ShopManagerRequiredMixin, View):
    """Quick one-click alternative to the "Edit" modal's production_status
    dropdown, next to a line already IN_PRODUCTION -- advancing to RECEIVED
    is the common case, not a correction, so it shouldn't need a modal.
    Plain status set, no state machine, same reasoning as
    OrderMarkDeliveredView: works regardless of the line's current status."""

    def post(self, request, order_pk, pk):
        line = get_object_or_404(OrderLine.objects.filter(order__club=request.club, order__pk=order_pk).select_related("order", "product"), pk=pk)
        mark_line_received(line)
        notify(request, f"s|{_('Marked received')}|{_('“%(product)s” is now marked received.') % {'product': line.product}}")
        return redirect("management:order_detail", pk=order_pk)


class PaymentUpdateView(ShopManagerRequiredMixin, RedirectOnInvalidMixin, UpdateView):
    """Reachable only via a payment's own "Edit" modal on the order detail
    page -- method and reference only, see PaymentEditForm's own docstring
    for why amount/status/paid_at stay out of reach."""

    model = Payment
    form_class = PaymentEditForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:order_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["order_pk"]}

    def get_queryset(self):
        return Payment.objects.filter(order__club=self.request.club, order__pk=self.kwargs["order_pk"])

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Payment updated')}|{_('Payment updated.')}")
        return response

    def get_success_url(self):
        return reverse("management:order_detail", kwargs={"pk": self.kwargs["order_pk"]})


class PaymentDeleteView(ShopManagerRequiredMixin, View):
    """No PROTECT to work around -- Payment is only ever referenced by its
    own Order (CASCADE the other way), so deleting one is a plain delete.

    Recomputes the order's payment_status from whatever confirmed Payments
    remain (shop.services.payments.sync_payment_status) -- covers "staff
    marked this paid by mistake" the same as before, but now also correctly
    drops PAID to PARTIALLY_PAID when deleting just one of several partial
    payments, rather than leaving it reading as settled. fulfillment_status
    is untouched either way (deleting a payment record says nothing about
    whether the item was handed over). Left alone if payment_status is
    already REFUNDED -- that's a deliberate, terminal state a payment row
    disappearing shouldn't resurrect. A deleted voucher payment restores its
    amount back onto the voucher's own available balance."""

    def post(self, request, order_pk, pk):
        payment = get_object_or_404(Payment.objects.filter(order__club=request.club, order__pk=order_pk).select_related("voucher"), pk=pk)
        order = payment.order
        voucher = payment.voucher
        amount = payment.amount
        was_confirmed = payment.status == Payment.PaymentStatus.CONFIRMED
        payment.delete()

        if voucher is not None and was_confirmed:
            voucher.consumed_amount = max(Decimal("0"), voucher.consumed_amount - amount)
            voucher.save(update_fields=["consumed_amount"])

        reverted_to_pending = False
        if order.payment_status != Order.PaymentStatus.REFUNDED:
            sync_payment_status(order)
            order.refresh_from_db(fields=["payment_status"])
            reverted_to_pending = order.payment_status == Order.PaymentStatus.PENDING

        if reverted_to_pending:
            notify(request, f"s|{_('Payment deleted')}|{_('Payment deleted -- order %(number)s has no payments left and is back to pending.') % {'number': order.number}}")
        else:
            notify(request, f"s|{_('Payment deleted')}|{_('Payment deleted.')}")
        return redirect("management:order_detail", pk=order_pk)


class OrderAddPaymentView(ShopManagerRequiredMixin, RedirectOnInvalidMixin, FormView):
    """The "Add payment" modal on an order's detail page -- records a
    payment of a specific amount and method, including by voucher. This is
    what a partial settlement actually looks like: a voucher covering part
    of the total (AddPaymentForm validates the voucher and amount), then a
    second submit with method=cash/bank_transfer/credit_card for whatever's
    still due. OrderMarkPaidView's "Mark paid" stays the one-click shortcut
    for the common "pay it all now" case; this is the flexible one behind
    it."""

    form_class = AddPaymentForm
    invalid_redirect_url_name = "management:order_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def get_order(self):
        return get_object_or_404(Order.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["club"] = self.request.club
        kwargs["order"] = self.get_order()
        kwargs["prefix"] = "add_payment"
        return kwargs

    def form_valid(self, form):
        order = self.get_order()
        try:
            record_shop_payment(
                order,
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data.get("reference", ""),
                voucher=form.cleaned_data.get("voucher"),
            )
        except PaymentError as error:
            notify(self.request, f"e|{_('Could not record payment')}|{error}")
            return redirect("management:order_detail", pk=order.pk)

        notify(self.request, f"s|{_('Payment recorded')}|{_('Payment recorded for order %(number)s.') % {'number': order.number}}")
        return redirect("management:order_detail", pk=order.pk)

    def get_success_url(self):
        return reverse("management:order_detail", kwargs={"pk": self.kwargs["pk"]})


class OrderMarkPaidView(ShopManagerRequiredMixin, View):
    """Reachable only via the "Mark paid" modal on the order detail page --
    creates a Payment row for whatever's still due (shop.services.payments.
    amount_due) and settles the order. There's no online payment for this
    shop, so this is how most orders get marked paid: someone on staff, on
    pickup. If a voucher already covered part of it (OrderAddPaymentView),
    this only tops up the remainder -- not the full order total again."""

    def post(self, request, pk):
        order = get_object_or_404(Order.objects.filter(club=request.club), pk=pk)
        form = OrderMarkPaidForm(request.POST)

        if not form.is_valid():
            for error in form.errors.values():
                notify(request, f"e|{_('Could not record payment')}|{' '.join(error)}")
            return redirect("management:order_detail", pk=pk)

        due = amount_due(order)
        if due <= 0:
            notify(request, f"e|{_('Nothing to collect')}|{_('Order %(number)s is already fully paid.') % {'number': order.number}}")
            return redirect("management:order_detail", pk=pk)

        record_shop_payment(order, amount=due, method=form.cleaned_data["method"], reference=form.cleaned_data["reference"])

        notify(request, f"s|{_('Order marked paid')}|{_('Order %(number)s is now paid.') % {'number': order.number}}")
        return redirect("management:order_detail", pk=pk)


class OrderMarkReadyForPickupView(ShopManagerRequiredMixin, View):
    """Reachable only via the "Mark ready for pickup" modal on the order
    detail page. Saves the (optional) pickup_instructions onto the order
    itself -- so it stays visible on the order afterwards, not just in the
    one-off notification -- then dispatches that notification. Plain status
    set, no state machine, same reasoning as OrderMarkDeliveredView: an
    order can be flagged ready regardless of its current payment state."""

    def post(self, request, pk):
        order = get_object_or_404(Order.objects.filter(club=request.club), pk=pk)
        form = OrderMarkReadyForPickupForm(request.POST)

        if not form.is_valid():
            for error in form.errors.values():
                notify(request, f"e|{_('Could not mark ready for pickup')}|{' '.join(error)}")
            return redirect("management:order_detail", pk=pk)

        order.pickup_instructions = form.cleaned_data["pickup_instructions"]
        order.fulfillment_status = Order.FulfillmentStatus.READY_FOR_PICKUP
        order.save(update_fields=["fulfillment_status", "pickup_instructions"])
        dispatch_order_ready_for_pickup_notification(order)

        notify(request, f"s|{_('Order ready for pickup')}|{_('%(purchaser)s has been notified order %(number)s is ready.') % {'purchaser': order.purchaser, 'number': order.number}}")
        return redirect("management:order_detail", pk=pk)


class OrderMarkDeliveredView(ShopManagerRequiredMixin, View):
    """Plain status set, no state machine -- picked up before formally marked
    paid can happen in real life, so this doesn't require PAID first."""

    def post(self, request, pk):
        order = get_object_or_404(Order.objects.filter(club=request.club), pk=pk)
        order.fulfillment_status = Order.FulfillmentStatus.DELIVERED
        order.save(update_fields=["fulfillment_status"])
        notify(request, f"s|{_('Order marked delivered')}|{_('Order %(number)s is now delivered.') % {'number': order.number}}")
        return redirect("management:order_detail", pk=pk)


class OrderCancelView(ShopManagerRequiredMixin, View):
    def post(self, request, pk):
        order = get_object_or_404(Order.objects.filter(club=request.club), pk=pk)
        order.fulfillment_status = Order.FulfillmentStatus.CANCELLED
        order.save(update_fields=["fulfillment_status"])
        notify(request, f"w|{_('Order cancelled')}|{_('Order %(number)s has been cancelled.') % {'number': order.number}}")
        return redirect("management:order_detail", pk=pk)


class InvoicePdfView(ShopManagerRequiredMixin, View):
    def get(self, request, pk):
        order = get_object_or_404(Order.objects.filter(club=request.club), pk=pk)
        invoice = get_object_or_404(Invoice, order=order, club=request.club)

        try:
            pdf = render_invoice_pdf(invoice)
        except ShopInvoicePDFError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return redirect("management:order_detail", pk=pk)

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
        return response


def _order_invoice_status(order):
    # An invoice is fundamentally about money -- payment_status, not
    # fulfillment_status, same reasoning _dues_invoice_status (below) already
    # has nothing but payment to go on. The order's own detail page is where
    # fulfillment_status gets its own badge.
    if order.payment_status == Order.PaymentStatus.PAID:
        return order.get_payment_status_display(), "badge-success"
    if order.payment_status == Order.PaymentStatus.PARTIALLY_PAID:
        return order.get_payment_status_display(), "badge-warning"
    if order.payment_status == Order.PaymentStatus.REFUNDED:
        return order.get_payment_status_display(), "badge-error"
    return order.get_payment_status_display(), "badge-neutral"


def _dues_invoice_status(invoice):
    if invoice.is_paid:
        return _("Paid"), "badge-success"
    if invoice.is_overdue:
        return _("Overdue"), "badge-error"
    return _("Sent"), "badge-neutral"


class InvoiceListView(ShopManagerRequiredMixin, TemplateView):
    """Every invoice this club has ever sent -- shop order invoices and
    membership dues invoices together, newest first, with a type filter and
    a name/family search. Different models with no shared table to query in
    one go, so each is fetched (search/type-filtered at the DB level, one
    query per kind) and normalised into the same row shape, then merged,
    sorted, and paginated in Python -- fine at a single club's scale
    (invoices in the dozens/hundreds), and far simpler than a raw SQL UNION
    across two differently-shaped tables. There's nothing on either invoice
    worth its own detail page beyond what the order/membership page already
    shows, so every row's "View" link lands there, same reasoning the old
    shop-only version of this page already had.

    Dues invoices are financial member data -- ClubAdminRequiredMixin-only
    everywhere else in the app (see MembershipListView's own docstring), so
    a ShopManager who isn't also an admin gets this page (they already see
    order invoices) but never sees a dues row, regardless of ?type=."""

    template_name = "management/invoice_list.html"
    paginate_by = 25

    def get_context_data(self, **kwargs):
        club = self.request.club
        is_admin = is_club_admin(self.request.user, club)
        selected_type = self.request.GET.get("type", "all")
        if selected_type not in ("order", "dues"):
            selected_type = "all"
        search = self.request.GET.get("q", "").strip()

        rows = []
        if selected_type in ("all", "order"):
            rows.extend(self._order_rows(club, search))
        if is_admin and selected_type in ("all", "dues"):
            rows.extend(self._dues_rows(club, search))
        rows.sort(key=lambda row: row["date"] or date.min, reverse=True)

        page_obj = Paginator(rows, self.paginate_by).get_page(self.request.GET.get("page"))

        return super().get_context_data(
            invoices=page_obj.object_list,
            page_obj=page_obj,
            paginator=page_obj.paginator,
            is_paginated=page_obj.has_other_pages(),
            total_count=len(rows),
            selected_type=selected_type,
            search=search,
            can_see_dues=is_admin,
            **kwargs,
        )

    @staticmethod
    def _search_by_member_or_family(queryset, prefix, search):
        # Also matches by family -- searching "Smith" finds every invoice for
        # a family that has an explicit name of "Smith" or that includes
        # anyone surnamed Smith, not just an invoice literally billed to a
        # Smith -- same search shape as MembershipListView's own ?q=.
        return (
            queryset.filter(**{f"{prefix}__first_name__icontains": search})
            | queryset.filter(**{f"{prefix}__last_name__icontains": search})
            | queryset.filter(**{f"{prefix}__family_memberships__family__name__icontains": search})
            | queryset.filter(**{f"{prefix}__family_memberships__family__memberships__member__last_name__icontains": search})
        ).distinct()

    def _order_rows(self, club, search):
        invoices = Invoice.objects.filter(club=club).select_related("order__purchaser")
        if search:
            invoices = self._search_by_member_or_family(invoices, "order__purchaser", search)

        rows = []
        for invoice in invoices:
            status_label, status_css = _order_invoice_status(invoice.order)
            rows.append(
                {
                    "kind": "order",
                    "kind_label": _("Order"),
                    "number": invoice.number,
                    "billed_to": invoice.order.purchaser,
                    "amount": invoice.order.total,
                    "date": invoice.issued_at.date() if invoice.issued_at else None,
                    "status_label": status_label,
                    "status_css": status_css,
                    "detail_url": reverse("management:order_detail", kwargs={"pk": invoice.order.pk}),
                    "download_url": reverse("management:order_invoice_pdf", kwargs={"pk": invoice.order.pk}),
                }
            )
        return rows

    def _dues_rows(self, club, search):
        invoices = DuesInvoice.objects.filter(club=club, sent_at__isnull=False).select_related("membership__member")
        if search:
            invoices = self._search_by_member_or_family(invoices, "membership__member", search)

        rows = []
        for invoice in invoices:
            status_label, status_css = _dues_invoice_status(invoice)
            rows.append(
                {
                    "kind": "dues",
                    "kind_label": _("Dues"),
                    "number": invoice.number,
                    "billed_to": invoice.membership.member,
                    "amount": invoice.amount,
                    "date": invoice.sent_at.date() if invoice.sent_at else None,
                    "status_label": status_label,
                    "status_css": status_css,
                    "detail_url": reverse("management:membership_invoice_detail", kwargs={"pk": invoice.membership.pk}),
                    "download_url": reverse("management:membership_invoice_pdf", kwargs={"pk": invoice.membership.pk}),
                }
            )
        return rows


class FormListView(FormManagerRequiredMixin, ListView):
    template_name = "management/form_list.html"
    context_object_name = "forms"

    def get_queryset(self):
        return FormBuilderForm.objects.filter(club=self.request.club).annotate(send_count=Count("sends", distinct=True))


class FormCreateView(FormManagerRequiredMixin, View):
    """A form + its first batch of questions in one submit -- same shape as
    ProductCreateView's product+variants: a plain View, not CreateView, since
    this needs a second, independent form (the field formset) alongside
    FormForm. The audience/send itself is a separate step (FormSendCreateView)
    once the question set exists."""

    template_name = "management/form_form.html"

    def get_forms(self, data=None):
        return (
            FormForm(data, instance=FormBuilderForm(club=self.request.club)),
            FieldFormSet(data, prefix="fields"),
        )

    def render_form(self, form, field_formset):
        return render(self.request, self.template_name, {"form": form, "field_formset": field_formset})

    def get(self, request, *args, **kwargs):
        form, field_formset = self.get_forms()
        return self.render_form(form, field_formset)

    def post(self, request, *args, **kwargs):
        form, field_formset = self.get_forms(request.POST)
        if not form.is_valid() or not field_formset.is_valid():
            return self.render_form(form, field_formset)

        with transaction.atomic():
            form_obj = form.save(commit=False)
            form_obj.club = request.club
            form_obj.save()
            # One .create() per row, not bulk_create() -- bulk_create() writes
            # straight to the DB without ever calling Field.save(), which is
            # where a blank key gets auto-slugified from the label. Every
            # question created here would otherwise end up with key="",
            # meaning every one of them collapses onto the *same* dynamic
            # form field (formbuilder.services.form_factory.build_form_class
            # keys its fields dict by Field.key) and renders as an
            # `name=""` input -- invisible to a browser's own form
            # serialization, so it always looks unanswered/required
            # regardless of what a member actually picks.
            for index, row in enumerate(field_formset.cleaned_data):
                if not row.get("label"):
                    continue
                FormBuilderField.objects.create(form=form_obj, label=row["label"], field_type=row["field_type"], required=row["required"], options=row["options"], order=index)

        notify(request, f"s|{_('Form created')}|{_('“%(form)s” created.') % {'form': form_obj}}")
        return redirect("management:form_detail", pk=form_obj.pk)


class FormDetailView(FormManagerRequiredMixin, DetailView):
    """Questions + this form's sends (each a separate occasion it went out
    to an audience -- see FormSend's own docstring). Reachable at the same
    URL the original forms/<uuid:pk>/submissions/ stub used, since a single
    "all submissions for this form" list stopped making sense once responses
    became per-send (formbuilder.services.reporting.form_report's own
    docstring) -- what replaces it is this page's own sends list, each
    linking to its own responses."""

    template_name = "management/form_detail.html"
    context_object_name = "form"

    def get_queryset(self):
        return FormBuilderForm.objects.filter(club=self.request.club)

    def get_context_data(self, **kwargs):
        form_obj = self.object
        fields = list(form_obj.fields.order_by("order"))
        for field in fields:
            field.edit_form = FormBuilderFieldForm(instance=field)
        sends = list(form_obj.sends.annotate(submission_count=Count("submissions", distinct=True)).order_by("-created"))
        return super().get_context_data(
            fields=fields,
            sends=sends,
            add_field_form=FormBuilderFieldForm(),
            **kwargs,
        )


class FormUpdateView(FormManagerRequiredMixin, UpdateView):
    model = FormBuilderForm
    form_class = FormForm
    template_name = "management/form_form.html"

    def get_queryset(self):
        return FormBuilderForm.objects.filter(club=self.request.club)

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Form updated')}|{_('“%(form)s” updated.') % {'form': self.object}}")
        return response

    def get_success_url(self):
        return reverse("management:form_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(update_view=True, **kwargs)


class FormFieldCreateView(FormManagerRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Add question" modal on the form's own detail
    page -- no standalone template, same shape as ProductVariantCreateView."""

    form_class = FormBuilderFieldForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:form_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def get_form_obj(self):
        return get_object_or_404(FormBuilderForm.objects.filter(club=self.request.club), pk=self.kwargs["pk"])

    def form_valid(self, form):
        form_obj = self.get_form_obj()
        last_order = form_obj.fields.order_by("-order").values_list("order", flat=True).first()
        form.instance.form = form_obj
        form.instance.order = (last_order or 0) + 1
        form.save()
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Question added')}|{_('“%(field)s” added to %(form)s.') % {'field': form.instance.label, 'form': form_obj}}")
        return response

    def get_success_url(self):
        return reverse("management:form_detail", args=[self.kwargs["pk"]])


class FormFieldUpdateView(FormManagerRequiredMixin, RedirectOnInvalidMixin, UpdateView):
    """Reachable only via a question's own "Edit" modal on the form's detail
    page -- same reasoning as FormFieldCreateView. Editing an already-answered
    question is still allowed (label/help_text/order/required all stay safe
    to change after the fact); only its ``key`` -- never exposed here -- is
    the part reporting actually joins on, and that's never touched once set."""

    model = FormBuilderField
    form_class = FormBuilderFieldForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:form_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["form_pk"]}

    def get_queryset(self):
        return FormBuilderField.objects.filter(form__club=self.request.club, form__pk=self.kwargs["form_pk"])

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Question updated')}|{_('“%(field)s” updated.') % {'field': self.object.label}}")
        return response

    def get_success_url(self):
        return reverse("management:form_detail", args=[self.kwargs["form_pk"]])


class FormFieldToggleActiveView(FormManagerRequiredMixin, View):
    """No delete view -- Answer.field is PROTECT (formbuilder.models.Answer's
    own docstring), same reasoning as ProductVariantToggleActiveView one
    level down in shop. A field with no answers yet can still be removed
    from the "Add question"/edit UI by simply deactivating it; nothing here
    offers a hard delete at all, matching Field's own "retire via
    is_active=False instead" design note (ARCHITECTURE.md §5.6)."""

    def post(self, request, form_pk, pk):
        field = get_object_or_404(FormBuilderField.objects.filter(form__club=request.club, form__pk=form_pk), pk=pk)
        field.is_active = not field.is_active
        field.save(update_fields=["is_active"])
        if field.is_active:
            notify(request, f"s|{_('Question activated')}|{_('“%(field)s” is now active.') % {'field': field.label}}")
        else:
            notify(request, f"w|{_('Question deactivated')}|{_('“%(field)s” is no longer active.') % {'field': field.label}}")
        return redirect("management:form_detail", pk=form_pk)


class FormSendCreateView(FormManagerRequiredMixin, CreateView):
    model = FormSend
    form_class = FormSendForm
    template_name = "management/formsend_form.html"

    def get_form_kwargs(self):
        # FormSend.clean() rejects a form from another club by comparing against
        # self.club_id -- on a brand-new instance that's still None until
        # ClubScopedModel.save() auto-assigns it, which only happens *after*
        # validation. Set it here so full_clean() sees the real club/form, not
        # None -- same fix as EventCreateView.get_form_kwargs.
        return super().get_form_kwargs() | {"club": self.request.club, "user": self.request.user, "instance": FormSend(club=self.request.club, form=self.get_form_object())}

    def get_context_data(self, **kwargs):
        return super().get_context_data(form_obj=self.get_form_object(), **kwargs)

    def get_form_object(self):
        return get_object_or_404(FormBuilderForm.objects.filter(club=self.request.club), pk=self.kwargs["form_pk"])

    def form_valid(self, form):
        form.instance.created_by = Member.objects.filter(user=self.request.user).first()
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Form sent')}|{_('“%(form)s” sent.') % {'form': self.object.form}}")
        dispatch_notify_form_send(str(self.object.pk))
        return response

    def get_success_url(self):
        # Straight to Responses -- the one page that shows this send's audience/
        # response-window summary *and* its (empty, for now) responses table, so
        # there's a single "nice screen" for a send, not two that show overlapping
        # information (see FormSendResponsesView's own docstring).
        return reverse("management:formsend_responses", args=[self.kwargs["form_pk"], self.object.pk])


class FormSendUpdateView(FormManagerRequiredMixin, UpdateView):
    model = FormSend
    form_class = FormSendForm
    template_name = "management/formsend_form.html"

    def get_queryset(self):
        return FormSend.objects.filter(club=self.request.club, form__pk=self.kwargs["form_pk"]).select_related("form")

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"club": self.request.club, "user": self.request.user}

    def get_context_data(self, **kwargs):
        return super().get_context_data(form_obj=self.object.form, update_view=True, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|{_('Send updated')}|{_('Updated.')}")
        return response

    def get_success_url(self):
        return reverse("management:formsend_responses", args=[self.kwargs["form_pk"], self.object.pk])


class FormSendResponsesView(FormManagerRequiredMixin, DetailView):
    """The one page for a send: audience/response-window summary *and* its
    responses table together -- what used to be a separate FormSendDetailView
    (reached only right after creating a send, with no way back to it) plus
    this page (reached from the form's own Sends list) showed overlapping,
    non-equivalent information depending which path got you there. Now every
    path -- creating a send, editing one, or the Sends list's own "Responses"
    link -- lands here."""

    model = FormSend
    template_name = "management/formsend_responses.html"
    context_object_name = "send"

    def get_queryset(self):
        return FormSend.objects.filter(club=self.request.club, form__pk=self.kwargs["form_pk"]).select_related("form")

    def get_context_data(self, **kwargs):
        send = self.object
        audience_count = form_effective_members(send).count()
        submission_count = send.submissions.count()

        # Pre-shaped for the template rather than looked up there by a dynamic
        # key (Field.id, a UUID) -- Django templates can't index a dict by a
        # variable key without a custom filter this repo doesn't otherwise
        # have, so each column carries its own summary (already sorted) and
        # each row carries its cells in the same column order.
        report = form_report(send)
        columns = [{"field": column, "summary": sorted(report.summaries[column.id].items()) if column.id in report.summaries else None} for column in report.columns]
        rows = [{"submission": row.submission, "cells": [row.values.get(column.id) for column in report.columns]} for row in report.rows]
        return super().get_context_data(
            audience_count=audience_count,
            submission_count=submission_count,
            not_yet_count=max(audience_count - submission_count, 0),
            report=report,
            columns=columns,
            rows=rows,
            **kwargs,
        )


class FormSendResponsesExportView(FormManagerRequiredMixin, View):
    """Plain streamed CSV -- an idempotent GET with nothing to stash/redirect
    around (unlike OrderProductionExportView's stash-token pattern, built
    for a POST-that-mutates-then-downloads flow this view doesn't have), and
    no existing lighter CSV helper in this repo to reuse; shop's own export
    (management.shop_export) is openpyxl/Excel-specific for a genuinely
    different need (a manufacturer order list, one sheet per product)."""

    def get(self, request, form_pk, pk):
        send = get_object_or_404(FormSend.objects.filter(club=request.club, form__pk=form_pk).select_related("form"), pk=pk)
        report = form_report(send)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{send.form.slug}-responses.csv"'
        writer = csv.writer(response)
        writer.writerow([_("Submitted"), _("Member"), *[column.label for column in report.columns]])
        for row in report.rows:
            writer.writerow(
                [
                    timezone.localtime(row.submission.submitted_at).strftime("%Y-%m-%d %H:%M"),
                    str(row.submission.member) if row.submission.member else "",
                    *[_format_answer_value(row.values.get(column.id)) for column in report.columns],
                ]
            )
        return response


def _format_answer_value(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


class EvaluationsComingSoonView(MemberAdminRequiredMixin, TemplateView):
    """Placeholder nav entry for player evaluations (design: ARCHITECTURE.md
    §5.8) -- nothing else is built yet. Deliberately *not* a FeatureRequiredMixin/
    waffle-flagged stub like the shop/forms sections above: those hide their nav
    item until a platform operator turns the flag on for a club, which is exactly
    wrong for a standing "don't forget to build this" reminder -- this stays
    visible to every MEMBER_ADMIN/ADMIN on every club unconditionally. Reuses
    _generic_list.html (StubListMixin's own template) with an empty object_list
    rather than a real queryset, since there's no model behind this yet at all."""

    template_name = "management/_generic_list.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(page_title=_("Player evaluations"), object_list=[], **kwargs)


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
            # registration_details is a plain FK now (one membership can carry
            # more than one request -- a second team, or player and referee
            # both), so it's a to-many relation: prefetch_related, not
            # select_related.
            memberships = list(ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER).select_related("member").prefetch_related("registration_details__requested_team"))
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
                membership.link_form = SignupLinkMemberForm(club=club, exclude_member=membership.member)
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


class SignupCancelView(ClubAdminRequiredMixin, View):
    """Withdraws one person's sign-up -- see club.services.cancellation.
    cancel_membership for the soft-cancel-vs-delete-the-member split."""

    def post(self, request, pk):
        club = request.club
        season = current_season(club)
        if season is None:
            raise Http404("No active season.")
        membership = get_object_or_404(ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER), member_id=pk)
        member_name = str(membership.member)

        cancel_membership(membership)

        notify(request, f"w|{_('Sign-up cancelled')}|{_('The registration for “%(member)s” has been cancelled.') % {'member': member_name}}")
        return redirect("management:signup_list")


class SignupLinkToMemberView(ClubAdminRequiredMixin, View):
    """Fixes an accidental duplicate -- see club.services.signup_linking.
    link_to_existing_member."""

    def post(self, request, pk):
        club = request.club
        season = current_season(club)
        if season is None:
            raise Http404("No active season.")
        membership = get_object_or_404(ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER), member_id=pk)

        form = SignupLinkMemberForm(request.POST, club=club, exclude_member=membership.member)
        if not form.is_valid():
            body = " ".join(str(error) for errors in form.errors.values() for error in errors)
            notify(request, f"e|{_('Could not link')}|{body}")
            return redirect("management:signup_list")

        target = form.cleaned_data["member"]
        try:
            link_to_existing_member(membership, target)
        except ValidationError as error:
            notify(request, f"e|{_('Could not link')}|{'; '.join(error.messages)}")
            return redirect("management:signup_list")

        notify(request, f"s|{_('Linked')}|{_('This registration is now linked to “%(member)s”.') % {'member': target}}")
        return redirect("management:signup_list")


class VolunteerListView(MemberAdminRequiredMixin, TemplateView):
    """Every current-season StaffAssignment club-wide -- no cross-team "who
    holds what role" view exists otherwise, RefereeListView being the
    closest precedent (and referee-specific) -- plus anyone who registered
    as a volunteer (registration.models.RegistrationDetails) and hasn't
    been placed into a real StaffAssignment yet. Placing one (team +
    position, VolunteerPlacementForm) is the one new action here; everything
    else about a StaffAssignment is still managed from the team's own staff
    page."""

    template_name = "management/volunteer_list.html"

    def get_context_data(self, **kwargs):
        club = self.request.club
        season = current_season(club)
        assignments = []
        pending = []

        if season is not None:
            assignments = list(StaffAssignment.objects.filter(team__club=club, season=season).select_related("member", "team", "position").order_by("team__name", "position__name"))
            pending = list(
                RegistrationDetails.objects.filter(
                    entry_kind=RegistrationDetails.EntryKind.VOLUNTEER,
                    resulting_staff_assignment__isnull=True,
                    membership__club=club,
                    membership__season=season,
                ).select_related("membership__member", "requested_team", "requested_position")
            )
            for details in pending:
                details.placement_form = VolunteerPlacementForm(
                    club=club, season=season, member=details.membership.member, initial={"team": details.requested_team_id, "position": details.requested_position_id}
                )

        return super().get_context_data(season=season, assignments=assignments, pending=pending, **kwargs)


class VolunteerPlaceView(MemberAdminRequiredMixin, View):
    """The Volunteers list's own "Place" action -- creates the real
    StaffAssignment a RegistrationDetails row could only request, and
    stamps it back so the row drops out of the pending list."""

    def post(self, request, pk):
        club = request.club
        season = current_season(club)
        if season is None:
            raise Http404("No active season to place this volunteer in.")

        details = get_object_or_404(
            RegistrationDetails.objects.filter(entry_kind=RegistrationDetails.EntryKind.VOLUNTEER, resulting_staff_assignment__isnull=True, membership__club=club, membership__season=season).select_related("membership__member"),
            pk=pk,
        )
        member = details.membership.member
        form = VolunteerPlacementForm(request.POST, club=club, season=season, member=member)

        if not form.is_valid():
            body = " ".join(str(error) for errors in form.errors.values() for error in errors)
            notify(request, f"e|{_('Could not place')}|{body}")
        elif StaffAssignment.objects.filter(team=form.cleaned_data["team"], season=season, member=member).exists():
            notify(request, f"w|{_('Already placed')}|" + _("%(member)s already holds a position on %(team)s.") % {"member": member, "team": form.cleaned_data["team"]})
        else:
            assignment = form.save()
            details.resulting_staff_assignment = assignment
            details.save(update_fields=["resulting_staff_assignment"])
            notify(request, f"s|{_('Volunteer placed')}|" + _("%(member)s is now %(position)s for %(team)s.") % {"member": member, "position": assignment.position, "team": assignment.team})

        return redirect("management:volunteer_list")
