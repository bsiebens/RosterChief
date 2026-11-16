"""Auth backends that drop permissions whose feature the current club does not have.

Django asks *every* configured backend and grants a permission if any says yes, so each backend that can grant
permissions must filter, not just one of them. The platform owner is exempt (their entitlements bypass).
"""

from allauth.account.auth_backends import AuthenticationBackend
from tenant_users.permissions.backend import UserBackend

from authorization.catalog import CATALOG as PERMISSIONS

from .context import get_current_entitlements


class FeatureFilterMixin:
    def get_all_permissions(self, user_obj, obj=None):
        perms = super().get_all_permissions(user_obj, obj)
        gated = {code for code in perms if (p := PERMISSIONS.get(code)) is not None and p.feature}
        if not gated:
            return perms
        features = get_current_entitlements()
        return perms - {code for code in gated if not features.has(PERMISSIONS[code].feature)}


class FeatureAwareUserBackend(FeatureFilterMixin, UserBackend):
    """Tenant-aware permission backend (group permissions come from UserTenantPermissions)."""


class FeatureAwareAuthenticationBackend(FeatureFilterMixin, AuthenticationBackend):
    """allauth's login backend (email login), with the same permission filtering."""
