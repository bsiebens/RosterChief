/**
 * A phone number as people read it: +32 474 93 95 55 (Belgian mobile), +32 15 12 34 56 (Belgian landline), other international numbers with the
 * country code split off. Numbers that are not in international form are shown as they were entered.
 */
const ONE_DIGIT_AREAS = new Set(['2', '3', '4', '9']);

export function formatPhone(value: string | null | undefined): string {
	const raw = (value ?? '').trim();
	if (!raw) return '';
	const digits = raw.replace(/[^\d+]/g, '').replace(/^00/, '+');
	if (!/^\+\d{8,15}$/.test(digits)) return raw;
	if (digits.startsWith('+32')) {
		const n = digits.slice(3);
		if (n.length === 9 && n.startsWith('4')) return `+32 ${n.slice(0, 3)} ${n.slice(3, 5)} ${n.slice(5, 7)} ${n.slice(7)}`;
		if (n.length === 8 && ONE_DIGIT_AREAS.has(n[0])) return `+32 ${n[0]} ${n.slice(1, 4)} ${n.slice(4, 6)} ${n.slice(6)}`;
		if (n.length === 8) return `+32 ${n.slice(0, 2)} ${n.slice(2, 4)} ${n.slice(4, 6)} ${n.slice(6)}`;
	}
	// Other countries: the country code (1 to 3 digits, the common ones known) and the rest in groups of three, then the remainder.
	const country = /^\+(1|7|20|27|3[0-46]|4[0-13-9]|5[1-8]|6[0-6]|8[1246]|9[0-58]|\d{3})/.exec(digits)?.[0] ?? digits.slice(0, 3);
	const rest = digits.slice(country.length).match(/.{1,3}/g) ?? [];
	return [country, ...rest].join(' ');
}
