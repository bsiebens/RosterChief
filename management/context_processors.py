"""Whether the signed-in user is a club ADMIN, for the management nav to hide
admin-only sections (seasons, positions, roles, shop, forms) from plain staff.

The underlying views are gated regardless (``ClubAdminRequiredMixin``) -- this is
purely so the nav doesn't show a link a coach or manager can't actually follow.
Same reasoning for ``news_permissions`` below, gating just the "New news item"
action rather than the whole section (``NewsAuthorRequiredMixin``/``can_add_news``).
"""

from waffle import flag_is_active

from billing.services.notices import club_billing_notice
from club.services.access import can_add_news, can_manage_members, has_management_access, is_club_admin, is_coach_manager
from members.models import ParentClaim

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
    "family_list": "family_list",
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
    "parent_claim_list": "parent_claim_list",
    "parent_claim_approve": "parent_claim_list",
    "parent_claim_reject": "parent_claim_list",
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
    "product_create": "product_list",
    "product_update": "product_list",
    "product_toggle_active": "product_list",
    "product_variant_create": "product_list",
    "product_variant_update": "product_list",
    "product_variant_toggle_active": "product_list",
    "product_category_create": "product_list",
    "product_category_update": "product_list",
    "product_category_delete": "product_list",
    "order_list": "order_list",
    "order_detail": "order_list",
    "order_mark_paid": "order_list",
    "order_mark_ready_for_pickup": "order_list",
    "order_bulk_mark_paid": "order_list",
    "order_bulk_mark_ready_for_pickup": "order_list",
    "order_production_export": "order_list",
    "order_production_download": "order_list",
    "order_production_reprint": "order_list",
    "payment_update": "order_list",
    "payment_delete": "order_list",
    "order_add_payment": "order_list",
    "order_line_update": "order_list",
    "order_mark_delivered": "order_list",
    "order_cancel": "order_list",
    "order_invoice_pdf": "order_list",
    "discount_list": "discount_list",
    "discount_create": "discount_list",
    "discount_update": "discount_list",
    "discount_toggle_active": "discount_list",
    "voucher_list": "voucher_list",
    "voucher_create": "voucher_list",
    "voucher_update": "voucher_list",
    "invoice_list": "invoice_list",
    "membership_invoice_detail": "invoice_list",
    "form_list": "form_list",
    "form_create": "form_list",
    "form_detail": "form_list",
    "form_update": "form_list",
    "form_field_create": "form_list",
    "form_field_update": "form_list",
    "form_field_toggle_active": "form_list",
    "formsend_create": "form_list",
    "formsend_detail": "form_list",
    "formsend_update": "form_list",
    "formsend_responses": "form_list",
    "formsend_responses_export": "form_list",
    "evaluations": "evaluations",
    "club_settings": "club_settings",
    "onboarding_requirement_list": "onboarding_requirement_list",
    "onboarding_requirement_create": "onboarding_requirement_list",
    "onboarding_requirement_update": "onboarding_requirement_list",
    "onboarding_requirement_delete": "onboarding_requirement_list",
    "member_requirement_complete": "member_list",
    "member_requirement_bypass": "member_list",
    "member_requirement_incomplete": "member_list",
    "member_requirement_document": "member_list",
    "signup_list": "signup_list",
    "signup_approve_all_clean": "signup_list",
    "signup_approve_one": "signup_list",
    "signup_place_in_team": "signup_list",
}


#: The redesigned sidebar has two levels (design_handoff_rosterchief_platform/README.md,
#: D1's 236px sidebar: Overview/Members/Teams/Calendar/News/Finance/Settings, each except
#: News expandable into sub-items -- D2 shows Members expanded as All members/Sign-ups/
#: Households/Roles). This maps each *leaf* nav value from _NAV_SECTIONS above to which
#: top-level item should be open/highlighted; management/templates/management/_nav_items.html
#: reads `nav_section` for that, and `nav` (unchanged) for which sub-item.
#:
#: Membership dues tracking moved from Members to Finance here versus the old flat nav --
#: D6 "Dues & billing" is a Finance concern in the design, not a People one.
_TOP_SECTION = {
    "home": "overview",
    "member_list": "members",
    "signup_list": "members",
    "family_list": "members",
    "parent_claim_list": "members",
    "group_list": "members",
    "evaluations": "members",
    "membership_list": "finance",
    "team_list": "teams",
    "referee_list": "teams",
    "news_list": "news",
    "event_list": "calendar",
    "location_list": "calendar",
    "opponent_list": "calendar",
    "sponsor_list": "finance",
    "product_list": "finance",
    "order_list": "finance",
    "discount_list": "finance",
    "voucher_list": "finance",
    "invoice_list": "finance",
    "form_list": "settings",
    "club_settings": "settings",
    "onboarding_requirement_list": "settings",
    "role_list": "settings",
    "position_list": "settings",
    "referee_level_list": "settings",
    "referee_management": "calendar",
}


def active_nav_section(request):
    """Which management nav item is currently active, derived from the resolved
    URL name. Guarded on the "management" namespace so a same-named url_name in
    some other app can never leak into this."""
    match = request.resolver_match
    if match is None or match.namespace != "management":
        return {"nav": None, "nav_section": None}

    nav = _NAV_SECTIONS.get(match.url_name)
    return {"nav": nav, "nav_section": _TOP_SECTION.get(nav)}


def is_admin(request):
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated:
        return {"is_club_admin": False, "can_manage_members": False}

    return {
        "is_club_admin": is_club_admin(request.user, club),
        # Gates the nav items MemberAdminRequiredMixin also gates at the view layer
        # (Members/Teams/Referee setup/Onboarding requirements) -- real ADMIN (which
        # already includes the platform-superuser bypass) or MEMBER_ADMIN.
        "can_manage_members": can_manage_members(request.user, club),
    }


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


def sidebar_counters(request):
    """Small always-visible counts next to two nav links that flag a queue
    waiting on an admin: pending parent claims, and upcoming club-managed games
    nobody's down to referee yet (management.views.games_missing_referees_count,
    the same shape RefereeManagementDashboardView's own kpi_no_referee uses for
    its default "next 10" range).

    Gated on can_manage_members (real ADMIN or MEMBER_ADMIN), matching how
    _nav_items.html itself gates both links -- a coach never sees either link, so
    there's no reason to run either query for them. Always an int when shown,
    never hidden at 0: "the queue is empty" and "nobody checked" have to read
    differently.
    """
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated or not can_manage_members(request.user, club):
        return {"pending_parent_claims_count": None, "games_missing_referees_count": None}

    # Imported here rather than at module level to keep this module's own import
    # graph small -- management.views pulls in most of the app's models/services,
    # none of which any other context processor here needs.
    from management.views import RefereeManagementDashboardView, games_missing_referees_count

    return {
        "pending_parent_claims_count": ParentClaim.objects.filter(club=club, status=ParentClaim.Status.PENDING).count(),
        "games_missing_referees_count": games_missing_referees_count(club, limit=int(RefereeManagementDashboardView.DEFAULT_RANGE)),
    }


def notification_bell(request):
    """The topbar bell's badge count and dropdown contents -- every signed-in
    staff member can have notifications (not just admins, unlike
    sidebar_counters' admin-only queues above), since notifications.Notification
    is keyed to whichever Member they are, not to a role.

    None (not 0) when there's no club/signed-in user/Member row to key off --
    same "hidden means not applicable, 0 means an empty inbox" distinction
    sidebar_counters draws."""
    club = getattr(request, "club", None)
    if club is None or not request.user.is_authenticated:
        return {"unread_notification_count": None, "recent_notifications": None}

    from members.models import Member
    from notifications.models import Notification

    member = Member.objects.filter(user=request.user).first()
    if member is None:
        return {"unread_notification_count": None, "recent_notifications": None}

    notifications = Notification.objects.filter(club=club, member=member).order_by("-created")[:8]
    return {
        "unread_notification_count": Notification.objects.filter(club=club, member=member, read_at__isnull=True).count(),
        "recent_notifications": notifications,
    }
