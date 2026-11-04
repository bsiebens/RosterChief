/** Sample numbers for the team page until attendance tracking and the fixture import exist. Names come from the real roster. */
export type SampleAttender = { name: string; pct: number; href: string };
type Player = { member_id: string; first_name: string; last_name: string };

const full = (p: Player) => `${p.first_name} ${p.last_name}`.trim();
const pick = (players: Player[], from: number, pcts: number[]): SampleAttender[] =>
	players.slice(from, from + pcts.length).map((p, i) => ({ name: full(p), pct: pcts[i], href: `/members/${p.member_id}` }));

/** Deterministic, so the page does not jump between loads. */
export function sampleAttendance(players: Player[]) {
	return {
		rate: 86,
		sessions: 18,
		top: pick(players, 0, [100, 96, 94]),
		attention: pick(players, Math.max(players.length - 3, 0), [52, 58, 61]),
		missedLastTwo: pick(players, 3, [0, 0]).slice(0, Math.min(2, players.length)),
		noShows: pick(players, Math.max(players.length - 1, 0), [0]).slice(0, Math.min(1, players.length))
	};
}

export type FixtureSource = { season: string; source: string; competition: string; url: string; imported: string; checked: string; summary: string };
export const sampleFixtureSources = (season: string): FixtureSource[] => [
	{ season, source: 'RBIHF', competition: '—', url: 'https://www.rbihf.be/league/team/4462', imported: '2026-10-19 13:23', checked: '2026-11-02 06:15', summary: '0 rescheduled, 0 new, 0 removed' }
];
