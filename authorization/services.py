from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils.text import slugify

from .catalog import CATALOG, DEFAULT_ROLES, OWNER_SLUG
from .models import Role


class ProtectedRoleError(Exception):
    """Raised when an operation would break the Owner role or leave a club without an owner."""


def sync_permissions() -> dict[str, Permission]:
    """Ensure a Permission row exists in the current schema for every catalog code."""
    out = {}
    for code, perm in CATALOG.items():
        app_label, codename = code.split(".", 1)
        ct, _ = ContentType.objects.get_or_create(app_label=app_label, model="clubpermission")
        out[code], _ = Permission.objects.get_or_create(content_type=ct, codename=codename, defaults={"name": perm.label})
    return out


def _validate(codes) -> list[str]:
    unknown = set(codes) - set(CATALOG)
    if unknown:
        raise ValueError(f"Unknown permissions: {sorted(unknown)}")
    return list(codes)


def _set_perms(role: Role, codes) -> None:
    perms = sync_permissions()
    role.group.permissions.set([perms[c] for c in _validate(codes)])


@transaction.atomic
def set_role_perms(role: Role, codes) -> None:
    """Replace a role's permissions. The Owner role always holds the full catalog and cannot be edited."""
    if role.slug == OWNER_SLUG:
        raise ProtectedRoleError("The Owner role always holds every permission and cannot be edited.")
    _set_perms(role, codes)


@transaction.atomic
def create_role(name: str, codes=(), *, slug: str | None = None, description: str = "", is_system: bool = False) -> Role:
    group = Group.objects.create(name=name)
    role = Role.objects.create(group=group, slug=slug or slugify(name), description=description, is_system=is_system)
    _set_perms(role, codes)
    return role


def _owner_group():
    return Role.objects.get(slug=OWNER_SLUG).group


def assign_role(user, role: Role) -> None:
    """Add ``role`` to ``user`` in the current club (schema). Users can hold several roles."""
    user.tenant_perms.groups.add(role.group)


@transaction.atomic
def remove_role(user, role: Role) -> None:
    """Remove ``role`` from ``user``. A club's last Owner cannot be removed."""
    _guard_last_owner(user, [role.group_id])
    user.tenant_perms.groups.remove(role.group)


@transaction.atomic
def set_roles(user, roles) -> None:
    """Replace all of ``user``'s roles with ``roles``. A club's last Owner cannot lose the Owner role."""
    _guard_last_owner(user, [r.group_id for r in roles], replacing=True)
    user.tenant_perms.groups.set([r.group for r in roles])


def validate_roles(user, roles) -> None:
    """Raise ``ProtectedRoleError`` if replacing ``user``'s roles with ``roles`` is not allowed."""
    _guard_last_owner(user, [r.group_id for r in roles], replacing=True)


def _guard_last_owner(user, keep_or_remove_ids, replacing=False) -> None:
    owner = _owner_group()
    perms = user.tenant_perms
    if not perms.groups.filter(pk=owner.pk).exists():
        return
    losing_owner = owner.pk not in keep_or_remove_ids if replacing else owner.pk in keep_or_remove_ids
    if losing_owner and not owner.user_set.exclude(pk=perms.pk).exists():
        raise ProtectedRoleError("A club must keep at least one Owner. Transfer ownership first.")


@transaction.atomic
def seed_default_roles() -> None:
    for slug, (name, codes) in DEFAULT_ROLES.items():
        role = Role.objects.filter(slug=slug).first()
        if role is None:
            create_role(name, codes, slug=slug, is_system=True)
        elif slug == OWNER_SLUG:
            _set_perms(role, codes)  # Owner always holds the full catalog


def user_perm_codes(user) -> set[str]:
    return set(user.get_all_permissions())


def roles_by_club(user, clubs=None) -> list[tuple]:
    """``[(club, [role names])]`` for ``clubs`` (default: every club ``user`` belongs to)."""
    from django_tenants.utils import schema_context

    out = []
    for club in clubs if clubs is not None else user.tenants.order_by("name"):
        with schema_context(club.schema_name):
            perms = type(user).objects.get(pk=user.pk).tenant_perms
            out.append((club, sorted(perms.groups.values_list("name", flat=True))))
    return out
