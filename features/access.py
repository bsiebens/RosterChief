"""Guards for views: require the club to have a feature."""

from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied

from .entitlements import entitlements_for_request


def _features(request):
    return getattr(request, "features", None) or entitlements_for_request(request)


def feature_required(*codes):
    """``@feature_required("sponsoring")``: 403 unless the club has every listed feature."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            features = _features(request)
            if not all(features.has(c) for c in codes):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class FeatureRequiredMixin(AccessMixin):
    required_features: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        features = _features(request)
        if not all(features.has(c) for c in self.required_features):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
