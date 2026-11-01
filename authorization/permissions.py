from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django_tenants.utils import get_public_schema_name, schema_context

from .catalog import ADMIN_CODES


def _public_perms(user):
    """The user's permissions row in the public schema (platform level), or None if they have none."""
    if not user.is_authenticated or not user.is_active:
        return None
    with schema_context(get_public_schema_name()):
        try:
            return user.__class__.objects.get(pk=user.pk).tenant_perms
        except ObjectDoesNotExist:
            return None


def is_platform_owner(user) -> bool:
    """True for a superuser of the public tenant (the platform owner)."""
    perms = _public_perms(user)
    return bool(perms and perms.is_superuser)


def is_platform_staff(user) -> bool:
    """True for platform staff or superusers: staff/superuser flags on the user's public-schema permissions."""
    perms = _public_perms(user)
    return bool(perms and (perms.is_staff or perms.is_superuser))


def is_platform_superuser(user) -> bool:
    perms = _public_perms(user)
    return bool(perms and perms.is_superuser)


def is_club_admin(user) -> bool:
    """True if the user is a platform owner or holds any admin-flagged permission in the current club."""
    if not user.is_authenticated:
        return False
    return is_platform_staff(user) or bool(ADMIN_CODES & set(user.get_all_permissions()))


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
