"""The single error envelope of the app API (API.md §6).

Every error -- ours, ninja's validation errors, auth failures, 404s -- answers::

    {"error": {"code": "not_a_member", "message": "…localised…", "fields": {...}}}

``code`` is a stable machine string (never translated); ``message`` is localised and
safe to show; ``fields`` only appears for validation errors (field name -> messages).
allauth's own ``/api/auth/`` responses keep allauth's shape (§6).
"""

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils.translation import gettext as _
from ninja.errors import AuthenticationError, HttpError
from ninja.errors import ValidationError as NinjaValidationError

#: Default code per HTTP status, for errors that don't carry their own.
STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    415: "unsupported_media_type",
    410: "gone",
    422: "validation_error",
    429: "rate_limited",
    503: "maintenance",
}


class ApiError(Exception):
    """Raise from any endpoint or service call to answer with the envelope."""

    def __init__(self, status: int, code: str, message=None, fields: dict | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields


def error_body(code: str, message, fields: dict | None = None) -> dict:
    error = {"code": code, "message": str(message)}
    if fields:
        error["fields"] = fields
    return {"error": error}


def _default_message(status: int) -> str:
    return {
        400: _("The request could not be understood."),
        401: _("Sign in to continue."),
        403: _("You don't have access to this."),
        404: _("Not found."),
        405: _("This method isn't allowed here."),
        409: _("That can't be done right now."),
        413: _("The file is too large."),
        415: _("This file type isn't accepted."),
        422: _("Some fields need attention."),
        429: _("Too many requests. Try again shortly."),
        503: _("RosterChief is down for maintenance. It will be back shortly."),
    }.get(status, _("Something went wrong."))


def _validation_fields(errors) -> dict:
    """ninja's pydantic errors (``[{loc, msg, type}]``) -> ``{field: [message]}``. The
    location's first element is where the value came from (body/query/path), dropped."""
    fields: dict[str, list[str]] = {}
    for error in errors:
        location = [str(part) for part in error.get("loc", ())[1:]] or ["non_field_errors"]
        fields.setdefault(".".join(location), []).append(str(error.get("msg", "")))
    return fields


def install_handlers(api) -> None:
    """Register the envelope for every error type on ``api`` (a NinjaAPI)."""

    def respond(request, status, code, message=None, fields=None):
        return api.create_response(request, error_body(code, message or _default_message(status), fields), status=status)

    @api.exception_handler(ApiError)
    def on_api_error(request, exc: ApiError):
        response = respond(request, exc.status, exc.code, exc.message, exc.fields)
        if exc.status == 401:
            response["WWW-Authenticate"] = "Bearer"
        return response

    @api.exception_handler(NinjaValidationError)
    def on_validation_error(request, exc: NinjaValidationError):
        return respond(request, 422, "validation_error", fields=_validation_fields(exc.errors))

    @api.exception_handler(AuthenticationError)
    def on_authentication_error(request, exc):
        response = respond(request, 401, "unauthenticated")
        response["WWW-Authenticate"] = "Bearer"
        return response

    @api.exception_handler(HttpError)
    def on_http_error(request, exc: HttpError):
        # ninja's own HttpErrors carry untranslated English; the localised default is shown instead.
        return respond(request, exc.status_code, STATUS_CODES.get(exc.status_code, "error"))

    @api.exception_handler(Http404)
    def on_not_found(request, exc):
        return respond(request, 404, "not_found")

    @api.exception_handler(PermissionDenied)
    def on_permission_denied(request, exc):
        return respond(request, 403, "forbidden")


def require_club(request):
    """The club on this host, or a 404 for the bare base domain, an unknown slug or an
    archived club (nothing to serve). The guard of every club-scoped public route;
    protected routes use ``api.core.auth.require_club_member`` instead."""
    club = getattr(request, "club", None)
    if club is None:
        raise ApiError(404, "not_found", _("No club found for this host."))
    return club
