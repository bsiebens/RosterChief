"""URL configuration for rosterchief.

``/admin/login/`` is deliberately intercepted *before* ``admin.site.urls`` and
redirected to the allauth login, so Django staff go through the same MFA
challenge as everyone else — Django's own admin login form knows nothing about
second factors. ``RequireMFAMiddleware`` then blocks any staff user who has not
enrolled.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from club.views import root

from .health import healthz

urlpatterns = [
    # No auth and no tenant: the proxy and the load balancer must reach it on any host.
    path("healthz", healthz, name="healthz"),
    path("admin/login/", RedirectView.as_view(pattern_name="account_login", query_string=True), name="admin_login_redirect"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("controlpanel/", include("controlpanel.urls")),
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

    # Club logos are uploads: runserver has to serve MEDIA_ROOT itself.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
