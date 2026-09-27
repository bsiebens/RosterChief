"""Shared plumbing for public, club-scoped pages outside the management app,
plus the one routed page this app owns: accepting a family invite
(members.services.invites). ClubScopedPublicMixin is also used by
registration, formbuilder, and mobile for their own public-facing pages.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import render
from django.views import View

from .models import FamilyInvite
from .services.invites import InviteError, accept_family_invite, invite_problem


class ClubScopedPublicMixin:
    """A club subdomain resolves this page; the base domain has no club for it
    to make sense on, so it simply doesn't exist there."""

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "club", None) is None:
            raise Http404("This page belongs to a club.")
        return super().dispatch(request, *args, **kwargs)


class FamilyInviteAcceptView(ClubScopedPublicMixin, LoginRequiredMixin, View):
    """The link in a family invite email. Sign-in required, and only the
    account the invite was sent to (matched on email, case-insensitively) can
    accept it -- before its expiry, once, and on the club that sent it. Any
    other case gets the same kind of friendly error and no family details."""

    template_name = "members/family_invite.html"

    def get_invite(self):
        return FamilyInvite.objects.filter(club=self.request.club, token=self.kwargs["token"]).select_related("family", "club").first()

    def get(self, request, token):
        invite = self.get_invite()
        problem = invite_problem(invite, request.user)
        return render(request, self.template_name, {"club": request.club, "invite": invite if problem is None else None, "problem": problem})

    def post(self, request, token):
        invite = self.get_invite()
        problem = invite_problem(invite, request.user)
        if problem is None:
            try:
                accept_family_invite(invite, request.user)
            except InviteError as error:
                problem = str(error)
        return render(request, self.template_name, {"club": request.club, "invite": invite if problem is None else None, "problem": problem, "accepted": problem is None}, status=200 if problem is None else 400)
