from django.db import transaction
from django.db.models import Count, ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView, View

from club.mixins import ClubAdminRequiredMixin, ClubStaffRequiredMixin, NewsAuthorRequiredMixin, NewsEditRequiredMixin, NewsPublisherRequiredMixin
from club.models import ClubMembership, ClubRole, Season
from club.services.access import can_edit_news, can_publish_news, current_season, members_visible_to
from club.services.fees import mark_as_paid, record_payment, remaining_balance
from controlpanel.messages import notify
from controlpanel.mixins import RedirectOnInvalidMixin
from controlpanel.services.statistics import club_attention, club_charts, club_statistics
from events.models import Event, EventSeries, Location, Opponent
from formbuilder.models import Form as FormBuilderForm
from formbuilder.models import Submission
from members.models import Family, FamilyMembership, Member
from members.services.family import add_child_to_family, add_parent_to_family, attach_to_family, detach_from_family, grant_login, register_family
from news.models import News, NewsPhoto
from shop.models import Discount, Invoice, Order, Product
from teams.models import Position, StaffAssignment, Team, TeamMembership

from .bulk_import import build_member_import_template, parse_member_import_rows, read_member_import_workbook
from .forms import (
    AddChildForm,
    AddParentForm,
    AttachToFamilyForm,
    ClubMembershipForm,
    ClubRoleAssignForm,
    FamilyCreateForm,
    GrantLoginForm,
    MemberForm,
    MemberImportUploadForm,
    NewsForm,
    NewsPhotoUploadForm,
    NewsPublishForm,
    PositionForm,
    RecordFeePaymentForm,
    TeamForm,
)
from .pdf import PDFExportError, membership_list_pdf


class HomeView(ClubStaffRequiredMixin, TemplateView):
    """The at-a-glance numbers a club admin/team manager/coach would actually want:
    club_attention/club_charts/club_statistics are the exact functions
    controlpanel/club_detail.html uses for the platform admin's per-club drill-down --
    already club-scoped, so directly reusable for this club's own staff."""

    template_name = "management/home.html"

    def get_context_data(self, **kwargs):
        club = self.request.club
        return super().get_context_data(
            attention=club_attention(club),
            charts=club_charts(club),
            groups=club_statistics(club),
            upcoming_events=Event.objects.filter(club=club, start__gte=timezone.now()).order_by("start")[:5],
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

    def get_queryset(self):
        members = members_visible_to(self.request.user, self.request.club)
        search = self.request.GET.get("q", "").strip()
        if search:
            members = members.filter(first_name__icontains=search) | members.filter(last_name__icontains=search) | members.filter(email__icontains=search) | members.filter(user__email__icontains=search)
        return members.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(search=self.request.GET.get("q", ""), **kwargs)
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
            club_memberships_by_member_id = {cm.member_id: cm for cm in club_memberships}
        for member in members:
            member.current_membership = club_memberships_by_member_id.get(member.pk)

        return context | {"members": members}


class MembershipListView(ClubAdminRequiredMixin, ListView):
    """Who's paid for the current season, and who hasn't -- MemberListView's Status
    column can only show this one row at a time. Financial data, so admin-only
    throughout (same line already drawn around the Shop nav section)."""

    template_name = "management/membership_list.html"
    context_object_name = "memberships"

    def get_selected_season(self):
        season_id = self.request.GET.get("season")
        if season_id:
            season = Season.objects.filter(club=self.request.club, pk=season_id).first()
            if season is not None:
                return season
        return current_season(self.request.club)

    def get_queryset(self):
        season = self.get_selected_season()
        if season is None:
            return ClubMembership.objects.none()

        memberships = ClubMembership.objects.filter(club=self.request.club, season=season).select_related("member").order_by("member__last_name", "member__first_name")

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
            counts = {row["fee_status"]: row["count"] for row in ClubMembership.objects.filter(club=club, season=current).values("fee_status").annotate(count=Count("id"))}
        paid = counts.get(ClubMembership.FeeStatus.PAID, 0)
        partial = counts.get(ClubMembership.FeeStatus.PARTIALLY_PAID, 0)
        unpaid = counts.get(ClubMembership.FeeStatus.UNPAID, 0)
        waived = counts.get(ClubMembership.FeeStatus.WAIVED, 0)
        total = paid + partial + unpaid + waived

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
        for membership in memberships:
            membership.member.family_memberships_display = family_memberships_by_member_id.get(membership.member_id, [])
            membership.remaining_balance_display = remaining_balance(membership)
            # Nothing to collect on an already-settled or deliberately-exempted row.
            if membership.fee_status in (ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.WAIVED):
                membership.record_payment_form = None
            else:
                membership.record_payment_form = RecordFeePaymentForm()

        return context | {"memberships": memberships}


class MembershipMarkPaidView(ClubAdminRequiredMixin, View):
    """Flag a batch of memberships as settled: active + paid, in one go. There's no
    bank integration, so this is always a manual admin action -- the point of this
    view is to make the manual action fast, not to replace it with automation.

    Uses club.services.fees.mark_as_paid per row (a .save() loop under the hood,
    never a bulk .update()): club/signals.py grants the MEMBER ClubRole via a
    post_save signal on ClubMembership, which .update() would bypass entirely,
    silently leaving a marked-paid member without their role. Same function backs
    the per-row "Mark fully paid" button (MembershipMarkFullyPaidView) -- one place
    decides how a membership becomes paid.
    """

    def post(self, request):
        ids = request.POST.getlist("membership_ids")
        memberships = ClubMembership.objects.filter(pk__in=ids, club=request.club)

        count = 0
        with transaction.atomic():
            for membership in memberships:
                mark_as_paid(membership, recorded_by=request.user)
                count += 1

        notify(request, f"s|{_('Marked as paid')}|{_('%(count)d membership(s) updated.') % {'count': count} }")

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
        notify(request, f"s|{_('Marked as paid')}|{_('“%(member)s” is now active and paid.') % {'member': membership.member}}")

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


class MemberImportTemplateView(ClubStaffRequiredMixin, View):
    """Anyone with management access can download the template -- filling it in
    doesn't grant any authority, only the upload step (admin-only) does."""

    def get(self, request):
        workbook = build_member_import_template()
        response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = 'attachment; filename="member_import_template.xlsx"'
        workbook.save(response)
        return response


class MemberImportView(ClubAdminRequiredMixin, View):
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


class MemberImportConfirmView(ClubAdminRequiredMixin, View):
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
        with transaction.atomic():
            for result in results:
                member = result["member"]
                if member is None:
                    continue
                member.save()
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


class MemberCreateView(ClubAdminRequiredMixin, CreateView):
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


class MemberUpdateView(ClubAdminRequiredMixin, View):
    """Not a generic UpdateView: this page owns two forms on one submit -- the
    member's own fields, and (when they're actually rostered this season) their
    ClubMembership's license/status/fee status. A Member has no club of its own
    without one, so this is the only place to see or change it."""

    template_name = "management/member_form.html"

    def get_member(self):
        return get_object_or_404(members_visible_to(self.request.user, self.request.club), pk=self.kwargs["pk"])

    def get_membership(self, member):
        season = current_season(self.request.club)
        if season is None:
            return None
        return ClubMembership.objects.filter(club=self.request.club, member=member, season=season).first() or ClubMembership(
            club=self.request.club, member=member, season=season, signed_up_at=timezone.localdate()
        )

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
            notify(request, f"s|{_('Member updated')}|{_('“%(member)s” updated.') % {'member': member} }")
            return redirect("management:member_detail", pk=member.pk)

        return self.render_form(member, form, membership_form)


class MemberDetailView(ClubStaffRequiredMixin, DetailView):
    template_name = "management/member_detail.html"
    context_object_name = "member"

    def get_queryset(self):
        return members_visible_to(self.request.user, self.request.club)

    def get_context_data(self, **kwargs):
        visible = members_visible_to(self.request.user, self.request.club)
        my_family_ids = FamilyMembership.objects.filter(member=self.object).values_list("family_id", flat=True)
        family_scoped_members = visible.filter(family_memberships__family_id__in=my_family_ids).distinct()
        family_groups, _ = group_by_family(family_scoped_members)

        return super().get_context_data(
            family_groups=family_groups,
            family_role_choices=FamilyMembership.FamilyRole.choices,
            add_child_form=AddChildForm(),
            add_parent_form=AddParentForm(),
            attach_to_family_form=AttachToFamilyForm(club=self.request.club, member=self.object),
            current_membership=ClubMembership.objects.filter(club=self.request.club, member=self.object, season=current_season(self.request.club)).first(),
            membership_history=ClubMembership.objects.filter(club=self.request.club, member=self.object).select_related("season").order_by("-season__start_date"),
            **kwargs,
        )


class MemberAttachToFamilyView(ClubAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Add to family" modal on a standalone member's page."""

    form_class = AttachToFamilyForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "management:member_detail"

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def get_form_kwargs(self):
        member = get_object_or_404(members_visible_to(self.request.user, self.request.club), pk=self.kwargs["pk"])
        return super().get_form_kwargs() | {"club": self.request.club, "member": member}

    def form_valid(self, form):
        member = get_object_or_404(members_visible_to(self.request.user, self.request.club), pk=self.kwargs["pk"])
        family = attach_to_family(member, role=form.cleaned_data["role"], family=form.cleaned_data["family"])
        body = _("“%(member)s” is now part of %(family)s.") % {"member": member, "family": family}
        notify(self.request, f"s|{_('Added to family')}|{body}")
        return redirect("management:member_detail", pk=member.pk)


class MemberGrantLoginView(ClubAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
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
        member = get_object_or_404(members_visible_to(self.request.user, self.request.club), pk=self.kwargs["pk"])
        if member.user_id is not None:
            # Already has one -- the row's button shouldn't have been there at all;
            # a direct POST replay (e.g. a resubmitted form) is the only way here.
            notify(self.request, f"w|{_('Already has a login')}|{_('“%(member)s” can already sign in.') % {'member': member} }")
        else:
            grant_login(member, form.cleaned_data["email"])
            notify(self.request, f"s|{_('Login granted')}|{_('“%(member)s” can now sign in.') % {'member': member} }")
        return redirect("management:member_detail", pk=member.pk)


class MemberDetachFromFamilyView(ClubAdminRequiredMixin, View):
    def post(self, request, pk, family_pk):
        member = get_object_or_404(members_visible_to(request.user, request.club), pk=pk)
        family = get_object_or_404(Family, pk=family_pk, memberships__member=member)
        # detach_from_family may delete `family` itself (left empty) -- str() it first,
        # since Family.__str__ queries self.memberships, which needs a pk to still exist.
        family_name = str(family)
        detach_from_family(member, family)
        body = _("“%(member)s” is no longer part of %(family)s.") % {"member": member, "family": family_name}
        notify(request, f"w|{_('Removed from family')}|{body}")
        return redirect("management:member_detail", pk=member.pk)


class FamilyMembershipRoleUpdateView(ClubAdminRequiredMixin, View):
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


class MemberDeleteView(ClubAdminRequiredMixin, View):
    def post(self, request, pk):
        member = get_object_or_404(members_visible_to(request.user, request.club), pk=pk)
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
    template_name = "management/team_list.html"
    context_object_name = "teams"

    def get_queryset(self):
        teams = Team.objects.filter(club=self.request.club)
        search = self.request.GET.get("q", "").strip()
        if search:
            teams = teams.filter(name__icontains=search)
        return teams

    def get_context_data(self, **kwargs):
        return super().get_context_data(search=self.request.GET.get("q", ""), **kwargs)


class TeamCreateView(ClubAdminRequiredMixin, CreateView):
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


class TeamUpdateView(ClubAdminRequiredMixin, UpdateView):
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


class TeamDeleteView(ClubAdminRequiredMixin, View):
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
    template_name = "management/team_detail.html"
    context_object_name = "team"

    def get_queryset(self):
        return Team.objects.filter(club=self.request.club)


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


class FamilyCreateView(ClubAdminRequiredMixin, FormView):
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
        visible = members_visible_to(self.request.user, self.request.club)
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


class FamilyAddChildView(ClubAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
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


class FamilyAddParentView(ClubAdminRequiredMixin, RedirectOnInvalidMixin, FormView):
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


class PositionListView(ClubAdminRequiredMixin, ListView):
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


# --- News: draft/edit is broad (any coach_manager/editor/admin), but only EDITOR/ADMIN
# may publish -- the release flow the news app exists for ---------------------------


class NewsListView(ClubStaffRequiredMixin, ListView):
    template_name = "management/news_list.html"
    context_object_name = "news_items"

    def get_queryset(self):
        return News.objects.filter(club=self.request.club).prefetch_related("teams")

    def get_context_data(self, **kwargs):
        for news_item in self.object_list:
            news_item.can_edit = can_edit_news(self.request.user, news_item)
        return super().get_context_data(**kwargs)


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
        photo.delete()

        notify(request, f"w|{_('Photo removed')}|{_('The photo was removed.')}")
        return redirect("management:news_detail", pk=news_item.pk)


class RosterListView(ClubStaffRequiredMixin, StubListMixin, ListView):
    page_title = _("Roster")

    def get_queryset(self):
        return TeamMembership.objects.filter(team__club=self.request.club)


class StaffListView(ClubStaffRequiredMixin, StubListMixin, ListView):
    page_title = _("Staff")

    def get_queryset(self):
        return StaffAssignment.objects.filter(team__club=self.request.club)


class EventListView(ClubStaffRequiredMixin, StubListMixin, ListView):
    page_title = _("Events")

    def get_queryset(self):
        return Event.objects.filter(club=self.request.club)


class EventSeriesListView(ClubStaffRequiredMixin, StubListMixin, ListView):
    page_title = _("Event series")

    def get_queryset(self):
        return EventSeries.objects.filter(club=self.request.club)


class LocationListView(ClubStaffRequiredMixin, StubListMixin, ListView):
    page_title = _("Locations")

    def get_queryset(self):
        return Location.objects.filter(club=self.request.club)


class OpponentListView(ClubStaffRequiredMixin, StubListMixin, ListView):
    page_title = _("Opponents")

    def get_queryset(self):
        return Opponent.objects.filter(club=self.request.club)


class ProductListView(ClubAdminRequiredMixin, StubListMixin, ListView):
    page_title = _("Products")

    def get_queryset(self):
        return Product.objects.filter(club=self.request.club)


class OrderListView(ClubAdminRequiredMixin, StubListMixin, ListView):
    page_title = _("Orders")

    def get_queryset(self):
        return Order.objects.filter(club=self.request.club)


class DiscountListView(ClubAdminRequiredMixin, StubListMixin, ListView):
    page_title = _("Discounts")

    def get_queryset(self):
        return Discount.objects.filter(club=self.request.club)


class InvoiceListView(ClubAdminRequiredMixin, StubListMixin, ListView):
    page_title = _("Invoices")

    def get_queryset(self):
        return Invoice.objects.filter(club=self.request.club)


class FormListView(ClubAdminRequiredMixin, StubListMixin, ListView):
    page_title = _("Forms")

    def get_queryset(self):
        return FormBuilderForm.objects.filter(club=self.request.club)


class SubmissionListView(ClubAdminRequiredMixin, StubListMixin, ListView):
    page_title = _("Submissions")

    def get_queryset(self):
        return Submission.objects.filter(form__club=self.request.club, form_id=self.kwargs["pk"])
