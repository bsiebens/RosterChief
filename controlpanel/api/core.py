from django.conf import settings
from ninja import NinjaAPI

from . import errors
from .auth import staff_auth

api = NinjaAPI(
    title="RosterChief Platform API",
    version="1",
    description="Control panel for platform staff. Base domain only; authenticate with X-Session-Token (/_allauth/app/v1/).",
    auth=staff_auth,
    urls_namespace="platform",
    docs_url="/docs" if settings.DEBUG else None,
)
errors.install(api)
