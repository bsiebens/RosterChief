"""Shared plumbing for public, club-scoped pages outside the management app,
plus the pages this app routes itself: accepting a family invite
(members.services.invites) and serving a member's private photo. ClubScopedPublicMixin is also used by
registration, formbuilder, and mobile for their own public-facing pages.
"""

import mimetypes

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.utils.cache import patch_vary_headers
from django.views import View

from rosterchief.media import MEDIA_CSP

from .models import FamilyInvite, Member
from .services.invites import InviteError, accept_family_invite, invite_problem
from .services.photos import can_view_member_photo


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


class MemberPhotoView(View):
    """Streams a member picture (``player_photo``/``profile_photo``) from private storage to whoever
    members.services.photos.can_view_member_photo allows -- staff authority is
    taken from ``request.club`` (so it only counts on that club's own host),
    while the member themselves and their parents/guardians are allowed on any
    host. Everyone else gets a 404, which doesn't confirm the member exists."""

    #: ``player_photo``/``profile_photo``, or None for the effective picture.
    field = None

    def get(self, request, pk):
        member = get_object_or_404(Member, pk=pk)
        photo_file = getattr(member, self.field) if self.field else member.photo
        if not photo_file or not can_view_member_photo(request.user, member, getattr(request, "club", None), field=self.field):
            raise Http404("No such photo.")
        try:
            photo = photo_file.open("rb")
        except FileNotFoundError as error:
            raise Http404("No such photo.") from error

        content_type = mimetypes.guess_type(photo_file.name)[0] or "application/octet-stream"
        if not content_type.startswith("image/") or content_type == "image/svg+xml":
            content_type = "application/octet-stream"
        response = FileResponse(photo, content_type=content_type)
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = MEDIA_CSP
        patch_vary_headers(response, ["Cookie"])
        return response
