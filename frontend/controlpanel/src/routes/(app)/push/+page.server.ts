import { platform, platformList } from '#lib/api.ts';
import type { ClubRef } from '#lib/billing.ts';
import type { PushMonitoring } from '#lib/push.ts';
import type { PageServerLoad } from './$types';

const WINDOWS = ['1h', '24h', '7d'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const load: PageServerLoad = async ({ fetch, url, depends }) => {
	depends('app:push');
	const w = url.searchParams.get('window') ?? '';
	const window = WINDOWS.includes(w) ? w : '24h';
	const c = url.searchParams.get('club') ?? '';
	const club = UUID.test(c) ? c : '';

	const params = new URLSearchParams({ window });
	if (club) params.set('club', club);
	const [monitoring, clubs] = await Promise.all([platform<PushMonitoring>('GET', `/push/monitoring?${params}`, undefined, fetch), platformList<ClubRef>('/clubs?status=all', fetch)]);

	return {
		monitoring: monitoring.data ?? null,
		error: monitoring.ok ? '' : (monitoring.message ?? 'The push monitoring could not be loaded.'),
		clubs: clubs.data ?? [],
		query: { window, club }
	};
};
