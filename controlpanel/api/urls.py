"""Registers the platform API's routers. Import ``api`` from here so every router is attached."""

from .core import api
from .routers import access, billing, clubs, entitlements, jobs, push, reference, stats, switches, users

api.add_router("", access.router)
api.add_router("", clubs.router)
api.add_router("", entitlements.router)
api.add_router("", jobs.router)
api.add_router("", reference.router)
api.add_router("", switches.router)
api.add_router("", billing.router)
api.add_router("", stats.router)
api.add_router("", users.router)
api.add_router("", push.router)

__all__ = ["api"]
