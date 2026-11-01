from django.http import HttpRequest
from ninja import NinjaAPI

from authorization.middleware import MFA_REQUIRED_MESSAGE
from authorization.services import ProtectedRoleError

from .auth import MFARequired
from features.entitlements import LimitExceeded, UnknownFeature


def register_error_handlers(api: NinjaAPI) -> None:
    @api.exception_handler(MFARequired)
    def mfa_required(request: HttpRequest, exc: MFARequired):
        return api.create_response(request, {"detail": MFA_REQUIRED_MESSAGE, "code": "mfa_required"}, status=403)

    @api.exception_handler(LimitExceeded)
    def limit_exceeded(request: HttpRequest, exc: LimitExceeded):
        return api.create_response(request, {"detail": str(exc), "code": "limit_exceeded", "feature": exc.code, "limit": exc.limit}, status=403)

    @api.exception_handler(ProtectedRoleError)
    def protected_role(request: HttpRequest, exc: ProtectedRoleError):
        return api.create_response(request, {"detail": str(exc), "code": "protected_role"}, status=400)

    @api.exception_handler(UnknownFeature)
    def unknown_feature(request: HttpRequest, exc: UnknownFeature):
        # A programming error (typo in a feature code), not something the caller can fix.
        return api.create_response(request, {"detail": "Server configuration error.", "code": "unknown_feature"}, status=500)
