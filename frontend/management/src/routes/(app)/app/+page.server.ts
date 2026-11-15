import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export const load: PageServerLoad = async ({ parent, url }) => {
	const { me } = await parent();
	const coach = me && !me.has_member_portal_role;
	const event = url.searchParams.get('event');
	const article = url.searchParams.get('article');
	if (event && uuid.test(event)) redirect(303, coach ? `/calendar/events/${event}?view=coach` : `/parent?tab=calendar&event=${event}`);
	if (article && uuid.test(article)) redirect(303, `/${coach ? 'coach' : 'parent'}?tab=news&article=${article}`);
	redirect(303, coach ? '/coach' : '/parent');
};
