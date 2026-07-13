from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView, View
from waffle import get_waffle_flag_model, get_waffle_switch_model

from club.models import Club, ClubRole

from .forms import ClubAdminForm, ClubForm, FlagForm, PlatformAdminForm
from .mixins import PlatformStaffRequiredMixin, PlatformSuperuserRequiredMixin
from .services.admins import grant_club_admin, revoke_club_admin
from .services.platform_admins import (
    PlatformAdminError,
    grant_platform_access,
    platform_admins,
    revoke_platform_access,
    set_platform_access,
)
from .services.statistics import club_statistics, clubs_with_totals, platform_totals

Flag = get_waffle_flag_model()
Switch = get_waffle_switch_model()


class DashboardView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "controlpanel/dashboard.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            nav="dashboard",
            totals=platform_totals(),
            clubs=clubs_with_totals(Club.objects.active()),
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
        return clubs_with_totals(clubs)

    def get_context_data(self, **kwargs):
        return super().get_context_data(nav="clubs", show_archived=self.show_archived, search=self.request.GET.get("q", ""), **kwargs)


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
