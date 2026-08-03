"""Whether the signed-in user is a club ADMIN, for the management nav to hide
admin-only sections (seasons, positions, roles, shop, forms) from plain staff.

The underlying views are gated regardless (``ClubAdminRequiredMixin``) -- this is
purely so the nav doesn't show a link a coach or manager can't actually follow.
"""

from club.services.access import has_management_access, is_club_admin

#: Every management URL name, mapped to the nav item it should light up --
#: management/templates/management/_nav_items.html compares against this.
#: One dict here beats threading `nav=` through every view in views.py, and
#: unlike that, a new page can't silently be forgotten (it just renders with
#: no active item until added below, rather than needing every view touched).
_NAV_SECTIONS = {
    "home": "home",
    "member_list": "member_list",
    "member_create": "member_list",
    "member_import_template": "member_list",
    "member_import": "member_list",
    "member_import_confirm": "member_list",
    "member_detail": "member_list",
    "member_update": "member_list",
    "member_delete": "member_list",
    "member_attach_family": "member_list",
    "member_grant_login": "member_list",
    "member_detach_family": "member_list",
    "family_create": "member_list",
    "family_detail": "member_list",
    "family_add_child": "member_list",
    "family_add_parent": "member_list",
    "family_membership_role_update": "member_list",
    "membership_list": "membership_list",
    "membership_mark_paid": "membership_list",
    "membership_export_pdf": "membership_list",
    "membership_mark_fully_paid": "membership_list",
    "membership_record_payment": "membership_list",
    "position_list": "position_list",
    "position_create": "position_list",
    "position_update": "position_list",
    "role_list": "role_list",
    "role_create": "role_list",
    "role_revoke": "role_list",
    "team_list": "team_list",
    "team_create": "team_list",
    "team_update": "team_list",
    "team_detail": "team_list",
    "roster_list": "roster_list",
    "staff_list": "staff_list",
    "event_list": "event_list",
    "event_series_list": "event_series_list",
    "location_list": "location_list",
    "opponent_list": "opponent_list",
    "product_list": "product_list",
    "order_list": "order_list",
    "discount_list": "discount_list",
    "invoice_list": "invoice_list",
    "form_list": "form_list",
    "submission_list": "form_list",
}


def active_nav_section(request):
    """Which management nav item is currently active, derived from the resolved
    URL name. Guarded on the "management" namespace so a same-named url_name in
    some other app can never leak into this."""
    match = request.resolver_match
    if match is None or match.namespace != "management":
        return {"nav": None}

    return {"nav": _NAV_SECTIONS.get(match.url_name)}


def is_admin(request):
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated:
        return {"is_club_admin": False}

    return {"is_club_admin": is_club_admin(request.user, club)}


def management_link(request):
    """Whether to show a "Management" link in the global navbar (templates/_base.html),
    next to the Django admin one -- only on a club subdomain, and only for someone with
    real authority there (see has_management_access)."""
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated:
        return {"has_management_access": False}

    return {"has_management_access": has_management_access(request.user, club)}
