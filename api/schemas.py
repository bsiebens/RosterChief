from ninja import Schema


class ClubOut(Schema):
    slug: str
    name: str
    is_platform: bool


class BrandingOut(Schema):
    """What the login page of a club needs: public by design, so nothing here may be private."""

    slug: str
    name: str
    is_platform: bool
    logo_url: str | None = None
    #: Browser icons generated from the logo: keys ico, png_32, png_192, png_512, apple_touch (site-relative URLs). Empty without a logo.
    icons: dict[str, str] = {}
    primary_color: str = ""
    secondary_color: str = ""
    primary_content_color: str = ""
    secondary_content_color: str = ""


class MyClubOut(Schema):
    slug: str
    name: str
    host: str
    logo_url: str | None = None
    primary_color: str = ""
    primary_content_color: str = ""


class MeOut(Schema):
    id: str
    member_id: str | None = None
    has_member_portal_role: bool = False
    email: str
    first_name: str
    last_name: str
    club: ClubOut
    roles: list[str]
    permissions: list[str]
    is_club_admin: bool
    can_manage_members: bool = False
    can_manage_settings: bool = False
    can_manage_finance: bool = False
    can_manage_charges: bool = False
    can_manage_sponsors: bool = False
    can_execute_evaluations: bool = False
    can_manage_evaluations: bool = False
    can_manage_roles: bool = False
    can_manage_referees: bool = False
    can_publish_news: bool = False
    can_add_news: bool = False
    can_manage_forms: bool = False
    can_manage_shop_products: bool = False
    can_manage_shop_inventory: bool = False
    can_manage_shop_orders: bool = False
    can_manage_shop_discounts: bool = False
    can_manage_shop_invoices: bool = False
    shop_vat_mode: str = "off"
    managed_team_ids: list[str] = []
    admin_group_ids: list[str] = []
    features: list[str]
