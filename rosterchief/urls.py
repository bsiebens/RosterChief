"""URL configuration for rosterchief.

``/admin/login/`` is deliberately intercepted *before* ``admin.site.urls`` and
redirected to the allauth login, so Django staff go through the same MFA
challenge as everyone else — Django's own admin login form knows nothing about
second factors. ``RequireMFAMiddleware`` then blocks any staff user who has not
enrolled.

Both admin routes answer on the base domain only (``rosterchief.admin_host``): on
a club subdomain they 404 like any path that doesn't exist there.
"""

import re

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView

from announcements.views import PendingAnnouncementView
from api.app.api import app_api
from api.app.docs import docs_page
from api.public.urls import api
from club.views import root, signup_closed
from members.language import set_language

from .admin_host import base_domain_only, base_domain_only_urls
from .health import healthz
from .media import serve_media

urlpatterns = [
    # No auth and no tenant: the proxy and the load balancer must reach it on any host.
    path("healthz", healthz, name="healthz"),
    path("admin/login/", base_domain_only(RedirectView.as_view(pattern_name="account_login", query_string=True)), name="admin_login_redirect"),
    path("admin/", base_domain_only_urls(admin.site.urls)),
    # Before allauth's own urls so it wins the match: self-registration is closed.
    # Accounts are created by an admin or by the family-registration form -- a
    # club has no reason to let a stranger create one.
    path("accounts/signup/", signup_closed, name="account_signup"),
    path("accounts/", include("allauth.urls")),
    # Gives the "set_language" name the marketing site's language switcher posts to
    # (django.views.i18n.set_language) -- not i18n_patterns()-wrapped, since this is a
    # multi-tenant, subdomain-routed project and a /en/ or /nl/ URL prefix on every
    # path would fight that everywhere else.
    # members.language.set_language also saves the choice on the signed-in member.
    path("i18n/", set_language, name="set_language"),
    path("", include("members.urls")),
    path("register/", include("registration.urls")),
    path("forms/", include("formbuilder.urls")),
    path("control/", include("controlpanel.urls")),
    path("manage/", include("management.urls")),
    path("app/", include("mobile.urls")),
    path("api/v1/", api.urls),
    # Native-app authentication (allauth.headless, "app" client only): /api/auth/app/v1/...
    # Base domain only -- the account plane of API.md §4; a club subdomain 404s.
    path("api/auth/", base_domain_only_urls(include("allauth.headless.urls"))),
    # The native-app API itself (api.app): account endpoints on the base domain, club endpoints
    # on each club's host -- each endpoint enforces its own plane (api/app/auth.py).
    path("api/app/v1/", app_api.urls),
    # Developer docs for the app APIs (docs/app-api/*.md). Served on the base domain; a club
    # host redirects there (api.app.docs.docs_page).
    path("api/docs/", docs_page, name="app_api_docs"),
    path("api/docs/<slug:page>/", docs_page, name="app_api_docs_page"),
    # Shared by management/base.html and mobile/base.html's own poll-on-load script --
    # not nested under either app's urls.py, since both surfaces hit this one verbatim.
    path("announcements/pending/", PendingAnnouncementView.as_view(), name="announcement_pending"),
    # "/" resolves per tenant: a club subdomain lands on the club, the base domain
    # serves the public marketing site (marketing.views.home). This is why
    # LOGIN_REDIRECT_URL can stay "/".
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
    # configured (see settings.STORAGES). deploy/caddy/Caddyfile serves /media/* itself, but a
    # proxy that only reverse-proxies (compose.behind-proxy.yaml) does not, so without this
    # route every uploaded club logo 404s there in production too.
    # Once AWS_STORAGE_BUCKET_NAME is set, club.logo.url points straight at the bucket and this
    # route is simply never hit.
    #
    # django.conf.urls.static.static() looks like the right helper, but it hard-codes its own
    # `if not settings.DEBUG: return []` — it is documented as dev-only and silently no-ops in
    # production no matter what guards the call site. Build the pattern directly against the
    # view it wraps instead, which has no such gate. serve_media adds the same hardening
    # headers the Caddy media block sets, since this route is reachable in production.
    urlpatterns += [
        re_path(rf"^{re.escape(settings.MEDIA_URL.lstrip('/'))}(?P<path>.*)$", serve_media, {"document_root": settings.MEDIA_ROOT}),
    ]
