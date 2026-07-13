from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView, View

from club.models import Club, ClubRole

from .forms import ClubAdminForm, ClubForm
from .mixins import PlatformStaffRequiredMixin
from .services.admins import grant_club_admin, revoke_club_admin
from .services.statistics import club_statistics, clubs_with_totals, platform_totals


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
