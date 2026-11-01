/** The command bar's tabs. One place, so a new section is a new entry here and nowhere else. Only sections that exist belong in here. */
export const NAV: { key: string; label: string; href: string; superuser?: boolean }[] = [
	{ key: 'dashboard', label: 'platform', href: '/' },
	{ key: 'jobs', label: 'jobs', href: '/jobs' }
];
