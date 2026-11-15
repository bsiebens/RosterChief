"""Who may see and do what in the management app.

Door: only club admins, owners and team staff get into the management app at all (``can_access_management``);
everyone else gets 403 ``no_access`` from every endpoint. Behind the door there are four tiers:

* **View** (everyone who got in): every member with their details, every team with its roster and staff, the numbers overview
  and the club's referees and officials. Guardian-only and staff-only people (no club membership in any season) are
  left out of the default member listing unless ``include_guardians=True``.
* **Members** (``members.manage_members``, the Owner role or the platform owner): add, edit and delete members and
  everything attached to them (families, groups, memberships, pictures, logins, eligibility).
* **Teams**: editing a team, its roster and its staff needs a *management position* (``Position.management_position``)
  on that very team in the current season. Creating, deleting and reordering teams is club-wide, so it needs the
  settings permission instead.
* **Evaluations**: two permissions, each also held by the Owner role and the platform owner. ``evaluations.manage_evaluations`` builds the
  checklists (the criteria players are scored on); ``evaluations.execute_evaluations`` gives evaluations, runs evaluation meetings and
  is the only way to see evaluations (the Evaluations tab on a member). Neither implies the other.
* **Referees** (``events.manage_referees``, the Owner role or the platform owner): assign referees and officials to home games,
  set their fees and print the payment forms.
* **Settings** (``admin.manage_settings``, the Owner role or the platform owner): club details and branding,
  positions, number pools, referee / official levels, creating, deleting and ordering teams.
"""

import datetime

from django.db.models import Exists, OuterRef
from django.utils import timezone

from authorization.permissions import is_platform_owner
from members.models import ClubMembership, Member

MANAGE_MEMBERS = "members.manage_members"
MANAGE_SETTINGS = "admin.manage_settings"
MANAGE_FINANCE = "registration.manage_finance"
MANAGE_SPONSORS = "sponsoring.manage_sponsors"
EXECUTE_EVALUATIONS = "evaluations.execute_evaluations"
MANAGE_EVALUATIONS = "evaluations.manage_evaluations"
MANAGE_REFEREES = "events.manage_referees"


def can_manage_members(user) -> bool:
    """Platform owner, or a user holding ``members.manage_members`` in the active club (schema)."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(MANAGE_MEMBERS)


def can_manage_finance(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``registration.manage_finance``: the billing review of registrations, their
    invoices and the payments against them. Separate from ``members.manage_members``, which neither implies nor is implied by it."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or _is_club_owner(user) or user.has_perm(MANAGE_FINANCE)


def can_manage_sponsors(user) -> bool:
    """The platform owner, club Owner and dedicated sponsor managers can edit sponsorships."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(MANAGE_SPONSORS) or _is_club_owner(user)


def require_sponsors(actor) -> None:
    from .errors import NotAllowed

    if not can_manage_sponsors(actor):
        raise NotAllowed("You are not allowed to manage sponsors.")


def can_record_fee_payments(user) -> bool:
    """Who may record or remove a payment on a membership fee: member admins (the member page) and finance (the finance page)."""
    return can_manage_members(user) or can_manage_finance(user)


def staffed_team_ids(user, *, on: datetime.date | None = None) -> set:
    """Ids of the teams ``user`` is staffed on (a StaffAssignment) in the season covering ``on`` (default today)."""
    from events.models import Season
    from teams.models import StaffAssignment

    if user is None or not user.is_authenticated:
        return set()
    season = Season.get_current(on)
    if season is None:
        return set()
    return set(StaffAssignment.objects.filter(member__user=user, season=season).values_list("team_id", flat=True))


def members_visible_to(user, *, include_guardians: bool = False, on: datetime.date | None = None):
    """Members ``user`` may see (queryset): everyone, except that guardian-only and staff-only people (no club
    membership in any season) are left out unless ``include_guardians``. Nobody for an anonymous or inactive user."""
    if user is None or not user.is_authenticated or not user.is_active:
        return Member.objects.none()
    qs = Member.objects.all()
    if not include_guardians:
        is_member = ClubMembership.objects.filter(member=OuterRef("pk"))
        only_other_roles = (Member.objects.guardians() | Member.objects.staff()).exclude(Exists(is_member))
        qs = qs.exclude(pk__in=only_other_roles.values("pk"))
    return qs


def can_view_member(user, member: Member, *, on: datetime.date | None = None) -> bool:
    """True if ``member`` is in the set ``members_visible_to`` gives ``user`` (guardian-only people included)."""
    return members_visible_to(user, include_guardians=True, on=on).filter(pk=member.pk).exists()


def can_edit_member(user, member: Member | None = None) -> bool:
    """Only holders of ``members.manage_members`` edit members; everyone else is read-only."""
    return can_manage_members(user)


def has_staff_role(user) -> bool:
    """True if ``user`` is staff on a team in a season that is current or still to come."""
    from django.utils import timezone

    from teams.models import StaffAssignment

    if user is None or not user.is_authenticated:
        return False
    return StaffAssignment.objects.filter(member__user=user, season__end_date__gte=timezone.now().date()).exists()


def can_access_management(user) -> bool:
    """Who may use the management app at all: club admins (any admin-flagged permission), the Owner role, the platform
    owner / staff, and team staff. Everyone else (players, parents, plain members) has no access to it."""
    from authorization.permissions import is_club_admin

    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_club_admin(user) or _is_club_owner(user) or has_staff_role(user) or bool(admin_group_ids(user))


def can_manage_settings(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``admin.manage_settings`` in the active club."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(MANAGE_SETTINGS) or _is_club_owner(user)


def can_execute_evaluations(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``evaluations.execute_evaluations``: gives and sees evaluations."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(EXECUTE_EVALUATIONS) or _is_club_owner(user)


def can_manage_evaluations(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``evaluations.manage_evaluations``: builds the checklists."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(MANAGE_EVALUATIONS) or _is_club_owner(user)


MANAGE_ROLES = "authorizations.manage_roles"


def can_manage_roles(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``authorizations.manage_roles``: creates and edits roles, sets their
    permissions and gives them to people."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(MANAGE_ROLES) or _is_club_owner(user)


def require_manage_roles(actor) -> None:
    from .errors import NotAllowed

    if not can_manage_roles(actor):
        raise NotAllowed("You are not allowed to manage roles.")


def require_execute_evaluations(actor) -> None:
    from .errors import NotAllowed

    if not can_execute_evaluations(actor):
        raise NotAllowed("You are not allowed to give or view evaluations.")


def require_manage_evaluations(actor) -> None:
    from .errors import NotAllowed

    if not can_manage_evaluations(actor):
        raise NotAllowed("You are not allowed to manage evaluations.")


def can_manage_referees(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``events.manage_referees``: arranges referees and officials."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(MANAGE_REFEREES) or _is_club_owner(user)


def _is_club_owner(user) -> bool:
    from django.core.exceptions import ObjectDoesNotExist

    from authorization.catalog import OWNER_SLUG
    from authorization.models import Role

    try:
        groups = user.tenant_perms.groups.all()
    except ObjectDoesNotExist:
        return False
    return Role.objects.filter(slug=OWNER_SLUG, group__in=groups).exists()


def managed_team_ids(user, *, on: datetime.date | None = None) -> set:
    """Ids of the teams ``user`` holds a management position on (head coach, ...) in the season covering ``on``."""
    from events.models import Season
    from teams.models import StaffAssignment

    if user is None or not user.is_authenticated or not user.is_active:
        return set()
    season = Season.get_current(on)
    if season is None:
        return set()
    return set(StaffAssignment.objects.filter(member__user=user, season=season, position__management_position=True).values_list("team_id", flat=True))


def can_manage_team(user, team) -> bool:
    """Settings holders manage every team; anyone else needs a management position on this team this season."""
    if can_manage_settings(user):
        return True
    return team is not None and team.pk in managed_team_ids(user)


def user_group_ids(user) -> set:
    """Ids of the groups the user's own member record is in."""
    from members.models import GroupMembership

    if user is None or not user.is_authenticated:
        return set()
    return set(GroupMembership.objects.filter(member__user=user).values_list("group_id", flat=True))


def admin_group_ids(user) -> set:
    """Ids of the groups the user's own member record administers (``GroupMembership.is_admin``)."""
    from members.models import GroupMembership

    if user is None or not user.is_authenticated or not user.is_active:
        return set()
    return set(GroupMembership.objects.filter(member__user=user, is_admin=True).values_list("group_id", flat=True))


def can_manage_group(user, group) -> bool:
    """Members managers manage every group (create, rename, delete, appoint admins); a group's own admins manage its members."""
    if can_manage_members(user):
        return True
    return group is not None and group.pk in admin_group_ids(user)


def can_schedule(user, *, team_ids=(), group_ids=(), club_wide=False) -> bool:
    """May ``user`` create or edit an event with this audience? Settings holders always; everyone else only for events
    that are entirely about teams they manage and/or groups they administer (never club-wide, never audience-less)."""
    if can_manage_settings(user):
        return True
    team_ids, group_ids = set(team_ids), set(group_ids)
    if club_wide or not (team_ids or group_ids) or not user.is_authenticated or not user.is_active:
        return False
    return team_ids <= managed_team_ids(user) and group_ids <= admin_group_ids(user)


def can_manage_event(user, event) -> bool:
    """Edit or delete ``event`` (or its series): same rule as creating one with its audience."""
    return can_schedule(user, team_ids=event.teams.values_list("pk", flat=True), group_ids=event.groups.values_list("pk", flat=True), club_wide=event.club_wide)


def can_manage_attendance(user, event) -> bool:
    """Set answers, notes, check-ins and the line-up of ``event``: settings holders, a manager of any of its teams, or an
    admin of any of its groups."""
    if can_manage_settings(user):
        return True
    return bool(set(event.teams.values_list("pk", flat=True)) & managed_team_ids(user)) or bool(set(event.groups.values_list("pk", flat=True)) & admin_group_ids(user))


def can_manage_any_team(user) -> bool:
    return can_manage_settings(user) or bool(managed_team_ids(user))


def require_admin(actor) -> None:
    """Raise ``NotAllowed`` unless ``actor`` may manage members. Every service write starts with this."""
    from .errors import NotAllowed

    if not can_manage_members(actor):
        raise NotAllowed("You are not allowed to manage members.")


def require_finance(actor) -> None:
    """Raise ``NotAllowed`` unless ``actor`` may handle registration finance."""
    from .errors import NotAllowed

    if not can_manage_finance(actor):
        raise NotAllowed("You are not allowed to manage registration finance.")


def require_record_fee_payments(actor) -> None:
    from .errors import NotAllowed

    if not can_record_fee_payments(actor):
        raise NotAllowed("You are not allowed to record payments.")


def require_group_manager(actor, group) -> None:
    """Raise ``NotAllowed`` unless ``actor`` manages ``group``'s members (members manager, or an admin of the group)."""
    from .errors import NotAllowed

    if not can_manage_group(actor, group):
        raise NotAllowed("You are not allowed to manage this group.")


def require_settings(actor) -> None:
    """Raise ``NotAllowed`` unless ``actor`` may manage the club's settings."""
    from .errors import NotAllowed

    if not can_manage_settings(actor):
        raise NotAllowed("You are not allowed to manage the club settings.")


def require_referees(actor) -> None:
    """Raise ``NotAllowed`` unless ``actor`` may arrange referees and officials."""
    from .errors import NotAllowed

    if not can_manage_referees(actor):
        raise NotAllowed("You are not allowed to manage referees.")


def require_team_manager(actor, team) -> None:
    """Raise ``NotAllowed`` unless ``actor`` manages ``team`` (a management position on it, or the settings permission)."""
    from .errors import NotAllowed

    if not can_manage_team(actor, team):
        raise NotAllowed("You are not allowed to manage this team.")


PUBLISH_NEWS = "news.publish_newsitems"


def can_publish_news(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``news.publish_newsitems``: sees and edits every news item,
    publishes, unpublishes and approves."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(PUBLISH_NEWS) or _is_club_owner(user)


def can_add_news(user) -> bool:
    """Publishers, and anyone who may use the management app and has a member record in this club (they may write a draft)."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return can_publish_news(user) or (can_access_management(user) and Member.objects.filter(user=user).exists())


def can_edit_news(user, item) -> bool:
    """Publishers edit (and delete) every item. Anyone else only their own, and only while it is a draft or pending review."""
    if can_publish_news(user):
        return True
    if user is None or not user.is_authenticated or not user.is_active or item.created_by_id is None:
        return False
    from news.models import News

    return item.status != News.Status.PUBLISHED and item.created_by.user_id == user.pk


def can_view_news(user, item) -> bool:
    """Publishers see everything; everyone else sees live published items and their own items in any state."""
    if can_publish_news(user):
        return True
    if item.status == item.Status.PUBLISHED and item.published_at is not None and item.published_at <= timezone.now():
        return True
    return user is not None and user.is_authenticated and user.is_active and item.created_by_id is not None and item.created_by.user_id == user.pk


def require_publish_news(actor) -> None:
    from .errors import NotAllowed

    if not can_publish_news(actor):
        raise NotAllowed("You are not allowed to publish news.")


def require_add_news(actor) -> None:
    from .errors import NotAllowed

    if not can_add_news(actor):
        raise NotAllowed("You are not allowed to write news.")


def require_edit_news(actor, item) -> None:
    from .errors import NotAllowed

    if not can_edit_news(actor, item):
        raise NotAllowed("You are not allowed to change this news item.")


MANAGE_FORMS = "forms.manage_forms"


def can_manage_forms(user) -> bool:
    """Platform owner, the club's Owner role, or a user holding ``forms.manage_forms``: builds forms, sends them, reads the
    responses."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or user.has_perm(MANAGE_FORMS) or _is_club_owner(user)


def require_manage_forms(actor) -> None:
    from .errors import NotAllowed

    if not can_manage_forms(actor):
        raise NotAllowed("You are not allowed to manage forms.")


SHOP_SUPERSET = "shop.manage_shop"
MANAGE_SHOP_PRODUCTS = "shop.manage_products"
MANAGE_SHOP_INVENTORY = "shop.manage_inventory"
MANAGE_SHOP_ORDERS = "shop.manage_orders"
MANAGE_SHOP_DISCOUNTS = "shop.manage_discounts"
MANAGE_SHOP_INVOICES = "shop.manage_invoices"


def _has_shop_perm(user, *codes) -> bool:
    """Platform owner, the club's Owner role, a holder of ``shop.manage_shop`` (superset) or of one of ``codes``.
    The club-has-the-shop-feature check is done by the callers (routers, /me)."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return is_platform_owner(user) or _is_club_owner(user) or any(user.has_perm(c) for c in (SHOP_SUPERSET, *codes))


def can_manage_shop(user) -> bool:
    """The whole shop: only ``shop.manage_shop`` (the superset), the club's Owner role or the platform owner, not the single-area
    permissions. For the things that must not be left to one area (the numbering of invoices)."""
    return _has_shop_perm(user)


def can_manage_shop_products(user) -> bool:
    return _has_shop_perm(user, MANAGE_SHOP_PRODUCTS)


def can_manage_shop_inventory(user) -> bool:
    return _has_shop_perm(user, MANAGE_SHOP_INVENTORY)


def can_manage_shop_orders(user) -> bool:
    return _has_shop_perm(user, MANAGE_SHOP_ORDERS)


def can_manage_shop_discounts(user) -> bool:
    """Discounts and vouchers."""
    return _has_shop_perm(user, MANAGE_SHOP_DISCOUNTS)


def can_manage_shop_invoices(user) -> bool:
    return _has_shop_perm(user, MANAGE_SHOP_INVOICES)


def can_view_shop_catalog(user) -> bool:
    """Products, categories and variants may be read by whoever manages products, inventory or orders."""
    return _has_shop_perm(user, MANAGE_SHOP_PRODUCTS, MANAGE_SHOP_INVENTORY, MANAGE_SHOP_ORDERS)


def can_manage_shop_any(user) -> bool:
    return _has_shop_perm(user, MANAGE_SHOP_PRODUCTS, MANAGE_SHOP_INVENTORY, MANAGE_SHOP_ORDERS, MANAGE_SHOP_DISCOUNTS, MANAGE_SHOP_INVOICES)


def _require(check, message):
    def require(actor) -> None:
        from .errors import NotAllowed

        if not check(actor):
            raise NotAllowed(message)

    return require


require_manage_shop = _require(can_manage_shop, "Only someone who manages the whole shop may do this.")
require_manage_shop_products = _require(can_manage_shop_products, "You are not allowed to manage shop products.")
require_manage_shop_inventory = _require(can_manage_shop_inventory, "You are not allowed to manage shop inventory.")
require_manage_shop_orders = _require(can_manage_shop_orders, "You are not allowed to manage shop orders.")
require_manage_shop_discounts = _require(can_manage_shop_discounts, "You are not allowed to manage shop discounts and vouchers.")
require_manage_shop_invoices = _require(can_manage_shop_invoices, "You are not allowed to manage shop invoices.")
require_view_shop_catalog = _require(can_view_shop_catalog, "You are not allowed to view the shop catalog.")
require_manage_shop_any = _require(can_manage_shop_any, "You are not allowed to manage the shop.")
