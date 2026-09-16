"""Shared plumbing for public, club-scoped pages outside the management app.

Nothing in this app has its own routed views right now (see members/urls.py) --
the parent-claim flow that used to live here was removed as unreachable/unused
(no nav ever linked to it). ClubScopedPublicMixin stays: registration,
formbuilder, and mobile all depend on it for their own public-facing pages.
"""

from django.http import Http404


class ClubScopedPublicMixin:
    """A club subdomain resolves this page; the base domain has no club for it
    to make sense on, so it simply doesn't exist there."""

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "club", None) is None:
            raise Http404("This page belongs to a club.")
        return super().dispatch(request, *args, **kwargs)
