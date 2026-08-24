"""Tenant-aware page branding.

Every page inherits its chrome from ``base_template``. On a club subdomain that
resolves to the club-branded skin, on the base domain to the platform one — the
control panel's own industrial design system (assets/controlpanel.css) — so the
auth screens (login, password reset, MFA, passkeys — anything allauth ships, now
or later) follow the tenant without a single template of their own knowing that
clubs exist. templates/403.html and templates/maintenance.html extend
``base_template`` directly too, so they follow the same split.

A club subdomain serves three very different chromes, though: the public club site
(daisyUI, assets/app.css), the member app (assets/mobile.css), and the management
app (assets/management.css) all live on the same tenant, distinguished only by
path. Without the checks below, a member clicking "Log out" from inside the app
would land back on the club's old *public* skin -- jarring, and visually nothing
like where they just were. MANAGEMENT_BASE_TEMPLATE/MOBILE_BASE_TEMPLATE pick up
management/base.html's or mobile/base.html's own chrome instead, for a request
path directly under /manage/ or /app/ respectively (matching management/urls.py's
and mobile/urls.py's own hardcoded prefixes in rosterchief/urls.py -- e.g. a 403
on a management page), plus the session flag each app's own view mixin sets --
management_context (ClubStaffRequiredMixin.dispatch, club/mixins.py) and
mobile_context (PersonScopeMixin/CoachScopeMixin.dispatch, mobile/mixins.py and
mobile/coach_mixins.py). Both are needed because allauth's login/logout/password-
change/MFA screens live under /accounts/, outside /manage/ and /app/, so the path
check alone can't see which app's user menu sent someone there.

The control panel's own pages deliberately do *not* use this: controlpanel/base.html
hardcodes itself, so no branding bug can ever dress the platform panel up as a club.
"""

PLATFORM_BASE_TEMPLATE = "controlpanel/_auth_base.html"
CLUB_BASE_TEMPLATE = "_club_base.html"
MANAGEMENT_BASE_TEMPLATE = "management/_auth_base.html"
MOBILE_BASE_TEMPLATE = "mobile/_auth_base.html"


def branding(request):
    club = getattr(request, "club", None)  # set by ClubTenantMiddleware

    if club and (request.path.startswith("/manage/") or request.session.get("management_context")):
        base_template = MANAGEMENT_BASE_TEMPLATE
    elif club and (request.path.startswith("/app/") or request.session.get("mobile_context")):
        base_template = MOBILE_BASE_TEMPLATE
    elif club:
        base_template = CLUB_BASE_TEMPLATE
    else:
        base_template = PLATFORM_BASE_TEMPLATE

    return {
        "club": club,
        "base_template": base_template,
    }
