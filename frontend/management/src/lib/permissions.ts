import type { Me } from '#lib/types.ts';

/** What the signed-in user may do. The API enforces all of it; this only decides what to show. */
type Who = Pick<Me, 'can_manage_members' | 'can_manage_finance' | 'can_manage_charges' | 'can_manage_settings' | 'can_manage_referees' | 'can_manage_roles' | 'can_publish_news' | 'can_add_news' | 'can_manage_forms' | 'can_manage_shop_products' | 'can_manage_shop_inventory' | 'can_manage_shop_orders' | 'can_manage_shop_discounts' | 'can_manage_shop_invoices' | 'can_execute_evaluations' | 'can_manage_evaluations' | 'managed_team_ids'> | null | undefined;

export const canManageMembers = (me: Who) => !!me?.can_manage_members;
export const canManageFinance = (me: Who) => !!me?.can_manage_finance;
/** Arrange referees and officials for home games and print their payment forms. */
export const canManageReferees = (me: Who) => !!me?.can_manage_referees;
/** Creates roles, sets their permissions and gives them to people. */
export const canManageRoles = (me: Who) => !!me?.can_manage_roles;
export const canManageSettings = (me: Who) => !!me?.can_manage_settings;
/** Edit a team, its roster and staff: a management position on that team, or the settings permission. */
export const canManageTeam = (me: Who, teamId: string | undefined) => !!me && (me.can_manage_settings || (!!teamId && (me.managed_team_ids ?? []).includes(teamId)));
/** Reserve a jersey number: the settings permission or a management position on any team. */
export const canReserveNumbers = (me: Who) => !!me && (me.can_manage_settings || (me.managed_team_ids ?? []).length > 0);
/** Gives evaluations, runs evaluation meetings and sees the Evaluations tab on a member. */
export const canExecuteEvaluations = (me: Who) => !!me?.can_execute_evaluations;
/** Builds the evaluation checklists (the criteria players are scored on). Does not allow reading evaluations. */
export const canManageEvaluations = (me: Who) => !!me?.can_manage_evaluations;
/** Add and change locations and opponents: the settings permission, or a management position on any team. */
export const canManageCalendar = (me: Who) => canReserveNumbers(me);
/** Add news items (any club member with a login). */
export const canAddNews = (me: Who) => !!me?.can_add_news;
/** Edit all news and publish, unpublish and send back. */
export const canPublishNews = (me: Who) => !!me?.can_publish_news;
/** Build forms, send them to members and read the responses. */
export const canManageForms = (me: Who) => !!me?.can_manage_forms;
/** Shop: products and categories, inventory, orders, discounts and vouchers, invoices (each its own permission). */
export const canManageShopProducts = (me: Who) => !!me?.can_manage_shop_products;
export const canManageShopInventory = (me: Who) => !!me?.can_manage_shop_inventory;
export const canManageShopOrders = (me: Who) => !!me?.can_manage_shop_orders;
export const canManageShopDiscounts = (me: Who) => !!me?.can_manage_shop_discounts;
export const canManageCharges = (me: Who) => !!me?.can_manage_charges;
export const canManageShopInvoices = (me: Who) => !!me?.can_manage_shop_invoices;
export const canManageShopAny = (me: Who) => canManageShopProducts(me) || canManageShopInventory(me) || canManageShopOrders(me) || canManageShopDiscounts(me) || canManageShopInvoices(me);
