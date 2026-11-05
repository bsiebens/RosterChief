import type { Me } from '#lib/types.ts';

/** What the signed-in user may do. The API enforces all of it; this only decides what to show. */
type Who = Pick<Me, 'can_manage_members' | 'can_manage_settings' | 'can_execute_evaluations' | 'managed_team_ids'> | null | undefined;

export const canManageMembers = (me: Who) => !!me?.can_manage_members;
export const canManageSettings = (me: Who) => !!me?.can_manage_settings;
/** Edit a team, its roster and staff: a management position on that team, or the settings permission. */
export const canManageTeam = (me: Who, teamId: string | undefined) => !!me && (me.can_manage_settings || (!!teamId && (me.managed_team_ids ?? []).includes(teamId)));
/** Reserve a jersey number: the settings permission or a management position on any team. */
export const canReserveNumbers = (me: Who) => !!me && (me.can_manage_settings || (me.managed_team_ids ?? []).length > 0);
/** Sees the Evaluations tab on a member (and, later, gives evaluations). */
export const canExecuteEvaluations = (me: Who) => !!me?.can_execute_evaluations;
/** Add and change locations and opponents: the settings permission, or a management position on any team. */
export const canManageCalendar = (me: Who) => canReserveNumbers(me);
