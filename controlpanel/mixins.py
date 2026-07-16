from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import Http404
from django.shortcuts import redirect


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


class PlatformSuperuserRequiredMixin(PlatformStaffRequiredMixin):
    """Superusers only.

    Managing platform admins is the one thing staff may not do. The panel is
    gated on ``is_staff or is_superuser``, so if a staff member could grant
    themselves ``is_superuser`` the two would collapse into the same thing and
    ``is_superuser`` would stop being a security boundary.
    """

    def test_func(self):
        return self.request.user.is_superuser


class RedirectOnInvalidMixin:
    """A form submitted from a modal has nowhere sensible to re-render on error: the page
    that opened it has already moved on, and the view has no standalone template of its
    own. Redirect back to ``invalid_redirect_url_name`` instead, with the errors flattened
    into messages, rather than Django's default of re-rendering ``template_name``.
    """

    invalid_redirect_url_name = None

    def get_invalid_redirect_kwargs(self):
        return {}

    def form_invalid(self, form):
        for error in form.errors.values():
            messages.error(self.request, " ".join(error))
        return redirect(self.invalid_redirect_url_name, **self.get_invalid_redirect_kwargs())
