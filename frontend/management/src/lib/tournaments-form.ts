import { numOrNull, strOrNull, type TDay, type TournamentDetail } from './tournaments.svelte.ts';

/** The tournament form as strings (inputs bind to strings); `payload` turns it into the API body. */
export type TournamentForm = {
	name: string;
	status: string;
	days: TDay[];
	registration_open: boolean;
	max_teams: string;
	earliest_birth_year: string;
	latest_birth_year: string;
	contact_email: string;
	description: string;
	home_team_id: string;
	deposit_amount: string;
	entry_fee_amount: string;
	lunch_mode: string;
	lunch_price_per_day: string;
	lunch_billed_with_fee: boolean;
	default_match_duration_minutes: string;
	default_gap_minutes: string;
	clock_direction: 'up' | 'down';
};

const s = (v: unknown) => (v === null || v === undefined ? '' : String(v));

export function formOf(d?: Partial<TournamentDetail>): TournamentForm {
	return {
		name: d?.name ?? '',
		status: d?.status ?? 'draft',
		days: d?.days?.length ? d.days.map((x) => ({ ...x })) : [{ date: '', starts_at: '09:00', ends_at: '17:00' }],
		registration_open: d?.registration_open ?? false,
		max_teams: s(d?.max_teams),
		earliest_birth_year: s(d?.earliest_birth_year),
		latest_birth_year: s(d?.latest_birth_year),
		contact_email: d?.contact_email ?? '',
		description: d?.description ?? '',
		home_team_id: d?.home_team?.id ?? '',
		deposit_amount: s(d?.deposit_amount),
		entry_fee_amount: s(d?.entry_fee_amount),
		lunch_mode: d?.lunch_mode ?? 'none',
		lunch_price_per_day: s(d?.lunch_price_per_day),
		lunch_billed_with_fee: d?.lunch_billed_with_fee ?? false,
		default_match_duration_minutes: s(d?.default_match_duration_minutes),
		default_gap_minutes: d?.default_gap_minutes === undefined ? '5' : String(d.default_gap_minutes),
		clock_direction: d?.clock_direction ?? 'up'
	};
}

export function payload(v: TournamentForm, opts: { homeTeam?: boolean } = { homeTeam: true }) {
	return {
		name: v.name.trim(),
		status: v.status,
		days: v.days.filter((x) => x.date).map((x) => ({ date: x.date, starts_at: x.starts_at, ends_at: x.ends_at })),
		registration_open: v.registration_open,
		max_teams: numOrNull(v.max_teams),
		earliest_birth_year: numOrNull(v.earliest_birth_year),
		latest_birth_year: numOrNull(v.latest_birth_year),
		contact_email: v.contact_email.trim(),
		description: v.description,
		...(opts.homeTeam ? { home_team_id: strOrNull(v.home_team_id) } : {}),
		deposit_amount: strOrNull(v.deposit_amount.trim()),
		entry_fee_amount: strOrNull(v.entry_fee_amount.trim()),
		lunch_mode: v.lunch_mode,
		lunch_price_per_day: strOrNull(v.lunch_price_per_day.trim()),
		lunch_billed_with_fee: v.lunch_billed_with_fee,
		default_match_duration_minutes: numOrNull(v.default_match_duration_minutes),
		default_gap_minutes: numOrNull(v.default_gap_minutes),
		clock_direction: v.clock_direction
	};
}
