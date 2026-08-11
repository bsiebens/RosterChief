from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views.generic import TemplateView


class ClubHomeView(LoginRequiredMixin, TemplateView):
    """Placeholder landing page for a club subdomain — where members land after
    signing in, until the club-facing site is built."""

    template_name = "club/home.html"


def root(request):
    """``/`` means different things per tenant.

    This is why allauth needs no login-redirect adapter: LOGIN_REDIRECT_URL is "/",
    and "/" resolves itself — a club subdomain lands on the club, the base domain
    hands off to the platform control panel.
    """
    if request.club is None:
        return redirect("controlpanel:dashboard")

    return ClubHomeView.as_view()(request)


def signup_closed(request):
    """Self-registration is closed -- see rosterchief/urls.py.

    Shadows allauth's own signup route rather than removing it, so the
    `account_signup` URL name every allauth template reverses still resolves and
    the login page doesn't 500 looking for it.
    """
    return render(request, "account/signup_closed.html", status=403)
