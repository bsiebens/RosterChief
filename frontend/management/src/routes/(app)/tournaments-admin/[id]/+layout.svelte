<script lang="ts">
	import { page } from '$app/state';
	import { t } from '#lib/i18n.svelte.ts';
	import { fmtDateRange, statusBadge, TBASE } from '#lib/tournaments.svelte.ts';

	let { data, children } = $props();
	const tn = $derived(data.tournament);
	const base = $derived(`${TBASE}/${tn.id}`);
	const tabs = $derived(
		[
			{ key: '', label: t('tn.tab.settings'), manage: true },
			{ key: '/registrations', label: t('tn.tab.registrations'), manage: true, count: tn.pending_registration_count },
			{ key: '/teams', label: t('tn.tab.teams') },
			{ key: '/setup', label: t('tn.tab.setup') },
			{ key: '/schedule', label: t('tn.tab.schedule') },
			{ key: '/scores', label: t('tn.tab.scores') },
			{ key: '/standings', label: t('tn.tab.standings') },
			{ key: '/tasks', label: t('tn.tab.tasks') },
			{ key: '/rooms', label: t('tn.tab.rooms') }
		].filter((x) => !x.manage || tn.can_manage)
	);
	const isActive = (key: string) => (key === '' ? page.url.pathname === base : page.url.pathname.startsWith(base + key));
</script>

<div class="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1">
	<a class="link text-sm text-muted" href={TBASE}>{t('tn.title')}</a>
	<span class="text-muted">/</span>
	<strong class="font-display text-ink">{tn.name}</strong>
	<span class="badge badge-sm {statusBadge(tn.status)}">{t(`tn.status.${tn.status}`)}</span>
	<span class="font-mono text-xs text-muted">{fmtDateRange(tn.start_date, tn.end_date)}</span>
</div>

<nav class="card mb-5 overflow-x-auto px-3" aria-label={t('tn.tabs_label')}>
	<div class="flex min-w-max">
		{#each tabs as tab (tab.key)}
			<a
				href="{base}{tab.key}"
				aria-current={isActive(tab.key) ? 'page' : undefined}
				class="-mb-px flex items-center gap-2 border-b-2 px-4 py-3.5 font-display text-sm font-bold tracking-[.08em] whitespace-nowrap uppercase focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-club {isActive(tab.key) ? 'border-club text-ink' : 'border-transparent text-muted hover:text-ink'}">
				{tab.label}
				{#if tab.count}<span class="badge badge-warning badge-xs">{tab.count}</span>{/if}
			</a>
		{/each}
	</div>
</nav>

{@render children()}
