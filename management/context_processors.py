"""Whether the signed-in user is a club ADMIN, for the management nav to hide
admin-only sections (seasons, positions, roles, shop, forms) from plain staff.

The underlying views are gated regardless (``ClubAdminRequiredMixin``) -- this is
purely so the nav doesn't show a link a coach or manager can't actually follow.
Same reasoning for ``news_permissions`` below, gating just the "New news item"
action rather than the whole section (``NewsAuthorRequiredMixin``/``can_add_news``).
"""

from waffle import flag_is_active

from billing.services.notices import club_billing_notice
from club.services.access import can_add_news, has_management_access, is_club_admin, is_coach_manager

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
    "member_referee_eligibility_update": "member_list",
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
    "group_list": "group_list",
    "group_create": "group_list",
    "group_detail": "group_list",
    "group_update": "group_list",
    "group_delete": "group_list",
    "group_bulk_add": "group_list",
    "group_member_remove": "group_list",
    "team_list": "team_list",
    "team_create": "team_list",
    "team_update": "team_list",
    "team_delete": "team_list",
    "team_detail": "team_list",
    "team_roster_add": "team_list",
    "team_bulk_add": "team_list",
    "team_roster_update": "team_list",
    "team_roster_remove": "team_list",
    "team_staff_add": "team_list",
    "team_staff_update": "team_list",
    "team_staff_remove": "team_list",
    "team_photo_set": "team_list",
    "team_photo_delete": "team_list",
    "referee_list": "referee_list",
    "referee_management": "referee_management",
    "referee_level_list": "referee_level_list",
    "referee_level_create": "referee_level_list",
    "referee_level_update": "referee_level_list",
    "news_list": "news_list",
    "news_create": "news_list",
    "news_detail": "news_list",
    "news_update": "news_list",
    "news_delete": "news_list",
    "news_publish": "news_list",
    "news_unpublish": "news_list",
    "news_photo_upload": "news_list",
    "news_photo_set_main": "news_list",
    "news_photo_delete": "news_list",
    "event_list": "event_list",
    "event_create": "event_list",
    "event_detail": "event_list",
    "event_update": "event_list",
    "event_delete": "event_list",
    "event_detach": "event_list",
    "event_fetch_game_info": "event_list",
    "event_referee_assign": "event_list",
    "event_referee_add_external": "event_list",
    "event_referee_remove": "event_list",
    "event_referee_fee_update": "event_list",
    "event_referee_form_pdf": "event_list",
    "rbihf_import": "event_list",
    "rbihf_import_confirm": "event_list",
    "event_series_create": "event_list",
    "event_series_detail": "event_list",
    "event_series_update": "event_list",
    "event_series_delete": "event_list",
    "event_series_stop": "event_list",
    "event_series_generate": "event_list",
    "location_list": "location_list",
    "location_create": "location_list",
    "location_update": "location_list",
    "location_delete": "location_list",
    "opponent_list": "opponent_list",
    "opponent_create": "opponent_list",
    "opponent_update": "opponent_list",
    "opponent_delete": "opponent_list",
    "sponsor_list": "sponsor_list",
    "sponsor_create": "sponsor_list",
    "sponsor_update": "sponsor_list",
    "sponsor_delete": "sponsor_list",
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


def billing_notice(request):
    """What this club owes the platform, for the club's own admins.

    A context processor rather than view context because the notice has to be able to follow
    an admin onto every management page once it turns urgent -- billing/base.html renders it
    at error level only, and the home page renders it at every level.

    Admins only: platform billing is none of an ordinary member's business, and the query is
    skipped entirely for everyone else rather than fetched and hidden in the template.
    """
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated or not is_club_admin(request.user, club):
        return {"billing_notice": None}

    return {"billing_notice": club_billing_notice(club)}


def management_position(request):
    """Whether the signed-in user holds a management position (or is ADMIN) --
    gates the nav's Locations/Opponents links, which ``ManagementPositionRequiredMixin``
    restricts to exactly this group (unlike most staff-visible sections)."""
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated:
        return {"has_management_position": False}

    return {"has_management_position": is_club_admin(request.user, club) or is_coach_manager(request.user, club)}


def management_link(request):
    """Whether to show a "Management" link in the global navbar (templates/_base.html),
    next to the Django admin one -- only on a club subdomain, and only for someone with
    real authority there (see has_management_access)."""
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated:
        return {"has_management_access": False}

    return {"has_management_access": has_management_access(request.user, club)}


def feature_sections(request):
    """Whether the nav's Shop/Forms sections -- and the Events page's "Import
    from RBIHF" button -- should show at all. Each is gated behind its own
    waffle Flag (see club.mixins.FeatureRequiredMixin, which gates the
    underlying views regardless), on top of the existing is_club_admin check
    those all already require."""
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated:
        return {"shop_enabled": False, "forms_enabled": False, "rbihf_enabled": False}

    return {
        "shop_enabled": flag_is_active(request, "shop"),
        "forms_enabled": flag_is_active(request, "formbuilder"),
        "rbihf_enabled": flag_is_active(request, "RBIHF"),
    }


def news_permissions(request):
    """Whether the "New news item" action should show -- viewing the News
    section itself is open to any staff, same as Roster/Staff."""
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated:
        return {"can_add_news": False}

    return {"can_add_news": can_add_news(request.user, club)}
