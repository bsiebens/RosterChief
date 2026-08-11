"""Public and member-facing pages: claiming a child, and a parent's own family.

Everything here is outside the management app on purpose -- a parent is not club
staff, and ClubStaffRequiredMixin would (rightly) turn them away.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import render
from django.views.generic import FormView, TemplateView

from members.forms import ParentClaimForm
from members.models import FamilyMembership, Member
from members.services.claims import submit_claim


class ClubScopedPublicMixin:
    """A club subdomain resolves this page; the base domain has no club to claim
    a child at, so it simply doesn't exist there."""

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "club", None) is None:
            raise Http404("This page belongs to a club.")
        return super().dispatch(request, *args, **kwargs)


class ParentClaimView(ClubScopedPublicMixin, FormView):
    """Public. Submitting is also how a parent registers -- there is no open
    signup page, so this form is the only way into an account for someone the
    club hasn't already added.

    It always reports the same thing back, whether or not the child was found:
    the response must not tell an anonymous submitter which children the club
    has. What actually happens next is decided by an admin.
    """

    template_name = "members/parent_claim.html"
    form_class = ParentClaimForm

    def form_valid(self, form):
        submit_claim(self.request.club, **form.cleaned_data)
        return render(self.request, "members/parent_claim_submitted.html", {"club": self.request.club})


class MyFamilyView(ClubScopedPublicMixin, LoginRequiredMixin, TemplateView):
    """What a linked parent sees: the children they're responsible for, and
    nothing else. The seam a real parent portal would grow from."""

    template_name = "members/my_family.html"

    def get_context_data(self, **kwargs):
        me = Member.objects.filter(user=self.request.user).first()
        children = Member.objects.none()
        if me is not None:
            children = Member.objects.filter(
                family_memberships__role=FamilyMembership.FamilyRole.CHILD,
                family_memberships__family__memberships__member=me,
                family_memberships__family__memberships__role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
                member_of__club=self.request.club,
            ).distinct()

        return super().get_context_data(club=self.request.club, me=me, children=children, **kwargs)

