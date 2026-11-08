import type { Me } from '#lib/types.ts';

/** What the signed-in user may do. The API enforces all of it; this only decides what to show. */
type Who = Pick<Me, 'can_manage_members' | 'can_manage_settings' | 'can_manage_referees' | 'can_publish_news' | 'can_add_news' | 'can_manage_forms' | 'can_execute_evaluations' | 'managed_team_ids'> | null | undefined;

export const canManageMembers = (me: Who) => !!me?.can_manage_members;
/** Arrange referees and officials for home games and print their payment forms. */
export const canManageReferees = (me: Who) => !!me?.can_manage_referees;
export const canManageSettings = (me: Who) => !!me?.can_manage_settings;
/** Edit a team, its roster and staff: a management position on that team, or the settings permission. */
export const canManageTeam = (me: Who, teamId: string | undefined) => !!me && (me.can_manage_settings || (!!teamId && (me.managed_team_ids ?? []).includes(teamId)));
/** Reserve a jersey number: the settings permission or a management position on any team. */
export const canReserveNumbers = (me: Who) => !!me && (me.can_manage_settings || (me.managed_team_ids ?? []).length > 0);
/** Sees the Evaluations tab on a member (and, later, gives evaluations). */
export const canExecuteEvaluations = (me: Who) => !!me?.can_execute_evaluations;
/** Add and change locations and opponents: the settings permission, or a management position on any team. */
export const canManageCalendar = (me: Who) => canReserveNumbers(me);
/** Add news items (any club member with a login). */
export const canAddNews = (me: Who) => !!me?.can_add_news;
/** Edit all news and publish, unpublish and send back. */
export const canPublishNews = (me: Who) => !!me?.can_publish_news;
/** Build forms, send them to members and read the responses. */
export const canManageForms = (me: Who) => !!me?.can_manage_forms;
