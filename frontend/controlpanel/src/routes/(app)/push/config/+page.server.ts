import { platform } from '#lib/api.ts';
import type { PushConfig } from '#lib/push.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, depends }) => {
	depends('app:push');
	const r = await platform<PushConfig>('GET', '/push/config', undefined, fetch);
	return { config: r.data ?? null, error: r.ok ? '' : (r.message ?? 'The configuration could not be loaded.') };
};
