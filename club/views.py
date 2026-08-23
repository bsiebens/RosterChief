from django.shortcuts import redirect, render


def root(request):
    """``/`` means different things per tenant.

    This is why allauth needs no login-redirect adapter: LOGIN_REDIRECT_URL is "/",
    and "/" resolves itself — a club subdomain hands off to the member-facing PWA
    (mobile:home, which is itself LoginRequiredMixin -- an anonymous visitor lands on
    login with ?next= pointing back at it, same round trip as hitting /app/ directly),
    the base domain hands off to the platform control panel.
    """
    if request.club is None:
        return redirect("controlpanel:dashboard")

    return redirect("mobile:home")


def signup_closed(request):
    """Self-registration is closed -- see rosterchief/urls.py.

    Shadows allauth's own signup route rather than removing it, so the
    `account_signup` URL name every allauth template reverses still resolves and
    the login page doesn't 500 looking for it.
    """
    return render(request, "account/signup_closed.html", status=403)
