from django.http import HttpRequest
from ninja import NinjaAPI

from authorization.middleware import MFA_REQUIRED_MESSAGE
from authorization.services import ProtectedRoleError
from features.entitlements import LimitExceeded, UnknownFeature
from members.services.errors import Invalid, NotAllowed

from .auth import MFARequired, NoManagementAccess


def register_error_handlers(api: NinjaAPI) -> None:
    @api.exception_handler(MFARequired)
    def mfa_required(request: HttpRequest, exc: MFARequired):
        return api.create_response(request, {"detail": MFA_REQUIRED_MESSAGE, "code": "mfa_required"}, status=403)

    @api.exception_handler(NoManagementAccess)
    def no_management_access(request: HttpRequest, exc: NoManagementAccess):
        return api.create_response(request, {"detail": "The management app is for club admins, owners and team staff.", "code": "no_access"}, status=403)

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

    @api.exception_handler(NotAllowed)
    def not_allowed(request: HttpRequest, exc: NotAllowed):
        return api.create_response(request, {"detail": str(exc) or "Not allowed.", "code": "forbidden"}, status=403)

    @api.exception_handler(Invalid)
    def invalid(request: HttpRequest, exc: Invalid):
        return api.create_response(request, {"detail": str(exc), "code": getattr(exc, "code", "invalid"), "errors": exc.errors or {}}, status=422)
