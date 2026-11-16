/** Roles (`/roles`, `/settings/roles`): needs `authorizations.manage_roles`. */
export type Permission = { code: string; label: string; admin: boolean; feature: string | null; feature_name: string | null; available: boolean };
export type RolePerson = { id: string; name: string; email: string };
export type Role = { id: string; name: string; slug: string; description: string; is_system: boolean; is_owner: boolean; permissions: string[]; member_count: number };
export type RoleDetail = Role & { members: RolePerson[] };

export const roleHref = (id: string) => `/settings/roles/${id}`;

/** The permissions grouped by the part before the dot (the area of the app), in catalogue order. */
export function groupPermissions(list: Permission[]): { area: string; items: Permission[] }[] {
	const out: { area: string; items: Permission[] }[] = [];
	for (const p of list) {
		const area = p.code.split('.')[0];
		const g = out.find((x) => x.area === area);
		if (g) g.items.push(p);
		else out.push({ area, items: [p] });
	}
	return out;
}
