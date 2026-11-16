import { api } from '#lib/management-api.ts';
import type { CoachEvent } from '#lib/portal.ts';

export const dateKey = (date: Date) => [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
export const startOfToday = () => {
	const now = new Date();
	return new Date(now.getFullYear(), now.getMonth(), now.getDate());
};

export const isGame = (event: { kind: string }) => event.kind === 'game' || event.kind === 'tournament';
export const isUpcoming = (event: CoachEvent) => !event.cancelled && Date.parse(event.start) >= Date.now();
/** Players who said they come, out of everyone invited, as a percentage for the thin bar. */
export const goingShare = (event: CoachEvent) => (event.invited > 0 ? Math.round((100 * event.going) / event.invited) : 0);

export type AttentionItem = { type: 'replies' | 'sheet' | 'lineup'; event: CoachEvent };

/** What the coach still has to do, each kind capped on its own; `more` says whether something was left out. */
export function attention(events: CoachEvent[], caps = { replies: 2, sheet: 1, lineup: 1 }): { items: AttentionItem[]; more: boolean } {
	const now = Date.now();
	const upcoming = events.filter((event) => isUpcoming(event) && event.can_manage_attendance);
	const replies = upcoming.filter((event) => event.unanswered > 0 && (!event.deadline || Date.parse(event.deadline) >= now));
	const sheets = events.filter((event) => !event.cancelled && event.kind === 'game' && Date.parse(event.start) < now && !event.has_game_sheet && event.can_manage_attendance).reverse();
	const lineups = upcoming.filter((event) => event.lineup && !event.lineup_published);
	const items: AttentionItem[] = [
		...replies.slice(0, caps.replies).map((event) => ({ type: 'replies' as const, event })),
		...lineups.slice(0, caps.lineup).map((event) => ({ type: 'lineup' as const, event })),
		...sheets.slice(0, caps.sheet).map((event) => ({ type: 'sheet' as const, event }))
	];
	return { items, more: replies.length > caps.replies || lineups.length > caps.lineup || sheets.length > caps.sheet };
}

/** Nudge everyone who has not answered; the result says how many were reminded or why not. */
export async function remind(eventId: string): Promise<{ ok: boolean; count: number; message: string }> {
	const response = await api<{ reminded: number }>('POST', `/events/${eventId}/attendance/remind`);
	return { ok: response.ok, count: response.data?.reminded ?? 0, message: response.message ?? '' };
}
