from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import Http404


class PlatformStaffRequiredMixin(UserPassesTestMixin):
    """Gate for the platform control panel.

    Two rules:

    * **Staff only.** ``is_staff`` or ``is_superuser``. Anonymous visitors are
      sent to the login page; signed-in non-staff get a 403 (Django's
      AccessMixin already distinguishes those two cases). Staff must also hold a
      second factor — ``RequireMFAMiddleware`` enforces that, so the panel is
      2FA-protected for free.
    * **Base domain only.** The panel manages *all* clubs, so it must not be
      reachable from inside one. If the tenant middleware resolved a club from
      the subdomain, the panel does not exist here.
    """

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "club", None) is not None:
            raise Http404("The control panel is not available on a club subdomain.")
        return super().dispatch(request, *args, **kwargs)

    def test_func(self):
        user = self.request.user
        return user.is_staff or user.is_superuser
