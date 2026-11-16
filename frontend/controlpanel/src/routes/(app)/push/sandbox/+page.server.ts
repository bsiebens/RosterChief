import { platform, platformList } from '#lib/api.ts';
import type { ClubRef } from '#lib/billing.ts';
import type { PushTarget, SandboxMessages, SandboxOverview, UserPush } from '#lib/push.ts';
import type { PageServerLoad } from './$types';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const load: PageServerLoad = async ({ fetch, url, depends }) => {
	depends('app:push');
	const sp = url.searchParams;
	const query = { channel: sp.get('channel') ?? '', provider: sp.get('provider') ?? '', outcome: sp.get('outcome') ?? '', club: UUID.test(sp.get('club') ?? '') ? sp.get('club')! : '' };
	const params = new URLSearchParams();
	for (const [k, v] of Object.entries(query)) if (v) params.set(k, v);

	const [overview, messages, clubs] = await Promise.all([
		platform<SandboxOverview>('GET', '/push/sandbox', undefined, fetch),
		platform<SandboxMessages>('GET', `/push/sandbox/messages?${params}`, undefined, fetch),
		platformList<ClubRef>('/clubs?status=all', fetch)
	]);

	// ?target=device:<uuid> or web_subscription:<uuid> (optionally &user=<account uuid> to show which device it is).
	let target: PushTarget | null = null;
	const [type, id] = (sp.get('target') ?? '').split(':');
	if ((type === 'device' || type === 'web_subscription') && UUID.test(id ?? '')) {
		target = { type, id, label: type === 'device' ? 'Device' : 'Web subscription' };
		const user = sp.get('user') ?? '';
		if (UUID.test(user)) {
			const r = await platform<UserPush>('GET', `/users/${user}/push`, undefined, fetch);
			const d = r.data?.devices.find((x) => x.id === id);
			const w = r.data?.web_subscriptions.find((x) => x.id === id);
			if (d) target.label = `${d.platform_label} · ${d.environment}${d.app_version ? ` · v${d.app_version}` : ''}`;
			else if (w) target.label = `Web subscription · ${w.endpoint_masked}`;
		}
	}

	return {
		overview: overview.data ?? null,
		error: overview.ok ? '' : (overview.message ?? 'The sandbox could not be loaded.'),
		messages: messages.data ?? null,
		messagesError: messages.ok ? '' : (messages.message ?? 'The captured messages could not be loaded.'),
		clubs: clubs.data ?? [],
		query,
		target
	};
};
