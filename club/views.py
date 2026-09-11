from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect, render

from .device import is_mobile_or_tablet
from .services.access import has_management_access


def root(request):
    """``/`` means different things per tenant, and per device.

    This is why allauth needs no login-redirect adapter: LOGIN_REDIRECT_URL is "/",
    and "/" resolves itself:

    * The base domain hands off to the platform control panel.
    * A club subdomain, on a phone or tablet, hands off to the member-facing PWA
      (mobile:home, which is itself LoginRequiredMixin -- an anonymous visitor lands
      on login with ?next= pointing back at it, same round trip as hitting /app/
      directly). Management isn't responsive yet, so touch devices always land here
      regardless of role.
    * A club subdomain, on desktop, goes to the management UI for anyone with
      has_management_access -- but that check needs a known user, so an anonymous
      desktop visitor is bounced to login with next=root itself (not straight at
      /manage/, which would 403 a logged-in visitor who turns out to have no
      management access instead of falling back). Once authenticated, root() is
      re-entered and can make the real call. A desktop visitor without management
      access falls back to the mobile PWA too -- there's nothing else on desktop for
      them to land on.

    Since this is the ordinary way of reaching a club subdomain on desktop at all,
    the anonymous branch also sets session["management_context"] before bouncing to
    login -- same flag ClubStaffRequiredMixin.dispatch sets for a real /manage/
    visit (club/mixins.py), read by club/context_processors.py's branding() to skin
    login, MFA, passkeys and everything else allauth chains next. If it turns out
    there's no management access after all, the flag is popped again right here
    before falling back to the mobile app, so that guess doesn't outlive this
    request and stick the management skin onto a plain member's session.
    """
    if request.club is None:
        return redirect("controlpanel:dashboard")

    if not is_mobile_or_tablet(request):
        if not request.user.is_authenticated:
            request.session["management_context"] = True
            return redirect_to_login(request.get_full_path())
        if has_management_access(request.user, request.club):
            return redirect("management:home")
        request.session.pop("management_context", None)

    return redirect("mobile:home")


def signup_closed(request):
    """Self-registration is closed -- see rosterchief/urls.py.

    Shadows allauth's own signup route rather than removing it, so the
    `account_signup` URL name every allauth template reverses still resolves and
    the login page doesn't 500 looking for it.
    """
    return render(request, "account/signup_closed.html", status=403)
