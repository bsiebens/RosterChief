/** The fixed positions of an ice hockey player. The value is what is stored; the label key is translated in the message files. */
export const HOCKEY_POSITIONS = [
	{ value: 'Forward', key: 'tn.pos.forward' },
	{ value: 'Defense', key: 'tn.pos.defense' },
	{ value: 'Goalie', key: 'tn.pos.goalie' }
] as const;

const ALIASES: Record<string, string[]> = {
	Forward: ['forward', 'forwards', 'fw', 'f', 'wing', 'winger', 'left wing', 'right wing', 'lw', 'rw', 'center', 'centre', 'c', 'attacker', 'striker'],
	Defense: ['defense', 'defence', 'defenseman', 'defenceman', 'defender', 'd', 'dm'],
	Goalie: ['goalie', 'goalies', 'goalkeeper', 'goaltender', 'keeper', 'g', 'gk']
};

/** The fixed position a free text label stands for, or '' (empty or unknown). Mirrors `tournaments.sports.normalise_position`. */
export function normalisePosition(label: string | null | undefined): string {
	const key = (label ?? '').trim().toLowerCase();
	return Object.entries(ALIASES).find(([, aliases]) => aliases.includes(key))?.[0] ?? '';
}
