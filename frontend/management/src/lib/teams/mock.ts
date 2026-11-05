/** Sample rows for the team page until the fixture import exists. */
export type FixtureSource = { season: string; source: string; competition: string; url: string; imported: string; checked: string; summary: string };
export const sampleFixtureSources = (season: string): FixtureSource[] => [
	{ season, source: 'RBIHF', competition: '—', url: 'https://www.rbihf.be/league/team/4462', imported: '2026-10-19 13:23', checked: '2026-11-02 06:15', summary: '0 rescheduled, 0 new, 0 removed' }
];
