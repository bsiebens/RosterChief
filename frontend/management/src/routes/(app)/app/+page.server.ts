import { redirect } from '@sveltejs/kit';
import { APP_COOKIE } from '#lib/landing.ts';
import type { PageServerLoad } from './$types';

const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export const load: PageServerLoad = async ({ parent, url, cookies }) => {
	const { me } = await parent();
	const coach = me && !me.has_member_portal_role;
	const event = url.searchParams.get('event');
	const article = url.searchParams.get('article');
	if (event && uuid.test(event)) redirect(303, coach ? `/calendar/events/${event}?view=coach` : `/member?tab=calendar&event=${event}`);
	if (article && uuid.test(article)) redirect(303, `/${coach ? 'coach' : 'member'}?tab=news&article=${article}`);
	// The bare app link (home screen icon): staff open the coach view; someone with both sides opens the app they used last.
	const lastApp = cookies.get(APP_COOKIE);
	redirect(303, coach || (me?.has_member_portal_role && me.member_id && lastApp !== 'member') ? '/coach' : '/member');
};
