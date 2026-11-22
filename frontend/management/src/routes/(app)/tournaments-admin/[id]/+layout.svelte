<script lang="ts">
	import { ArrowLeft } from '@lucide/svelte';
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { t } from '#lib/i18n.svelte.ts';
	import { TBASE, tournamentHeader } from '#lib/tournaments.svelte.ts';

	let { data, children } = $props();
	const tn = $derived(data.tournament);
	const base = $derived(`${TBASE}/${tn.id}`);
	const tabs = $derived(
		[
			{ key: '', label: t('tn.tab.settings'), manage: true },
			{ key: '/registrations', label: t('tn.tab.registrations'), manage: true, count: tn.pending_registration_count, warn: true },
			{ key: '/teams', label: t('tn.tab.teams'), count: tn.teams_in_count },
			{ key: '/setup', label: t('tn.tab.setup') },
			{ key: '/schedule', label: t('tn.tab.schedule') },
			{ key: '/scores', label: t('tn.tab.scores') },
			{ key: '/standings', label: t('tn.tab.standings') },
			{ key: '/tasks', label: t('tn.tab.tasks') },
			{ key: '/rooms', label: t('tn.tab.rooms') },
			{ key: '/rules', label: t('tn.tab.rules'), manage: true },
			{ key: '/sponsors', label: t('tn.tab.sponsors'), manage: true }
		].filter((x) => !x.manage || tn.can_manage)
	);
	$effect(() => {
		try {
			tournamentHeader.status = data.tournament.status;
		} catch {
			// The props are already gone while a navigation tears this layout down; the next page sets its own.
		}
	});
	onDestroy(() => (tournamentHeader.status = ''));
	const isActive = (key: string) => (key === '' ? page.url.pathname === base : page.url.pathname.startsWith(base + key) || (key === '/scores' && page.url.pathname.startsWith(`${base}/matches/`)));
</script>

<nav class="card mb-5 flex items-center gap-3 px-3" aria-label={t('tn.tabs_label')}>
	<div class="flex min-w-0 flex-1 overflow-x-auto">
		<div class="flex min-w-max">
		{#each tabs as tab (tab.key)}
			<a
				href="{base}{tab.key}"
				aria-current={isActive(tab.key) ? 'page' : undefined}
				class="-mb-px flex items-center gap-2 border-b-2 px-4 py-3.5 font-display text-sm font-bold tracking-[.08em] whitespace-nowrap uppercase focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-club {isActive(tab.key) ? 'border-club text-ink' : 'border-transparent text-muted hover:text-ink'}">
				{tab.label}
				{#if tab.count !== undefined && (tab.count > 0 || !tab.warn)}<span class="badge badge-sm {tab.warn ? 'badge-warning' : 'badge-ghost text-ink'} min-w-5 font-mono text-xs font-semibold">{tab.count}</span>{/if}
			</a>
		{/each}
		</div>
	</div>
	<a class="btn btn-outline shrink-0 gap-2" href={TBASE}><ArrowLeft size={16} aria-hidden="true" /> {t('tn.back_to_list')}</a>
</nav>

{@render children()}
