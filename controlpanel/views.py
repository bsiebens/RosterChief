from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView, View
from waffle import get_waffle_flag_model, get_waffle_switch_model

from billing.models import Due, Plan, PlanPrice
from billing.services import BillingError
from billing.services.dues import next_period_start, open_period, reactivate, record_payment, start_trial, subscribe, waive
from billing.services.invoices import invoice_pdf, issue_invoice
from club.models import Club, ClubRole
from events.models import Location
from features.models import Maintenance

from .forms import ClubAdminForm, ClubForm, DuePaymentForm, FlagForm, HomeLocationForm, MaintenanceForm, OpenPeriodForm, PlanForm, PlanPriceForm, PlatformAdminForm, SubscriptionForm, TrialForm
from .messages import notify
from .mixins import PlatformStaffRequiredMixin, PlatformSuperuserRequiredMixin, RedirectOnInvalidMixin
from .services.admins import grant_club_admin, revoke_club_admin
from .services.platform_admins import (
    PlatformAdminError,
    grant_platform_access,
    platform_admins,
    revoke_platform_access,
    set_platform_access,
)
from .services.statistics import club_attention, club_charts, club_statistics, clubs_with_health, flag_adoption, flags_for_club, onboarding_funnel, platform_attention, platform_charts, platform_totals

Flag = get_waffle_flag_model()
Switch = get_waffle_switch_model()


@contextmanager
def suppress_billing_errors(request, title="Billing error"):
    """Turn a BillingError into an error message rather than letting it propagate.

    Fits call sites that fall through to the same redirect on the happy and unhappy path
    alike — the success message is set inside the block, the failure message by this
    context manager, and whichever fired, the caller's next line runs unchanged.
    """
    try:
        yield
    except BillingError as error:
        notify(request, f"e|{title}|{error}")


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
        notify(self.request, f"s|Club created|Club “{self.object}” created.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:club_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="clubs", **kwargs)


class ClubUpdateView(PlatformStaffRequiredMixin, UpdateView):
    model = Club
    form_class = ClubForm
    template_name = "controlpanel/club_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|Club updated|Club “{self.object}” updated.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:club_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="clubs", update_view=True, **kwargs)


class ClubDetailView(PlatformStaffRequiredMixin, DetailView):
    model = Club
    template_name = "controlpanel/club_detail.html"
    context_object_name = "club"

    def get_context_data(self, **kwargs):
        subscription = getattr(self.object, "subscription", None)
        next_start = next_period_start(self.object)

        # Bound per-row so each due's "Add payment" modal can render its own form without
        # the template calling DuePaymentForm(initial=...) itself.
        dues = list(self.object.dues.select_related("plan", "invoice").prefetch_related("payments"))
        for due in dues:
            if due.is_owing:
                due.payment_form = DuePaymentForm(initial={"amount": due.balance})

        home_location = Location.objects.filter(club=self.object, is_home=True).first()

        return super().get_context_data(
            # Drives the "archived but owes nothing -- reactivate?" prompt. Computed from the
            # dues already fetched above rather than re-querying.
            dues_settled=not any(due.is_owing for due in dues),
            home_location=home_location,
            home_location_form=HomeLocationForm(instance=home_location),
            nav="clubs",
            groups=club_statistics(self.object),
            attention=club_attention(self.object),
            charts=club_charts(self.object),
            subscription=subscription,
            dues=dues,
            today=timezone.localdate(),
            admins=ClubRole.objects.filter(club=self.object, role=ClubRole.Roles.ADMIN).select_related("member", "member__user"),
            admin_form=ClubAdminForm(),
            flags=flags_for_club(self.object),
            open_period_form=OpenPeriodForm(),
            open_period_blurb=f"Next period starts {date_format(next_start, 'j M Y')} unless you say otherwise. By default it continues from the end of the last one, so a lapsed year is still owed — pick a start date to forgive the gap.",
            subscription_form=SubscriptionForm(instance=subscription) if subscription else SubscriptionForm(),
            trial_form=TrialForm(),
            **kwargs,
        )


class ClubArchiveView(PlatformStaffRequiredMixin, View):
    """Clubs are archived, never destroyed — their data (and invoices) are kept."""

    def post(self, request, pk):
        club = get_object_or_404(Club, pk=pk)
        club.archive()
        notify(request, f"w|Club archived|Club “{club}” archived. Its subdomain no longer resolves.")
        return redirect("controlpanel:club_detail", pk=club.pk)


class ClubRestoreView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        club = get_object_or_404(Club, pk=pk)
        club.restore()
        notify(request, f"s|Club restored|Club “{club}” restored.")
        return redirect("controlpanel:club_detail", pk=club.pk)


class ClubAdminAddView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Add admin" modal on the club detail page — POST-only, and
    there is no standalone template to render on GET or on a rejected submission."""

    form_class = ClubAdminForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:club_detail"

    @property
    def club(self):
        return get_object_or_404(Club, pk=self.kwargs["pk"])

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def form_valid(self, form):
        role = grant_club_admin(self.club, **form.cleaned_data)
        notify(self.request, f"s|Admin added|{role.member} is now an admin of {role.club}. They must set up two-factor authentication before they can sign in.")
        return redirect("controlpanel:club_detail", pk=self.kwargs["pk"])


class ClubHomeLocationSetView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Create or update the club's home Location -- reachable only via the "Home
    location" modal on the club detail page. Binds to the existing home Location
    (if any) so submitting the form edits it in place rather than ever creating
    a second one; ``unique_home_location_per_club`` backs that up at the DB level."""

    form_class = HomeLocationForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:club_detail"

    @property
    def club(self):
        return get_object_or_404(Club, pk=self.kwargs["pk"])

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"instance": Location.objects.filter(club=self.club, is_home=True).first()}

    def form_valid(self, form):
        location = form.save(commit=False)
        location.club = self.club
        location.is_home = True
        location.save()
        notify(self.request, f"s|Home location set|{location} is now {self.club}'s home location.")
        return redirect("controlpanel:club_detail", pk=self.kwargs["pk"])


class ClubAdminRemoveView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk, role_pk):
        role = get_object_or_404(ClubRole, pk=role_pk, club_id=pk, role=ClubRole.Roles.ADMIN)
        member = role.member
        revoke_club_admin(role)
        notify(request, f"w|Admin removed|{member} is no longer an admin of this club.")
        return redirect("controlpanel:club_detail", pk=pk)


class ClubFeatureToggleView(PlatformStaffRequiredMixin, View):
    """Turn a feature on or off for one club."""

    def post(self, request, pk, flag_pk):
        club = get_object_or_404(Club, pk=pk)
        flag = get_object_or_404(Flag, pk=flag_pk)

        if flag.clubs.filter(pk=club.pk).exists():
            flag.clubs.remove(club)
            notify(request, f"w|Feature disabled|“{flag.name}” turned off for {club}.")
        else:
            flag.clubs.add(club)
            notify(request, f"s|Feature enabled|“{flag.name}” turned on for {club}.")

        return redirect("controlpanel:club_detail", pk=club.pk)


class FeatureListView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "controlpanel/features.html"

    def get_context_data(self, **kwargs):
        # Bound per-row so each flag's "Edit" modal can render its own form: the template
        # can't call FlagForm(instance=flag) itself, so the form rides along on the flag.
        flags = list(Flag.objects.prefetch_related("clubs").order_by("name"))
        for flag in flags:
            flag.edit_form = FlagForm(instance=flag)

        return super().get_context_data(
            nav="features",
            flags=flags,
            flag_form=FlagForm(),
            switches=Switch.objects.order_by("name"),
            maintenance=Maintenance.current(),
            maintenance_form=MaintenanceForm(),
            **kwargs,
        )


class MaintenanceView(PlatformStaffRequiredMixin, View):
    """Close the platform, or open it again."""

    def post(self, request):
        if Maintenance.is_on():
            Maintenance.stop()
            notify(request, "s|Maintenance ended|The clubs are back.")
        else:
            form = MaintenanceForm(request.POST)
            message = form.cleaned_data["message"] if form.is_valid() else ""
            Maintenance.start(message=message, user=request.user)
            notify(request, "w|Platform closed|Every club subdomain now serves a maintenance page, and the scheduled jobs stand down.")

        return redirect("controlpanel:features")


class FlagCreateView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, CreateView):
    """Reachable only via the "New feature" modal on the features page — POST-only, and
    there is no standalone template to render on GET or on a rejected submission."""

    model = Flag
    form_class = FlagForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:features"

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|Feature created|Feature “{self.object.name}” created.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:features")


class FlagUpdateView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, UpdateView):
    """Reachable only via a flag's "Edit" modal on the features page — POST-only, and
    there is no standalone template to render on GET or on a rejected submission."""

    model = Flag
    form_class = FlagForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:features"

    def form_valid(self, form):
        response = super().form_valid(form)
        notify(self.request, f"s|Feature updated|Feature “{self.object.name}” updated.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:features")


class SwitchToggleView(PlatformStaffRequiredMixin, View):
    """Global kill-switch: on or off for the whole platform."""

    def post(self, request, pk):
        switch = get_object_or_404(Switch, pk=pk)
        switch.active = not switch.active
        switch.save()
        title = "Switch on" if switch.active else "Switch off"
        notify(request, f"s|{title}|Switch “{switch.name}” is now {'on' if switch.active else 'off'}.")
        return redirect("controlpanel:features")


class PlatformAdminListView(PlatformSuperuserRequiredMixin, TemplateView):
    template_name = "controlpanel/admins.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="admins", admins=platform_admins(), admin_form=PlatformAdminForm(), **kwargs)


class PlatformAdminAddView(PlatformSuperuserRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via the "Grant access" modal on the admins page — POST-only, and
    there is no standalone template to render on GET or on a rejected submission."""

    form_class = PlatformAdminForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:admins"

    def form_valid(self, form):
        user = grant_platform_access(form.cleaned_data["email"], is_superuser=form.cleaned_data["is_superuser"])
        notify(self.request, f"s|Platform access granted|{user.email} now has platform access. They must set up two-factor authentication before they can sign in.")
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
            notify(request, f"e|Couldn't update access|{error}")
        else:
            notify(request, f"s|Access updated|Updated platform access for {user.email}.")
        return redirect("controlpanel:admins")


class PlatformAdminRevokeView(PlatformSuperuserRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(get_user_model(), pk=pk)
        try:
            revoke_platform_access(request.user, user)
        except PlatformAdminError as error:
            notify(request, f"e|Couldn't revoke access|{error}")
        else:
            notify(request, f"w|Access revoked|{user.email} no longer has platform access.")
        return redirect("controlpanel:admins")


class BillingView(PlatformStaffRequiredMixin, TemplateView):
    """Plans and their prices, plus every period we are owed money for."""

    template_name = "controlpanel/billing.html"

    def get_context_data(self, **kwargs):
        today = timezone.localdate()

        # Bound per-row so each "Edit" / "New price" modal can render its own form: the
        # template can't call PlanForm(instance=plan) itself, so the form rides along on
        # the object it belongs to.
        plans = list(Plan.objects.prefetch_related("prices").annotate(club_count=Count("subscriptions")))
        for plan in plans:
            plan.edit_form = PlanForm(instance=plan)
            plan.price_form = PlanPriceForm()

        owing = list(Due.objects.filter(status__in=Due.OWING).select_related("club", "plan").order_by("grace_until"))
        for due in owing:
            due.payment_form = DuePaymentForm(initial={"amount": due.balance})

        return super().get_context_data(
            nav="billing",
            plans=plans,
            plan_form=PlanForm(),
            owing=owing,
            today=today,
            **kwargs,
        )


class PlanCreateView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, CreateView):
    """Reachable only via the "New plan" modal on the billing page — POST-only, and there
    is no standalone template to render on GET or on a rejected submission."""

    model = Plan
    form_class = PlanForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:billing"

    def get_success_url(self):
        notify(self.request, f"s|Plan created|Plan “{self.object}” created. Give it a price before billing anyone.")
        return reverse("controlpanel:billing")


class PlanUpdateView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, UpdateView):
    """Reachable only via a plan's "Edit" modal on the billing page — POST-only, and there
    is no standalone template to render on GET or on a rejected submission."""

    model = Plan
    form_class = PlanForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:billing"

    def get_success_url(self):
        notify(self.request, f"s|Plan updated|Plan “{self.object}” updated.")
        return reverse("controlpanel:billing")


class PlanPriceCreateView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, CreateView):
    """A rate change is a new dated price, never an edit of the old one — periods already
    billed keep the amount they were billed at.

    Reachable only via a plan's "New price" modal on the billing page — POST-only, and
    there is no standalone template to render on GET or on a rejected submission.
    """

    model = PlanPrice
    form_class = PlanPriceForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:billing"

    @property
    def plan(self):
        return get_object_or_404(Plan, pk=self.kwargs["pk"])

    def form_valid(self, form):
        form.instance.plan = self.plan
        response = super().form_valid(form)
        notify(self.request, f"s|Price added|{self.plan} is €{self.object.amount} for periods opening from {self.object.active_from}.")
        return response

    def get_success_url(self):
        return reverse("controlpanel:billing")


class SubscribeClubView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Put a club on a plan, which opens its first period.

    Reachable only via the "Change plan" modal on the club detail page — POST-only, and
    there is no standalone template to render on GET or on a rejected submission.
    """

    form_class = SubscriptionForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:club_detail"

    @property
    def club(self):
        return get_object_or_404(Club, pk=self.kwargs["pk"])

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.club.pk}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        subscription = getattr(self.club, "subscription", None)
        if subscription:
            kwargs["instance"] = subscription
        return kwargs

    def form_valid(self, form):
        club = self.club
        existing = getattr(club, "subscription", None)
        with suppress_billing_errors(self.request, title="Couldn't change plan"):
            if existing:
                # Changing plan does not re-bill: the current period keeps the amount it was
                # issued at, and the new rate applies from the next one.
                was_on_trial = existing.trial_ends_at is not None
                subscription = form.save(commit=False)
                subscription.club = club
                if was_on_trial:
                    # A manual plan change while on a trial is a deliberate override --
                    # left in place, the trial fields would silently swap the plan again
                    # later, onto a plan the admin didn't just choose.
                    subscription.trial_ends_at = None
                    subscription.post_trial_plan = None
                subscription.save()
                notify(self.request, f"s|Plan changed|{club} is now on {subscription.plan}. The current period keeps the amount it was billed at.")
            else:
                subscribe(club, form.cleaned_data["plan"], start=form.cleaned_data.get("start"), auto_archive=form.cleaned_data["auto_archive"], auto_renew=form.cleaned_data["auto_renew"])
                notify(self.request, f"s|Billing started|{club} is on {form.cleaned_data['plan']}. Its first period is open.")

        return redirect("controlpanel:club_detail", pk=club.pk)


class ClubStartTrialView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Put a club with no subscription yet on a short trial -- reachable only via the
    "Start trial" modal on the club detail page, shown alongside "Start billing" only
    while the club has no subscription. POST-only, no standalone template."""

    form_class = TrialForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:club_detail"

    @property
    def club(self):
        return get_object_or_404(Club, pk=self.kwargs["pk"])

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.kwargs["pk"]}

    def form_valid(self, form):
        club = self.club
        with suppress_billing_errors(self.request, title="Couldn't start trial"):
            trial_plan = form.cleaned_data["trial_plan"]
            start_trial(
                club,
                trial_plan,
                post_trial_plan=form.cleaned_data["post_trial_plan"],
                start=form.cleaned_data.get("start"),
                auto_renew=form.cleaned_data["auto_renew"],
                auto_archive=form.cleaned_data["auto_archive"],
            )
            notify(self.request, f"s|Trial started|{club} is on a {trial_plan.duration_months}-month trial of {trial_plan}, then switches to {form.cleaned_data['post_trial_plan']}.")

        return redirect("controlpanel:club_detail", pk=club.pk)


class RecordPaymentView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Reachable only via a due's "Record payment" modal on the club or billing page —
    POST-only, and there is no standalone template to render on GET or on a rejected
    submission."""

    form_class = DuePaymentForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:club_detail"

    @property
    def due(self):
        return get_object_or_404(Due.objects.select_related("club", "plan"), pk=self.kwargs["pk"])

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.due.club_id}

    def form_valid(self, form):
        due = self.due
        with suppress_billing_errors(self.request, title="Couldn't record payment"):
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
            notify(self.request, f"s|Payment recorded|€{form.cleaned_data['amount']} recorded. {due.get_status_display().capitalize()} — €{due.balance} outstanding.")

        return redirect("controlpanel:club_detail", pk=due.club_id)


class WaiveDueView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        due = get_object_or_404(Due, pk=pk)
        with suppress_billing_errors(request, title="Couldn't waive period"):
            waive(due)
            notify(request, f"w|Period waived|Period {due.period_start} to {due.period_end} waived. Nothing is owed and the club will not be archived for it.")

        return redirect("controlpanel:club_detail", pk=due.club_id)


class OpenPeriodView(PlatformStaffRequiredMixin, RedirectOnInvalidMixin, FormView):
    """Renew a club, or reactivate an archived one.

    Reachable only via the "Open period" modal on the club detail page — POST-only, and
    there is no standalone template to render on GET or on a rejected submission.
    """

    form_class = OpenPeriodForm
    http_method_names = ["post"]
    invalid_redirect_url_name = "controlpanel:club_detail"

    @property
    def club(self):
        return get_object_or_404(Club, pk=self.kwargs["pk"])

    def get_invalid_redirect_kwargs(self):
        return {"pk": self.club.pk}

    def form_valid(self, form):
        club = self.club
        start = form.cleaned_data.get("start")
        with suppress_billing_errors(self.request, title="Couldn't open period"):
            due = reactivate(club, start=start) if club.is_archived else open_period(club, start=start)
            notify(self.request, f"s|Period opened|Period {due.period_start} to {due.period_end} opened for €{due.amount}. Invoice {due.invoice.number}.")

        return redirect("controlpanel:club_detail", pk=club.pk)


class InvoicePdfView(PlatformStaffRequiredMixin, View):
    def get(self, request, pk):
        due = get_object_or_404(Due.objects.select_related("club", "plan", "invoice"), pk=pk)
        invoice = issue_invoice(due)
        try:
            pdf = invoice_pdf(invoice)
        except BillingError as error:
            # The native PDF libraries are missing: say so rather than 500.
            notify(request, f"e|PDF unavailable|{error}")
            return redirect("controlpanel:club_detail", pk=due.club_id)

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
        return response
