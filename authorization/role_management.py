"""Management rules for roles: create, edit (name, description, permissions), delete, and give them to people. Every write starts with
``require_manage_roles``. The Owner role is locked (it always holds the whole catalog); other system roles cannot be deleted."""

from django.db import transaction
from django.utils.text import slugify

from members.models import Member
from members.services import access
from members.services.errors import Invalid

from .catalog import CATALOG, OWNER_SLUG
from .models import Role
from .services import ProtectedRoleError, assign_role, create_role, remove_role, set_role_perms


def roles_queryset():
    return Role.objects.select_related("group").order_by("-is_system", "group__name")


def get_role(role_id) -> Role | None:
    return roles_queryset().filter(pk=role_id).first()


def member_count(role: Role) -> int:
    return role.group.user_set.count()


def role_codes(role: Role) -> list[str]:
    """The catalog codes the role holds (the Owner role always holds all of them)."""
    if role.slug == OWNER_SLUG:
        return list(CATALOG)
    return sorted(f"{ct}.{codename}" for ct, codename in role.group.permissions.values_list("content_type__app_label", "codename") if f"{ct}.{codename}" in CATALOG)


def role_members(role: Role):
    """The people (members with a login) holding the role."""
    return Member.objects.filter(user_id__in=role.group.user_set.values_list("profile_id", flat=True)).order_by("last_name", "first_name")


def _check(name: str | None, codes, *, exclude: Role | None = None) -> None:
    errors = {}
    if name is not None:
        if not name.strip():
            errors["name"] = ["This field is required."]
        elif len(name.strip()) > 150:
            errors["name"] = ["Use at most 150 characters."]
        else:
            same = Role.objects.filter(group__name__iexact=name.strip())
            if exclude is not None:
                same = same.exclude(pk=exclude.pk)
            if same.exists():
                errors["name"] = ["A role with this name already exists."]
    unknown = set(codes or []) - set(CATALOG)
    if unknown:
        errors["permissions"] = [f"Unknown permission: {sorted(unknown)[0]}."]
    if errors:
        raise Invalid("Check the role.", errors)


def _unique_slug(name: str) -> str:
    base = slugify(name)[:45] or "role"
    slug, n = base, 2
    while Role.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


@transaction.atomic
def create(actor, *, name: str, description: str = "", permissions=()) -> Role:
    access.require_manage_roles(actor)
    _check(name, permissions)
    return create_role(name.strip(), permissions, slug=_unique_slug(name), description=description.strip())


@transaction.atomic
def update(actor, role: Role, *, name=None, description=None, permissions=None) -> Role:
    access.require_manage_roles(actor)
    if role.slug == OWNER_SLUG:
        raise ProtectedRoleError("The Owner role always holds every permission and cannot be edited.")
    _check(name, permissions, exclude=role)
    if name is not None:
        role.group.name = name.strip()
        role.group.save(update_fields=["name"])
    if description is not None:
        role.description = description.strip()
        role.save(update_fields=["description", "modified"])
    if permissions is not None:
        set_role_perms(role, permissions)
    return role


@transaction.atomic
def delete(actor, role: Role) -> None:
    access.require_manage_roles(actor)
    if role.is_system:
        raise ProtectedRoleError("System roles cannot be deleted.")
    role.group.delete()  # cascades to the Role and drops it from everyone who held it


def _holder(member_id) -> Member:
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        raise Invalid("Unknown member.", {"member_id": ["Unknown member."]})
    if member.user_id is None:
        raise Invalid("This person has no login.", {"member_id": ["This person has no login, so they cannot hold a role."]}, code="no_login")
    return member


@transaction.atomic
def add_member(actor, role: Role, member_id) -> Member:
    access.require_manage_roles(actor)
    member = _holder(member_id)
    assign_role(member.user, role)
    return member


@transaction.atomic
def remove_member(actor, role: Role, member_id) -> None:
    access.require_manage_roles(actor)
    remove_role(_holder(member_id).user, role)  # raises ProtectedRoleError for a club's last Owner
