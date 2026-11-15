import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent }) => {
	const { auth } = await parent();
	if (auth.status !== 'authenticated' || auth.me.level !== 'superuser') error(403, 'Only platform superusers can restore club backups.');
};
