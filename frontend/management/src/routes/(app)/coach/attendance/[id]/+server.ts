import { redirect } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Old deep links (Take attendance buttons, saved links): attendance now lives in the event page's Attendance tab.
export const GET: RequestHandler = ({ params }) => redirect(307, `/calendar/events/${params.id}?tab=attendance&view=coach`);
