"""Tenant-aware page branding.

Every page inherits its chrome from ``base_template``. On a club subdomain that
resolves to the club-branded skin, on the base domain to the platform one — the
control panel's own industrial design system (assets/controlpanel.css) — so the
auth screens (login, password reset, MFA, passkeys — anything allauth ships, now
or later) follow the tenant without a single template of their own knowing that
clubs exist. templates/403.html and templates/maintenance.html extend
``base_template`` directly too, so they follow the same split.

A club subdomain serves two very different chromes, though: the public club site
(daisyUI, assets/app.css) and the management app (assets/management.css) live on
the same tenant, distinguished only by path. Without the checks below, a staff
member clicking "Change password" from inside the management app would land back
on the club's *public* skin -- jarring, and visually nothing like where they just
were. MANAGEMENT_BASE_TEMPLATE picks up management/base.html's own chrome instead,
for two cases: a request path directly under /manage/ (matching management/urls.py's
own hardcoded "manage/" prefix in rosterchief/urls.py -- e.g. a 403 on a management
page), and the session flag ClubStaffRequiredMixin.dispatch sets on every management
view (club/mixins.py) -- needed because allauth's password-change/MFA/logout screens
live under /accounts/, outside /manage/, so the path check alone can't see they were
reached from the management app's own user menu.

The control panel's own pages deliberately do *not* use this: controlpanel/base.html
hardcodes itself, so no branding bug can ever dress the platform panel up as a club.
"""

PLATFORM_BASE_TEMPLATE = "controlpanel/_auth_base.html"
CLUB_BASE_TEMPLATE = "_club_base.html"
MANAGEMENT_BASE_TEMPLATE = "management/_auth_base.html"


def branding(request):
    club = getattr(request, "club", None)  # set by ClubTenantMiddleware

    if club and (request.path.startswith("/manage/") or request.session.get("management_context")):
        base_template = MANAGEMENT_BASE_TEMPLATE
    elif club:
        base_template = CLUB_BASE_TEMPLATE
    else:
        base_template = PLATFORM_BASE_TEMPLATE

    return {
        "club": club,
        "base_template": base_template,
    }
