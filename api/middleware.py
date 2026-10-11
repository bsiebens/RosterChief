"""CORS for the public API only.

Every route under /api/v1/ is public, read-only, and unauthenticated -- no
cookies or credentials are ever involved, so there's no CSRF/session risk in
answering any origin. That's the whole reason this is a few lines here
instead of pulling in django-cors-headers for a handful of GET routes: the
rest of the site keeps Django's ordinary same-origin behaviour untouched.
"""

from django.http import HttpResponse

API_PATH_PREFIX = "/api/v1/"

#: Endpoints whose data rarely changes and is the same for every visitor of one club's site, with how
#: long a browser or a shared cache (Cloudflare) may reuse the answer. The server already caches these
#: for 30 s (api/cache.py); this lets the caller skip even that round trip. Deliberately only the team
#: list: live scores, fixtures and news must stay fresh. Per-host, so one club's answer is never
#: served for another's subdomain.
PUBLIC_MAX_AGE = {"/api/v1/teams/": 60}


class PublicApiCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(API_PATH_PREFIX):
            return self.get_response(request)

        if request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        max_age = PUBLIC_MAX_AGE.get(request.path)
        if max_age and request.method == "GET" and response.status_code == 200:
            response["Cache-Control"] = f"public, max-age={max_age}"

        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response
