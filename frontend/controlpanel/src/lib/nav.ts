/** The command bar's tabs. One place, so a new section is a new entry here and nowhere else. Only sections that exist belong in here. */
export const NAV: { key: string; label: string; href: string; superuser?: boolean }[] = [
	{ key: 'dashboard', label: 'platform', href: '/' },
	{ key: 'clubs', label: 'clubs', href: '/clubs' },
	{ key: 'features', label: 'features', href: '/features' },
	{ key: 'sports', label: 'sports', href: '/sports' },
	{ key: 'billing', label: 'billing', href: '/billing' },
	{ key: 'admins', label: 'admins', href: '/admins', superuser: true },
	{ key: 'users', label: 'users', href: '/users', superuser: true },
	{ key: 'jobs', label: 'jobs', href: '/jobs' }
];
