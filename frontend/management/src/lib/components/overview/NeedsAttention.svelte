<script lang="ts">
	import { TriangleAlert, ArrowRight, CircleCheck } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Overview } from '#lib/overview.ts';

	let { overview, noTypeCount = null, shopClosed = false, shopHref = '/shop', registrationClosed = false }: { overview: Overview; noTypeCount?: number | null; shopClosed?: boolean; shopHref?: string; registrationClosed?: boolean } = $props();
	const k = $derived(overview.kpis!);
	const plural = (n: number, key: string) => t(n === 1 ? `overview.attention.${key}_one` : `overview.attention.${key}`, { n });
	const items = $derived(
		[
			// The shop being closed for members is the most visible issue: red, first.
			...(shopClosed ? [{ n: 1, key: 'shop_closed', title: t('overview.attention.shop_closed'), sub: t('overview.attention.shop_closed_sub'), href: shopHref, action: t('overview.attention.shop_action'), tint: 'bg-danger' }] : []),
			...(registrationClosed ? [{ n: 1, key: 'registration_closed', title: t('overview.attention.registration_closed'), sub: t('overview.attention.registration_closed_sub'), href: '/settings/registration', action: t('overview.attention.shop_action'), tint: 'bg-danger' }] : []),
			{ n: k.teams_without_coach, key: 'no_coach', href: '/teams?coach=none', action: t('overview.attention.assign'), tint: 'bg-warn' },
			{ n: k.awaiting_approval, key: 'awaiting', href: '/members?kind=member&status=pending', action: t('overview.attention.review'), tint: 'bg-warn' },
			{ n: k.documents_to_review ?? 0, key: 'docs', href: '/members?kind=all&documents=pending', action: t('overview.attention.review'), tint: 'bg-warn' },
			{ n: k.unrostered_members, key: 'unrostered', href: '/members?kind=member&team=none', action: t('overview.attention.review'), tint: 'bg-warn' },
			{ n: noTypeCount ?? 0, key: 'no_type', href: '/members?kind=none', action: t('overview.attention.review'), tint: 'bg-warn' }
		]
			.filter((i) => i.n > 0)
			.map((i) => ({ ...i, title: 'title' in i ? i.title : plural(i.n, i.key), sub: 'sub' in i ? i.sub : t(`overview.attention.${i.key}_sub`) }))
	);
</script>

<div class="card h-full">
	<header class="card-body">
		<h2 class="card-title min-h-7"><TriangleAlert size={16} class="shrink-0" aria-hidden="true" /> {t('overview.attention.title')}</h2>
	</header>
	{#if !items.length}
		<p class="flex items-center gap-2 px-6 py-5 text-sm text-muted"><CircleCheck size={16} aria-hidden="true" /> {t('overview.attention.none')}</p>
	{:else}
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<!-- From a fourth issue on the list scrolls instead of growing (three rows are about 13.5rem). -->
		<ul class="divide-y divide-line px-6 {items.length > 3 ? 'max-h-[13.5rem] overflow-y-auto' : ''}" tabindex={items.length > 3 ? 0 : undefined} aria-label={items.length > 3 ? t('overview.attention.title') : undefined}>
			{#each items as i (i.key)}
				<li>
					<a class="flex items-center gap-4 py-4 hover:bg-surface" href={i.href}>
						<span class="h-10 w-1 shrink-0 rounded-full {i.tint}"></span>
						<span class="min-w-0 flex-1">
							<span class="block font-semibold {i.key === 'shop_closed' || i.key === 'registration_closed' ? 'text-danger' : 'text-ink'}">{i.title}</span>
							<span class="block text-sm text-muted">{i.sub}</span>
						</span>
						<span class="btn btn-outline btn-sm gap-1">{i.action} <ArrowRight size={14} aria-hidden="true" /></span>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</div>
