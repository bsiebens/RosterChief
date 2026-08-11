"""Public and member-facing pages: claiming a child, and a parent's own family.

Everything here is outside the management app on purpose -- a parent is not club
staff, and ClubStaffRequiredMixin would (rightly) turn them away.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from controlpanel.messages import notify
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

    A signed-in parent claiming a second child (a sibling, most often) gets
    their "about you" fields locked to read-only text instead of blank inputs
    -- typing their name/email again risks a typo forking off a second
    User/Member instead of reusing theirs, and approval also uses this to
    merge the new child into the family they and their first child already
    belong to (see members.services.claims.approve_claim).
    """

    template_name = "members/parent_claim.html"
    form_class = ParentClaimForm

    def get_member(self):
        if not self.request.user.is_authenticated:
            return None
        return Member.objects.filter(user=self.request.user).first()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["lock_parent_fields"] = self.get_member() is not None
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(locked_member=self.get_member(), **kwargs)

    def form_valid(self, form):
        data = dict(form.cleaned_data)
        member = self.get_member()
        if member is not None:
            # Never trust the client for this -- the fields aren't even in the
            # submitted form when locked. Pulled straight from the account
            # that's actually signed in.
            data["parent_first_name"] = member.first_name
            data["parent_last_name"] = member.last_name
            data["parent_email"] = member.contact_email
        submit_claim(self.request.club, submitted_by_user=self.request.user if self.request.user.is_authenticated else None, **data)

        club = self.request.club
        title = _("Request received")
        body = _("%(club)s will check this against their records. If it matches, you'll get an email with a link to set your password.") % {"club": club.name}
        if club.contact_email:
            body += " " + _("Any questions, write to %(email)s.") % {"email": club.contact_email}
        # Worded identically whether or not that child exists, and sent before any
        # lookup happens at all -- a message that differed would tell an anonymous
        # submitter which children the club has.
        notify(self.request, f"s|{title}|{body}")
        return redirect("members:parent_claim")


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

