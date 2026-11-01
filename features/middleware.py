from django.utils.functional import SimpleLazyObject

from .context import current_entitlements
from .entitlements import entitlements_for_request


class FeaturesMiddleware:
    """Expose ``request.features`` (lazy, resolved at most once per request). Place after authentication."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.features = SimpleLazyObject(lambda: entitlements_for_request(request))
        token = current_entitlements.set(request.features)
        try:
            return self.get_response(request)
        finally:
            current_entitlements.reset(token)
