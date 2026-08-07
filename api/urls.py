"""The public read-only API -- see ARCHITECTURE.md and the plan this shipped
under. Mounted at /api/v1/ (rosterchief/urls.py), club-scoped by the same
subdomain-based tenant resolution every other view uses
(club.tenancy.ClubTenantMiddleware sets request.club before this ever runs).

Each domain app owns its own router and schemas (news/api.py, teams/api.py,
events/api.py) -- this module only wires them together, same reasoning as
management/controlpanel never owning domain logic themselves.
"""

from ninja import NinjaAPI

from club.api import router as club_router
from events.api import router as events_router
from news.api import router as news_router
from teams.api import router as teams_router

api = NinjaAPI(
    title="RosterChief public API",
    version="1.0.0",
    description="Public, read-only data for a club's own external website: news, team rosters, fixtures, and sponsors.",
    urls_namespace="api",
)

api.add_router("/news", news_router)
api.add_router("/teams", teams_router)
api.add_router("/", events_router)
api.add_router("/sponsors", club_router)
