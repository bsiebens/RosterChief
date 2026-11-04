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
};

/** Sample numbers until attendance tracking and the shop exist. */
export const mockAttendance = { rate: 86, sessions: 42 };
export const mockShop = { orders: 18, revenue: '1240.50', outstanding: '185.00', unpaid: 3 };

/** Sample news and fixtures until the news and calendar modules exist. Dates are relative to today so the page always looks current. */
const daysFromNow = (n: number, hours = 0, minutes = 0) => {
	const d = new Date();
	d.setHours(hours, minutes, 0, 0);
	d.setDate(d.getDate() + n);
	return d.toISOString();
};
export type NewsItem = { id: string; title: string; date: string };
export type UpcomingItem = { id: string; title: string; start: string; team: string };
export const mockNews = (): NewsItem[] => [
	{ id: 'n1', title: 'New shirts for the Sharks', date: daysFromNow(-3) },
	{ id: 'n2', title: 'Sharks score at the fan day of the local derby', date: daysFromNow(-15) },
	{ id: 'n3', title: 'U15 opens the season away', date: daysFromNow(-42) },
	{ id: 'n4', title: 'U13 opens the season with a fifth place', date: daysFromNow(-49) },
	{ id: 'n5', title: 'Club dinner: save the date', date: daysFromNow(-52) }
];
export const mockUpcoming = (): UpcomingItem[] => [
	{ id: 'u1', title: 'Rivals vs Sharks', start: daysFromNow(1, 21, 15), team: 'Sharks' },
	{ id: 'u2', title: 'Development game', start: daysFromNow(2, 9, 30), team: 'U12' },
	{ id: 'u3', title: 'U18 vs Bulldogs', start: daysFromNow(2, 16, 15), team: 'U18' },
	{ id: 'u4', title: 'Knights vs Sharks', start: daysFromNow(2, 19, 30), team: 'Sharks' },
	{ id: 'u5', title: 'Div 3 vs Knights', start: daysFromNow(3, 11, 55), team: 'Div 3' },
	{ id: 'u6', title: 'U14 vs Bulldogs', start: daysFromNow(3, 12, 0), team: 'U14' },
	{ id: 'u7', title: 'Development game', start: daysFromNow(3, 13, 0), team: 'U12' },
	{ id: 'u8', title: 'Women vs Lions', start: daysFromNow(3, 16, 45), team: 'Women' }
];
