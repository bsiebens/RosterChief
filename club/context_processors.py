"""Tenant-aware page branding.

Every page inherits its chrome from ``base_template``. On a club subdomain that
resolves to the club-branded skin, on the base domain to the RosterChief one, so
the auth screens (login, password reset, MFA, passkeys — anything allauth ships,
now or later) follow the tenant without a single template of their own knowing
that clubs exist.

The control panel deliberately does *not* use this: it hardcodes the platform
base, so no branding bug can ever dress the platform panel up as a club.
"""

PLATFORM_BASE_TEMPLATE = "_platform_base.html"
CLUB_BASE_TEMPLATE = "_club_base.html"


def branding(request):
    club = getattr(request, "club", None)  # set by ClubTenantMiddleware

    return {
        "club": club,
        "base_template": CLUB_BASE_TEMPLATE if club else PLATFORM_BASE_TEMPLATE,
    }
