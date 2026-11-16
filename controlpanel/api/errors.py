"""One error envelope for the whole platform API: ``{"error": {"code", "message", "fields"?}}``."""

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import ProtectedError
from django.http import Http404, HttpRequest
from ninja import NinjaAPI
from ninja.errors import AuthenticationError, HttpError, ValidationError

from billing.services import BillingError

STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "server_error",
    503: "maintenance",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str | None = None, message: str = "", fields: dict | None = None, report: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.fields, self.report = status, code or STATUS_CODES.get(status, "error"), message, fields, report


class MFAEnrollmentRequired(Exception):
    pass


def envelope(status: int, code: str, message: str, fields: dict | None = None, report: dict | None = None) -> dict:
    body = {"code": code, "message": message}
    if fields:
        body["fields"] = fields
    if report:
        body["report"] = report
    return {"error": body}


def install(api: NinjaAPI) -> None:
    def respond(request: HttpRequest, status: int, code: str, message: str, fields=None, report=None):
        response = api.create_response(request, envelope(status, code, message, fields, report), status=status)
        if status == 401:
            response["WWW-Authenticate"] = "Bearer"
        return response

    @api.exception_handler(ApiError)
    def _api_error(request, exc: ApiError):
        return respond(request, exc.status, exc.code, exc.message, exc.fields, exc.report)

    @api.exception_handler(ValidationError)
    def _validation(request, exc: ValidationError):
        fields: dict[str, list[str]] = {}
        for err in exc.errors:
            loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path", "form", "header", "cookie")]
            fields.setdefault(".".join(loc) or "non_field", []).append(err.get("msg", "Invalid value"))
        return respond(request, 422, "validation_error", "The request was not valid.", fields)

    @api.exception_handler(DjangoValidationError)
    def _model_validation(request, exc: DjangoValidationError):
        fields = {k: [str(m) for m in v] for k, v in exc.message_dict.items()} if hasattr(exc, "error_dict") else {"non_field": [str(m) for m in exc.messages]}
        return respond(request, 422, "validation_error", "The request was not valid.", fields)

    @api.exception_handler(HttpError)
    def _http_error(request, exc: HttpError):
        return respond(request, exc.status_code, STATUS_CODES.get(exc.status_code, "error"), str(exc))

    @api.exception_handler(AuthenticationError)
    def _unauthenticated(request, exc):
        return respond(request, 401, "unauthenticated", "Authentication credentials were missing or invalid.")

    @api.exception_handler(Http404)
    def _not_found(request, exc):
        return respond(request, 404, "not_found", "Not found.")

    @api.exception_handler(PermissionDenied)
    def _forbidden(request, exc):
        return respond(request, 403, "forbidden", "You do not have permission to do this.")

    @api.exception_handler(ProtectedError)
    def _protected(request, exc):
        return respond(request, 409, "in_use", "This record is still referenced by other records and cannot be deleted.")

    @api.exception_handler(BillingError)
    def _billing(request, exc):
        return respond(request, 409, "billing_error", str(exc))

    @api.exception_handler(MFAEnrollmentRequired)
    def _mfa(request, exc):
        return respond(request, 403, "mfa_enrollment_required", "Enrol a multi-factor method before using the platform API.")
