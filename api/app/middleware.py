"""Headers every app-API and auth-API response carries (API.md §15)."""

#: The native-app surfaces: the resource API and allauth's headless auth API.
APP_PREFIXES = ("/api/app/", "/api/auth/")


class AppApiHeadersMiddleware:
    """Personalised responses must never be stored by a shared cache or the device's URL
    cache: ``Cache-Control: private, no-store`` unless the view already set a policy.
    The docs pages under these prefixes are public and cacheable, so they're skipped."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(APP_PREFIXES) and not request.path.endswith(("/docs", "/openapi.json", "/openapi.yaml", "/openapi.html")):
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response
