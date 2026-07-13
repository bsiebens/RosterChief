"""URL configuration for rosterchief.

``/admin/login/`` is deliberately intercepted *before* ``admin.site.urls`` and
redirected to the allauth login, so Django staff go through the same MFA
challenge as everyone else — Django's own admin login form knows nothing about
second factors. ``RequireMFAMiddleware`` then blocks any staff user who has not
enrolled.
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/login/", RedirectView.as_view(pattern_name="account_login", query_string=True), name="admin_login_redirect"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("controlpanel/", include("controlpanel.urls")),
]
