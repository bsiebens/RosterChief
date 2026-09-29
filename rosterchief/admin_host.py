"""Keep Django's admin on the base domain only.

The admin is a platform-level tool: it manages every club at once, so it has no
business answering on a club subdomain. Every admin view is wrapped so a request
whose host carries a tenant label (a known club, an archived one, or an unknown
slug alike) gets a plain 404 -- the same answer as any other path that doesn't
exist there. The check lives in Django rather than only in the reverse proxy, so it
holds whichever proxy (or none) sits in front of the app.
"""

from functools import wraps

from django.http import Http404
from django.urls import URLPattern, URLResolver

from club.tenancy import ClubTenantMiddleware


def is_tenant_host(request) -> bool:
    """True when the request's host names a club subdomain (resolved or not)."""
    return ClubTenantMiddleware.get_subdomain(request) is not None


def base_domain_only(view):
    """Wrap ``view`` so it 404s on a club subdomain."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if is_tenant_host(request):
            raise Http404("Not available on this host.")
        return view(request, *args, **kwargs)

    return wrapper


def _wrap_patterns(patterns):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            _wrap_patterns(pattern.url_patterns)
        elif isinstance(pattern, URLPattern):
            pattern.callback = base_domain_only(pattern.callback)


def base_domain_only_urls(urls):
    """``admin.site.urls`` (or an ``include(...)``) with every view wrapped by
    :func:`base_domain_only`.

    Takes and returns the ``(patterns, app_name, namespace)`` triple ``path()``
    accepts for an include. ``include()`` hands over the urlconf *module* rather
    than a list, so its ``urlpatterns`` are wrapped in that case.
    """
    patterns, app_name, namespace = urls
    patterns = getattr(patterns, "urlpatterns", patterns)
    _wrap_patterns(patterns)
    return patterns, app_name, namespace
