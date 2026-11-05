import type { ClubAttendance } from '#lib/attendance.ts';

/** GET /api/v1/overview (see api/schemas_overview.py) plus the placeholder numbers for the parts that have no data yet. */
export type Overview = {
	season: { id: string; name: string } | null;
	previous_season: { id: string; name: string } | null;
	kpis: { total_members: number; previous_total_members: number | null; awaiting_approval: number; teams_without_coach: number; unrostered_members: number; new_members: number; previous_new_members: number | null } | null;
	signups: { month: string; new: number; returning: number }[];
	fees: Record<'paid' | 'partially_paid' | 'unpaid' | 'waived', number> | null;
	renewal: { previous_season: string; renewed: number; of: number; rate: number; previous_rate: number | null } | null;
	cards: {
		people: { admins: number; staff: number; members: number; guardians: number };
		members: { total: number; active: number; pending: number; lapsed: number };
		teams: { teams: number; players: number; staff: number };
		events: { upcoming: number; this_season: number };
	} | null;
	/** Club-wide turnout over the season's past events; null when the season has no past events yet. */
	attendance: ClubAttendance | null;
};

/** Sample numbers until the shop exists. */
export const mockShop = { orders: 18, revenue: '1240.50', outstanding: '185.00', unpaid: 3 };

/** Sample news and fixtures until the news and calendar modules exist. Dates are relative to today so the page always looks current. */
const daysFromNow = (n: number, hours = 0, minutes = 0) => {
	const d = new Date();
	d.setHours(hours, minutes, 0, 0);
	d.setDate(d.getDate() + n);
	return d.toISOString();
};
export type NewsItem = { id: string; title: string; date: string };
export const mockNews = (): NewsItem[] => [
	{ id: 'n1', title: 'New shirts for the Sharks', date: daysFromNow(-3) },
	{ id: 'n2', title: 'Sharks score at the fan day of the local derby', date: daysFromNow(-15) },
	{ id: 'n3', title: 'U15 opens the season away', date: daysFromNow(-42) },
	{ id: 'n4', title: 'U13 opens the season with a fifth place', date: daysFromNow(-49) },
	{ id: 'n5', title: 'Club dinner: save the date', date: daysFromNow(-52) }
];
