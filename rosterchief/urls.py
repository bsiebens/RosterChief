"""URL configuration for rosterchief.

``/admin/login/`` is deliberately intercepted *before* ``admin.site.urls`` and
redirected to the allauth login, so Django staff go through the same MFA
challenge as everyone else — Django's own admin login form knows nothing about
second factors. ``RequireMFAMiddleware`` then blocks any staff user who has not
enrolled.
"""

import re

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve

from api.urls import api
from club.views import root, signup_closed

from .health import healthz

urlpatterns = [
    # No auth and no tenant: the proxy and the load balancer must reach it on any host.
    path("healthz", healthz, name="healthz"),
    path("admin/login/", RedirectView.as_view(pattern_name="account_login", query_string=True), name="admin_login_redirect"),
    path("admin/", admin.site.urls),
    # Before allauth's own urls so it wins the match: self-registration is closed.
    # Accounts are created by an admin, by the family-registration form, or by an
    # approved parent claim (members/views.py) -- a club has no reason to let a
    # stranger create one, and the claim queue would be the first thing to suffer.
    path("accounts/signup/", signup_closed, name="account_signup"),
    path("accounts/", include("allauth.urls")),
    path("", include("members.urls")),
    path("controlpanel/", include("controlpanel.urls")),
    path("manage/", include("management.urls")),
    path("app/", include("mobile.urls")),
    path("api/v1/", api.urls),
    # "/" resolves per tenant: a club subdomain lands on the club, the base domain
    # hands off to the control panel. This is why LOGIN_REDIRECT_URL can stay "/".
    path("", root, name="root"),
]

if settings.DEBUG:
    # Only when the app is actually installed. It is a dev dependency, and the production
    # image installs with --no-dev, so DEBUG=True in a container must not take the whole
    # site down over a package that is only there to refresh a browser tab.
    if settings.BROWSER_RELOAD_AVAILABLE:
        urlpatterns += [path("__reload__/", include("django_browser_reload.urls"))]

if not settings.AWS_STORAGE_BUCKET_NAME:
    # Gated on the storage backend, not on DEBUG: local disk is the default until a bucket is
    # configured (see settings.STORAGES), and Caddy only reverse-proxies — it never serves
    # /media/* itself — so without this route every uploaded club logo 404s in production too.
    # Once AWS_STORAGE_BUCKET_NAME is set, club.logo.url points straight at the bucket and this
    # route is simply never hit.
    #
    # django.conf.urls.static.static() looks like the right helper, but it hard-codes its own
    # `if not settings.DEBUG: return []` — it is documented as dev-only and silently no-ops in
    # production no matter what guards the call site. Build the pattern directly against the
    # view it wraps instead, which has no such gate.
    urlpatterns += [
        re_path(rf"^{re.escape(settings.MEDIA_URL.lstrip('/'))}(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ]
