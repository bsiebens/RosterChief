"""Fixed permission catalog. Clubs build their own roles from these codes.

Each app adds its permissions here. ``admin=True`` marks capabilities that make
the holder a "club admin" (and therefore subject to mandatory MFA).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Perm:
    code: str  # "<app_label>.<codename>", matches user.has_perm()
    label: str
    admin: bool = False
    feature: str | None = None  # code of the feature the club needs for this permission to apply


CATALOG: dict[str, Perm] = {
    p.code: p
    for p in [
        Perm("authorizations.manage_roles", "Manage roles", admin=True),
        Perm("members.manage_members", "Manage members", admin=True),
        Perm("admin.manage_settings", "Manage settings", admin=True),
        Perm("members.execute_evaluations", "Execute evaluations"),
        Perm("members.create_evaluations", "Create evaluations"),
        Perm("shop.manage_shop", "Manage shop", admin=True),
        Perm("events.manage_referees", "Manage referees", admin=True),
        Perm("news.publish_newsitems", "Publish newsitems", admin=True),
    ]
}

OWNER_SLUG = "owner"

# slug: (name, permission codes)
DEFAULT_ROLES: dict[str, tuple[str, list[str]]] = {
    OWNER_SLUG: ("Owner", list(CATALOG)),
    "member": ("Member", []),
}

ADMIN_CODES = frozenset(code for code, p in CATALOG.items() if p.admin)
