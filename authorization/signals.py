from django.db.models.signals import post_migrate, pre_delete
from django.dispatch import receiver
from django_tenants.signals import post_schema_sync
from django_tenants.utils import get_public_schema_name, schema_context


@receiver(post_schema_sync)
def seed_new_club(sender, tenant, **kwargs):
    """Seed default roles in a brand-new club schema and enrol its owner."""
    from club.models import Club

    from .models import Role
    from .services import assign_role, seed_default_roles

    schema_name = tenant["schema_name"] if isinstance(tenant, dict) else tenant.schema_name
    if schema_name == get_public_schema_name():
        return
    club = Club.objects.get(schema_name=schema_name)
    with schema_context(schema_name):
        seed_default_roles()
    club.add_user(club.owner)  # runs in the club schema
    with schema_context(schema_name):
        owner = type(club.owner).objects.get(pk=club.owner_id)
        assign_role(owner, Role.objects.get(slug="owner"))


@receiver(post_migrate)
def sync_catalog(sender, **kwargs):
    """Keep permissions (and the Owner role) in step with the catalog after migrations."""
    from django.db import connection

    if sender.name != "authorization" or connection.schema_name == get_public_schema_name():
        return
    from .models import Role
    from .services import seed_default_roles, sync_permissions

    sync_permissions()
    if Role.objects.exists():
        seed_default_roles()


@receiver(pre_delete, sender="authorization.Role")
def protect_system_roles(sender, instance, **kwargs):
    """Block deleting system roles, including via cascades (e.g. deleting the underlying Group)."""
    from .services import ProtectedRoleError

    if instance.is_system:
        raise ProtectedRoleError(f"The {instance} role is a system role and cannot be deleted.")
