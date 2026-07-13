from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView, View
from waffle import get_waffle_flag_model, get_waffle_switch_model

from billing.models import Due, Tier, TierPrice
from billing.services import BillingError
from billing.services.dues import next_period_start, open_period, reactivate, record_payment, subscribe, waive
from billing.services.invoices import invoice_pdf, issue_invoice
from club.models import Club, ClubRole

from .forms import ClubAdminForm, ClubForm, DuePaymentForm, FlagForm, OpenPeriodForm, PlatformAdminForm, SubscriptionForm, TierForm, TierPriceForm
from .mixins import PlatformStaffRequiredMixin, PlatformSuperuserRequiredMixin
from .services.admins import grant_club_admin, revoke_club_admin
from .services.platform_admins import (
    PlatformAdminError,
    grant_platform_access,
    platform_admins,
    revoke_platform_access,
    set_platform_access,
)
from .services.statistics import club_attention, club_charts, club_statistics, clubs_with_health, flag_adoption, onboarding_funnel, platform_attention, platform_charts, platform_totals

Flag = get_waffle_flag_model()
Switch = get_waffle_switch_model()


class DashboardView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "controlpanel/dashboard.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            nav="dashboard",
            totals=platform_totals(),
            attention=platform_attention(),
            funnel=onboarding_funnel(),
            flags=flag_adoption(),
            charts=platform_charts(),
            clubs=clubs_with_health(),
            today=timezone.localdate(),
            **kwargs,
        )


class ClubListView(PlatformStaffRequiredMixin, ListView):
    template_name = "controlpanel/club_list.html"
    context_object_name = "clubs"

    @property
    def show_archived(self):
        return self.request.GET.get("archived") == "1"

    def get_queryset(self):
        clubs = Club.objects.archived() if self.show_archived else Club.objects.active()
        search = self.request.GET.get("q", "").strip()
        if search:
            clubs = clubs.filter(name__icontains=search)
        return clubs_with_health(clubs)

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="clubs", show_archived=self.show_archived, search=self.request.GET.get("q", ""), today=timezone.localdate(), **kwargs)


class ClubCreateView(PlatformStaffRequiredMixin, CreateView):
    model = Club
    form_class = ClubForm
    template_name = "controlpanel/club_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Club “{self.object}” created.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:club_detail", args=[self.object.pk])


class ClubUpdateView(PlatformStaffRequiredMixin, UpdateView):
    model = Club
    form_class = ClubForm
    template_name = "controlpanel/club_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Club “{self.object}” updated.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:club_detail", args=[self.object.pk])


class ClubDetailView(PlatformStaffRequiredMixin, DetailView):
    model = Club
    template_name = "controlpanel/club_detail.html"
    context_object_name = "club"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            nav="clubs",
            groups=club_statistics(self.object),
            attention=club_attention(self.object),
            charts=club_charts(self.object),
            subscription=getattr(self.object, "subscription", None),
            dues=self.object.dues.select_related("tier", "invoice").prefetch_related("payments"),
            today=timezone.localdate(),
            admins=ClubRole.objects.filter(club=self.object, role=ClubRole.Roles.ADMIN).select_related("member", "member__user"),
            flags=flags_for_club(self.object),
            **kwargs,
        )


class ClubArchiveView(PlatformStaffRequiredMixin, View):
    """Clubs are archived, never destroyed — their data (and invoices) are kept."""

    def post(self, request, pk):
        club = get_object_or_404(Club, pk=pk)
        club.archive()
        messages.warning(request, f"Club “{club}” archived. Its subdomain no longer resolves.")
        return redirect("controlpanel:club_detail", pk=club.pk)


class ClubRestoreView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        club = get_object_or_404(Club, pk=pk)
        club.restore()
        messages.success(request, f"Club “{club}” restored.")
        return redirect("controlpanel:club_detail", pk=club.pk)


class ClubAdminAddView(PlatformStaffRequiredMixin, FormView):
    form_class = ClubAdminForm
    template_name = "controlpanel/club_admin_form.html"

    @property
    def club(self):
        return get_object_or_404(Club, pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="clubs", club=self.club, **kwargs)

    def form_valid(self, form):
        role = grant_club_admin(self.club, **form.cleaned_data)
        messages.success(self.request, f"{role.member} is now an admin of {role.club}. They must set up two-factor authentication before they can sign in.")
        return redirect("controlpanel:club_detail", pk=self.kwargs["pk"])


class ClubAdminRemoveView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk, role_pk):
        role = get_object_or_404(ClubRole, pk=role_pk, club_id=pk, role=ClubRole.Roles.ADMIN)
        member = role.member
        revoke_club_admin(role)
        messages.warning(request, f"{member} is no longer an admin of this club.")
        return redirect("controlpanel:club_detail", pk=pk)


def flags_for_club(club):
    """Every flag, annotated with whether it is on for this club and why."""
    enabled_ids = set(club.flags.values_list("pk", flat=True))
    return [
        {
            "flag": flag,
            "enabled": flag.pk in enabled_ids,
            # `everyone` overrides club targeting, so the per-club toggle is moot.
            "overridden": flag.everyone is not None,
        }
        for flag in Flag.objects.order_by("name")
    ]


class ClubFeatureToggleView(PlatformStaffRequiredMixin, View):
    """Turn a feature on or off for one club."""

    def post(self, request, pk, flag_pk):
        club = get_object_or_404(Club, pk=pk)
        flag = get_object_or_404(Flag, pk=flag_pk)

        if flag.clubs.filter(pk=club.pk).exists():
            flag.clubs.remove(club)
            messages.warning(request, f"“{flag.name}” turned off for {club}.")
        else:
            flag.clubs.add(club)
            messages.success(request, f"“{flag.name}” turned on for {club}.")

        return redirect("controlpanel:club_detail", pk=club.pk)


class FeatureListView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "controlpanel/features.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            nav="features",
            flags=Flag.objects.prefetch_related("clubs").order_by("name"),
            switches=Switch.objects.order_by("name"),
            **kwargs,
        )


class FlagCreateView(PlatformStaffRequiredMixin, CreateView):
    model = Flag
    form_class = FlagForm
    template_name = "controlpanel/flag_form.html"
    success_url = None

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="features", **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Feature “{self.object.name}” created.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:features")


class FlagUpdateView(PlatformStaffRequiredMixin, UpdateView):
    model = Flag
    form_class = FlagForm
    template_name = "controlpanel/flag_form.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="features", **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Feature “{self.object.name}” updated.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:features")


class SwitchToggleView(PlatformStaffRequiredMixin, View):
    """Global kill-switch: on or off for the whole platform."""

    def post(self, request, pk):
        switch = get_object_or_404(Switch, pk=pk)
        switch.active = not switch.active
        switch.save()
        messages.success(request, f"Switch “{switch.name}” is now {'on' if switch.active else 'off'}.")
        return redirect("controlpanel:features")


class PlatformAdminListView(PlatformSuperuserRequiredMixin, TemplateView):
    template_name = "controlpanel/admins.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="admins", admins=platform_admins(), **kwargs)


class PlatformAdminAddView(PlatformSuperuserRequiredMixin, FormView):
    form_class = PlatformAdminForm
    template_name = "controlpanel/admin_form.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="admins", **kwargs)

    def form_valid(self, form):
        user = grant_platform_access(form.cleaned_data["email"], is_superuser=form.cleaned_data["is_superuser"])
        messages.success(self.request, f"{user.email} now has platform access. They must set up two-factor authentication before they can sign in.")
        return redirect("controlpanel:admins")


class PlatformAdminUpdateView(PlatformSuperuserRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(get_user_model(), pk=pk)
        try:
            set_platform_access(
                request.user,
                user,
                is_staff=request.POST.get("is_staff") == "1",
                is_superuser=request.POST.get("is_superuser") == "1",
            )
        except PlatformAdminError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"Updated platform access for {user.email}.")
        return redirect("controlpanel:admins")


class PlatformAdminRevokeView(PlatformSuperuserRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(get_user_model(), pk=pk)
        try:
            revoke_platform_access(request.user, user)
        except PlatformAdminError as error:
            messages.error(request, str(error))
        else:
            messages.warning(request, f"{user.email} no longer has platform access.")
        return redirect("controlpanel:admins")


class BillingView(PlatformStaffRequiredMixin, TemplateView):
    """Tiers and their prices, plus every period we are owed money for."""

    template_name = "controlpanel/billing.html"

    def get_context_data(self, **kwargs):
        today = timezone.localdate()
        return super().get_context_data(
            nav="billing",
            tiers=Tier.objects.prefetch_related("prices").annotate(club_count=Count("subscriptions")),
            owing=Due.objects.filter(status__in=Due.OWING).select_related("club", "tier").order_by("grace_until"),
            today=today,
            **kwargs,
        )


class TierCreateView(PlatformStaffRequiredMixin, CreateView):
    model = Tier
    form_class = TierForm
    template_name = "controlpanel/tier_form.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="billing", **kwargs)

    def get_success_url(self):
        messages.success(self.request, f"Tier “{self.object}” created. Give it a price before billing anyone.")
        return reverse("controlpanel:billing")


class TierUpdateView(PlatformStaffRequiredMixin, UpdateView):
    model = Tier
    form_class = TierForm
    template_name = "controlpanel/tier_form.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="billing", **kwargs)

    def get_success_url(self):
        messages.success(self.request, f"Tier “{self.object}” updated.")
        return reverse("controlpanel:billing")


class TierPriceCreateView(PlatformStaffRequiredMixin, CreateView):
    """A rate change is a new dated price, never an edit of the old one — periods already
    billed keep the amount they were billed at."""

    model = TierPrice
    form_class = TierPriceForm
    template_name = "controlpanel/tier_price_form.html"

    @property
    def tier(self):
        return get_object_or_404(Tier, pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="billing", tier=self.tier, **kwargs)

    def form_valid(self, form):
        form.instance.tier = self.tier
        response = super().form_valid(form)
        messages.success(self.request, f"{self.tier} is €{self.object.amount} for periods opening from {self.object.active_from}.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:billing")


class SubscribeClubView(PlatformStaffRequiredMixin, FormView):
    """Put a club on a tier, which opens its first period."""

    form_class = SubscriptionForm
    template_name = "controlpanel/subscription_form.html"

    @property
    def club(self):
        return get_object_or_404(Club, pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        subscription = getattr(self.club, "subscription", None)
        if subscription:
            kwargs["instance"] = subscription
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="clubs", club=self.club, subscription=getattr(self.club, "subscription", None), **kwargs)

    def form_valid(self, form):
        club = self.club
        existing = getattr(club, "subscription", None)
        try:
            if existing:
                # Changing tier does not re-bill: the current period keeps the amount it was
                # issued at, and the new rate applies from the next one.
                subscription = form.save(commit=False)
                subscription.club = club
                subscription.save()
                messages.success(self.request, f"{club} is now on {subscription.tier}. The current period keeps the amount it was billed at.")
            else:
                subscribe(club, form.cleaned_data["tier"], start=form.cleaned_data.get("start"), auto_archive=form.cleaned_data["auto_archive"])
                messages.success(self.request, f"{club} is on {form.cleaned_data['tier']}. Its first period is open.")
        except BillingError as error:
            messages.error(self.request, str(error))

        return redirect("controlpanel:club_detail", pk=club.pk)


class RecordPaymentView(PlatformStaffRequiredMixin, FormView):
    form_class = DuePaymentForm
    template_name = "controlpanel/payment_form.html"

    @property
    def due(self):
        return get_object_or_404(Due.objects.select_related("club", "tier"), pk=self.kwargs["pk"])

    def get_initial(self):
        return {"amount": self.due.balance}

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="clubs", due=self.due, **kwargs)

    def form_valid(self, form):
        due = self.due
        try:
            record_payment(
                due,
                form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data["reference"],
                paid_at=form.cleaned_data["paid_at"],
                note=form.cleaned_data["note"],
                user=self.request.user,
            )
            due.refresh_from_db()
            messages.success(self.request, f"€{form.cleaned_data['amount']} recorded. {due.get_status_display().capitalize()} — €{due.balance} outstanding.")
        except BillingError as error:
            messages.error(self.request, str(error))

        return redirect("controlpanel:club_detail", pk=due.club_id)


class WaiveDueView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        due = get_object_or_404(Due, pk=pk)
        try:
            waive(due)
            messages.warning(request, f"Period {due.period_start} to {due.period_end} waived. Nothing is owed and the club will not be archived for it.")
        except BillingError as error:
            messages.error(request, str(error))

        return redirect("controlpanel:club_detail", pk=due.club_id)


class OpenPeriodView(PlatformStaffRequiredMixin, FormView):
    """Renew a club, or reactivate an archived one."""

    form_class = OpenPeriodForm
    template_name = "controlpanel/period_form.html"

    @property
    def club(self):
        return get_object_or_404(Club, pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        club = self.club
        return super().get_context_data(nav="clubs", club=club, next_start=next_period_start(club), **kwargs)

    def form_valid(self, form):
        club = self.club
        start = form.cleaned_data.get("start")
        try:
            due = reactivate(club, start=start) if club.is_archived else open_period(club, start=start)
            messages.success(self.request, f"Period {due.period_start} to {due.period_end} opened for €{due.amount}. Invoice {due.invoice.number}.")
        except BillingError as error:
            messages.error(self.request, str(error))

        return redirect("controlpanel:club_detail", pk=club.pk)


class InvoicePdfView(PlatformStaffRequiredMixin, View):
    def get(self, request, pk):
        due = get_object_or_404(Due.objects.select_related("club", "tier", "invoice"), pk=pk)
        invoice = issue_invoice(due)
        try:
            pdf = invoice_pdf(invoice)
        except BillingError as error:
            # The native PDF libraries are missing: say so rather than 500.
            messages.error(request, str(error))
            return redirect("controlpanel:club_detail", pk=due.club_id)

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
        return response
