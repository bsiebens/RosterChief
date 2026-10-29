"""The one API of the platform, mounted at /api/v1/ (rosterchief/urls.py).

A single NinjaAPI with two kinds of routes, kept apart by package:

* ``api.public`` -- unauthenticated, read-only, club-scoped by host (a club's own website).
  Open CORS, short server-side cache (api/core/cache.py).
* ``api.protected`` -- bearer-token routes for the member/coach apps (native and web).
* ``api.panel`` -- the management panel's routes; also protected, authorised per club role.

Authentication is declared per router below (``PUBLIC`` / ``PROTECTED``), never per endpoint,
so a new router can't become public by accident. Account endpoints answer on the base domain,
club endpoints on the club's subdomain (API.md §4); the same token works on both. Every error
uses the envelope of api/core/errors.py. Shared building blocks live in ``api.core``.
"""

from ninja import NinjaAPI

from api.core import surface
from api.core.auth import app_auth
from api.core.errors import install_handlers
from api.panel.routers.bugs import router as panel_bugs_router
from api.panel.routers.club_settings import router as panel_club_settings_router
from api.panel.routers.dashboard import router as panel_dashboard_router
from api.panel.routers.event_extras import router as panel_event_extras_router
from api.panel.routers.event_officials import router as panel_event_officials_router
from api.panel.routers.event_timeline import router as panel_event_timeline_router
from api.panel.routers.events import router as panel_events_router
from api.panel.routers.evaluations import router as panel_evaluations_router
from api.panel.routers.fees import router as panel_fees_router
from api.panel.routers.forms_admin import router as panel_forms_admin_router
from api.panel.routers.forms_sends import router as panel_forms_sends_router
from api.panel.routers.forms_submissions import router as panel_forms_submissions_router
from api.panel.routers.groups import router as panel_groups_router
from api.panel.routers.jobs import router as panel_jobs_router
from api.panel.routers.levels import router as panel_levels_router
from api.panel.routers.locations import router as panel_locations_router
from api.panel.routers.me import router as panel_me_router
from api.panel.routers.members import router as panel_members_router
from api.panel.routers.members_import import router as panel_members_import_router
from api.panel.routers.members_onboarding import router as panel_members_onboarding_router
from api.panel.routers.news import router as panel_news_router
from api.panel.routers.news_photos import router as panel_news_photos_router
from api.panel.routers.notifications import router as panel_notifications_router
from api.panel.routers.number_pools import router as panel_number_pools_router
from api.panel.routers.numbers import router as panel_numbers_router
from api.panel.routers.opponents import router as panel_opponents_router
from api.panel.routers.positions import router as panel_positions_router
from api.panel.routers.referee_management import router as panel_referee_management_router
from api.panel.routers.registrations import router as panel_registrations_router
from api.panel.routers.roles import router as panel_roles_router
from api.panel.routers.seasons_admin import router as panel_seasons_admin_router
from api.panel.routers.shop_catalog import router as panel_shop_catalog_router
from api.panel.routers.shop_discounts import router as panel_shop_discounts_router
from api.panel.routers.shop_orders import router as panel_shop_orders_router
from api.panel.routers.shop_vouchers import router as panel_shop_vouchers_router
from api.panel.routers.sponsors import router as panel_sponsors_router
from api.panel.routers.team_analytics_pdf import router as panel_team_analytics_pdf_router
from api.panel.routers.team_extras import router as panel_team_extras_router
from api.panel.routers.teams import router as panel_teams_router
from api.panel.routers.volunteers import router as panel_volunteers_router
from api.protected.routers.account import router as account_router
from api.protected.routers.club import router as club_router
from api.protected.routers.coach import router as coach_router
from api.protected.routers.coach_evaluations import router as coach_evaluations_router
from api.protected.routers.coach_events import router as coach_events_router
from api.protected.routers.coach_lineup import router as coach_lineup_router
from api.protected.routers.coach_news import router as coach_news_router
from api.protected.routers.coach_roster import router as coach_roster_router
from api.protected.routers.forms import router as forms_router
from api.protected.routers.inbox import router as inbox_router
from api.protected.routers.member import router as member_router
from api.protected.routers.payments import router as payments_router
from api.protected.routers.people import router as people_router
from api.protected.routers.registration import router as registration_router
from api.protected.routers.shop import router as shop_router
from api.protected.routers.support import router as support_router
from api.public.routers.events import router as public_events_router
from api.public.routers.news import router as public_news_router
from api.public.routers.sponsors import router as public_sponsors_router
from api.public.routers.teams import router as public_teams_router

DESCRIPTION = """
Everything RosterChief exposes over HTTP: a club's public data and the app API. Read the
developer guide first: **[/api/docs/](/api/docs/)**.

* **Public routes** (`/news`, `/teams`, `/games`, `/standings`, `/sponsors`): no token, on a club's
  own host, read-only.
* **Sign-in** is a separate API (allauth headless): `/api/auth/app/v1/`, spec at
  [/api/auth/openapi.html](/api/auth/openapi.html). It returns the `access_token` used here.
* **Protected routes:** `Authorization: Bearer <access_token>` on every call except `/meta`.
* **Two planes:** account endpoints (`/me`, `/clubs`, `/devices`, `/meta`) on the base domain;
  club endpoints (`/context`, …) on the club's own host — take it from `api_base_url` in `/clubs`.
* **Who:** club endpoints act for the account's managed people (`/context`). Reads take
  `member_id` (one person) or `all`; writes always name one `member_id`.
* **Errors** always use `{"error": {"code", "message", "fields"}}`; branch on `code`.
"""

api = NinjaAPI(
    title="RosterChief API",
    version="1",
    description=DESCRIPTION,
    urls_namespace="api",
    docs_url="/docs",
    openapi_url="/openapi.json",
)
install_handlers(api)

#: (prefix, router): no authentication.
PUBLIC = [
    ("/news", public_news_router),
    ("/teams", public_teams_router),
    ("/", public_events_router),
    ("/sponsors", public_sponsors_router),
]

#: Routers that require ``Authorization: Bearer``. Individual endpoints may opt out (``auth=None``).
PROTECTED = [
    account_router,
    club_router,
    member_router,
    inbox_router,
    people_router,
    payments_router,
    forms_router,
    support_router,
    shop_router,
    registration_router,
    coach_router,
    coach_events_router,
    coach_lineup_router,
    coach_roster_router,
    coach_news_router,
    coach_evaluations_router,
    panel_me_router,
    panel_members_import_router,
    panel_members_onboarding_router,
    panel_members_router,
    panel_fees_router,
    panel_evaluations_router,
    panel_news_router,
    panel_team_analytics_pdf_router,
    panel_team_extras_router,  # before panel_teams_router: its static /manage/teams/reorder must win over /manage/teams/{team_id}
    panel_teams_router,
    panel_events_router,
    panel_event_timeline_router,
    panel_event_officials_router,
    panel_referee_management_router,
    panel_event_extras_router,
    panel_jobs_router,
    panel_groups_router,
    panel_positions_router,
    panel_number_pools_router,
    panel_numbers_router,
    panel_seasons_admin_router,
    panel_locations_router,
    panel_opponents_router,
    panel_news_photos_router,
    panel_club_settings_router,
    panel_roles_router,
    panel_sponsors_router,
    panel_levels_router,
    panel_volunteers_router,
    panel_registrations_router,
    panel_forms_admin_router,
    panel_forms_submissions_router,
    panel_dashboard_router,
    panel_bugs_router,
    panel_notifications_router,
    panel_forms_sends_router,
    panel_shop_catalog_router,
    panel_shop_discounts_router,
    panel_shop_vouchers_router,
    panel_shop_orders_router,
]

for prefix, router in PUBLIC:
    api.add_router(prefix, router, auth=None)
for router in PROTECTED:
    api.add_router("/", router, auth=app_auth)

surface.register_public(PUBLIC)
