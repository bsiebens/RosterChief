import re

from django.http import HttpResponse

# The trailing slash distinguishes public news and team reads from management reads.
# Keep the allowlist narrow because both APIs share /api/v1/.
PUBLIC_PATH = re.compile(r"/api/v1/(?:news/(?:[^/]+/)?|teams/(?:standings/|[^/]+/(?:roster|games|standings)/)?|games/[^/]+/|sponsors/|docs|openapi\.json)")


class PublicApiCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if request.method not in {"GET", "OPTIONS"} or not PUBLIC_PATH.fullmatch(path):
            return self.get_response(request)

        response = HttpResponse(status=204) if request.method == "OPTIONS" else self.get_response(request)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        if path == "/api/v1/teams/" and request.method == "GET" and response.status_code == 200:
            response["Cache-Control"] = "public, max-age=60"
        return response
