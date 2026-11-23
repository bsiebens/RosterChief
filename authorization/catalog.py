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
        Perm("registration.manage_finance", "Manage registration finance", admin=True),
        Perm("finance.manage_charges", "Manage charge invoices (fines and other costs)", admin=True),
        Perm("sponsoring.manage_sponsors", "Manage sponsors", admin=True),
        Perm("evaluations.manage_evaluations", "Manage evaluations", feature="evaluations"),
        Perm("evaluations.execute_evaluations", "Execute and view evaluations", feature="evaluations"),
        Perm("shop.manage_shop", "Manage shop", admin=True),
        Perm("shop.manage_products", "Manage shop products", feature="shop"),
        Perm("shop.manage_inventory", "Manage shop inventory", feature="shop"),
        Perm("shop.manage_orders", "Manage shop orders", feature="shop"),
        Perm("shop.manage_discounts", "Manage shop discounts and vouchers", feature="shop"),
        Perm("shop.manage_invoices", "Manage shop invoices", feature="shop"),
        Perm("events.manage_referees", "Manage referees", admin=True),
        Perm("news.publish_newsitems", "Publish newsitems", admin=True),
        Perm("tournaments.manage_tournaments", "Manage tournaments (settings, registrations, teams, payments)", admin=True, feature="tournaments"),
        Perm("tournaments.operate_tournaments", "Operate tournaments (rosters, schedule, results, dressing rooms, shifts)", feature="tournaments"),
        Perm("chat.moderate", "Moderate chat (see chats with minors, handle reports)", feature="chat"),
        Perm("chat.announce", "Start club-wide announcement chats", feature="chat"),
        Perm("forms.manage_forms", "Manage forms"),
    ]
}

OWNER_SLUG = "owner"

# slug: (name, permission codes)
DEFAULT_ROLES: dict[str, tuple[str, list[str]]] = {
    OWNER_SLUG: ("Owner", list(CATALOG)),
    "member": ("Member", []),
}

ADMIN_CODES = frozenset(code for code, p in CATALOG.items() if p.admin)
