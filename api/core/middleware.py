"""HTTP-level behaviour of the API surfaces: legacy prefix, CORS and cache headers.

* ``/api/v1/`` public routes (api.public): read-only, no credentials ever involved, so any
  origin may read them (``Access-Control-Allow-Origin: *``, GET/OPTIONS only).
* ``/api/v1/`` protected routes: bearer token only (no cookies, so no CSRF surface). Browsers
  from a configured frontend origin (``API_CORS_ALLOWED_ORIGINS``) may call them; credentials
  are never allowed. Personalised answers are ``Cache-Control: private, no-store``.
* ``/api/auth/`` (allauth headless): no CORS, same no-store policy.
"""

from django.conf import settings
from django.http import HttpResponse

from .surface import is_public_path

API_PATH_PREFIX = "/api/v1/"
AUTH_PATH_PREFIX = "/api/auth/"
#: The prefix the native apps used before the two APIs merged; answered as ``/api/v1/``.
LEGACY_PREFIX = "/api/app/v1/"

#: Endpoints whose data rarely changes and is the same for every visitor of one club's site, with how
#: long a browser or a shared cache (Cloudflare) may reuse the answer. The server already caches these
#: for 30 s (api/core/cache.py); this lets the caller skip even that round trip. Deliberately only the team
#: list: live scores, fixtures and news must stay fresh. Per-host, so one club's answer is never
#: served for another's subdomain.
PUBLIC_MAX_AGE = {"/api/v1/teams/": 60}

PROTECTED_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
PROTECTED_HEADERS = "Authorization, Content-Type, Idempotency-Key, Accept-Language"
UNCACHED_SUFFIXES = ("/docs", "/openapi.json", "/openapi.yaml", "/openapi.html")


class LegacyAppPrefixMiddleware:
    """Deprecated alias: ``/api/app/v1/...`` is served as ``/api/v1/...`` (same endpoints, same
    token), flagged with ``Deprecation``. Rewritten before any other middleware looks at the path."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path_info.startswith(LEGACY_PREFIX):
            return self.get_response(request)

        rewritten = API_PATH_PREFIX + request.path_info[len(LEGACY_PREFIX) :]
        request.path_info = rewritten
        request.path = rewritten
        response = self.get_response(request)
        response["Deprecation"] = "true"
        response["Link"] = f'<{API_PATH_PREFIX}>; rel="successor-version"'
        return response


class ApiMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith(API_PATH_PREFIX):
            return self.v1(request, path)
        if path.startswith(AUTH_PATH_PREFIX):
            response = self.get_response(request)
            if not path.endswith(UNCACHED_SUFFIXES):
                response.headers.setdefault("Cache-Control", "private, no-store")
            return response
        return self.get_response(request)

    def v1(self, request, path):
        public = is_public_path(path)
        if request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        if public:
            max_age = PUBLIC_MAX_AGE.get(path)
            if max_age and request.method == "GET" and response.status_code == 200:
                response["Cache-Control"] = f"public, max-age={max_age}"
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        response.headers.setdefault("Cache-Control", "private, no-store")
        origin = request.headers.get("Origin")
        if origin and origin in settings.API_CORS_ALLOWED_ORIGINS:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = PROTECTED_METHODS
            response["Access-Control-Allow-Headers"] = PROTECTED_HEADERS
            response["Access-Control-Expose-Headers"] = "Idempotency-Replayed, Retry-After, Deprecation"
            response["Access-Control-Max-Age"] = "600"
        if origin:
            response["Vary"] = "Origin"
        return response
