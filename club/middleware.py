from django.http import Http404, JsonResponse
from django_tenants.utils import get_public_schema_name


class ArchivedClubMiddleware:
    """An archived club's domains stop resolving: 404 on every path. Place right after the tenant middleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        club = getattr(request, "tenant", None)
        if club is not None and club.schema_name != get_public_schema_name() and club.archived_at is not None:
            if request.path.startswith("/api/"):
                return JsonResponse({"error": {"code": "not_found", "message": "Not found."}}, status=404)
            raise Http404
        return self.get_response(request)
