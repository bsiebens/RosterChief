from ninja import NinjaAPI

from . import events, news, sponsors, teams

api = NinjaAPI(
    title="RosterChief public API",
    version="1.0.0",
    description="Public, read-only data for a club's own external website: news, team rosters, league standings, fixtures, results, and sponsors.",
    urls_namespace="legacy_public_api",
)
api.add_router("/news", news.router)
api.add_router("/teams", teams.router)
api.add_router("/", events.router)
api.add_router("/sponsors", sponsors.router)
