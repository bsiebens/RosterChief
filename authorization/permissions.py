from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django_tenants.utils import get_public_schema_name, schema_context

from .catalog import ADMIN_CODES


def is_platform_owner(user) -> bool:
    """True for a superuser of the public tenant (the platform owner)."""
    if not user.is_authenticated or not user.is_active:
        return False
    with schema_context(get_public_schema_name()):
        perms = getattr(user.__class__.objects.get(pk=user.pk), "tenant_perms", None)
        return bool(perms and perms.is_superuser)


def is_club_admin(user) -> bool:
    """True if the user is a platform owner or holds any admin-flagged permission in the current club."""
    if not user.is_authenticated:
        return False
    return is_platform_owner(user) or bool(ADMIN_CODES & set(user.get_all_permissions()))


def club_permission_required(*codes):
    def decorator(view):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return AccessMixin().handle_no_permission()
            if not (is_platform_owner(request.user) or request.user.has_perms(codes)):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class ClubPermissionRequiredMixin(AccessMixin):
    required_perms: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (is_platform_owner(request.user) or request.user.has_perms(self.required_perms)):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
