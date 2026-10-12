"""The authenticated native-app API, mounted at /api/app/v1/ (rosterchief/urls.py).

A second NinjaAPI next to the public one (api/urls.py) on purpose: its own auth default,
error envelope and OpenAPI document. Account endpoints answer on the base domain, club
endpoints on the club's subdomain (API.md §4); the same token works on both.
"""

from ninja import NinjaAPI

from .auth import app_auth
from .errors import install_handlers
from .routers.account import router as account_router
from .routers.club import router as club_router
from .routers.coach import router as coach_router
from .routers.coach_events import router as coach_events_router
from .routers.coach_lineup import router as coach_lineup_router
from .routers.coach_roster import router as coach_roster_router
from .routers.forms import router as forms_router
from .routers.inbox import router as inbox_router
from .routers.member import router as member_router
from .routers.payments import router as payments_router
from .routers.people import router as people_router
from .routers.registration import router as registration_router
from .routers.shop import router as shop_router
from .routers.support import router as support_router

DESCRIPTION = """
The API the RosterChief native apps are built on. Read the developer guide first:
**[/api/docs/](/api/docs/)**.

* **Sign-in** is a separate API (allauth headless): `/api/auth/app/v1/`, spec at
  [/api/auth/openapi.html](/api/auth/openapi.html). It returns the `access_token` used here.
* **Authentication:** `Authorization: Bearer <access_token>` on every call except `/meta`.
* **Two planes:** account endpoints (`/me`, `/clubs`, `/devices`, `/meta`) on the base domain;
  club endpoints (`/context`, …) on the club's own host — take it from `api_base_url` in `/clubs`.
* **Who:** club endpoints act for the account's managed people (`/context`). Reads take
  `member_id` (one person) or `all`; writes always name one `member_id`.
* **Errors** always use `{"error": {"code", "message", "fields"}}`; branch on `code`.
"""

app_api = NinjaAPI(
    title="RosterChief app API",
    version="1",
    description=DESCRIPTION,
    urls_namespace="app_api",
    auth=app_auth,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

install_handlers(app_api)
app_api.add_router("/", account_router)
app_api.add_router("/", club_router)
app_api.add_router("/", member_router)
app_api.add_router("/", inbox_router)
app_api.add_router("/", people_router)
app_api.add_router("/", payments_router)
app_api.add_router("/", forms_router)
app_api.add_router("/", support_router)
app_api.add_router("/", shop_router)
app_api.add_router("/", registration_router)
app_api.add_router("/", coach_router)
app_api.add_router("/", coach_events_router)
app_api.add_router("/", coach_lineup_router)
app_api.add_router("/", coach_roster_router)
