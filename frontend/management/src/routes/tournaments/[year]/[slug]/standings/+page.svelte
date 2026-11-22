<script lang="ts">
	import { Swords } from '@lucide/svelte';
	import BracketView from '#lib/components/tournaments/BracketView.svelte';
	import GroupTable from '#lib/components/tournaments/public/GroupTable.svelte';
	import Podium from '#lib/components/tournaments/public/Podium.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.standings.tournament);
	const s = $derived(data.standings);
	const empty = $derived(!s.final_standings.length && !s.standings.some((x) => x.tables.some((tb) => tb.rows.length)) && !s.brackets.some((b) => b.rounds.length));
</script>

<svelte:head><title>{pageTitle(`${tn.name} · ${t('tp.nav.standings')}`)}</title></svelte:head>

{#if empty}<div class="rounded-2xl border border-line bg-white py-12 text-center text-muted" role="status">{t('tp.standings.empty')}</div>{/if}

{#if s.final_standings.length}<Podium rows={s.final_standings} path={tn.path} />{/if}

{#each s.standings as st (st.stage.id)}
	{@const tables = st.tables.filter((tb) => tb.rows.length)}
	{#if tables.length}
		<section class="grid gap-3">
			<h2 class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{st.stage.name}</h2>
			<div class="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
				{#each tables as tb (tb.group?.id ?? 'all')}<GroupTable title={tb.group?.name ?? st.stage.name} table={tb} path={tn.path} />{/each}
			</div>
		</section>
	{/if}
{/each}

{#each s.brackets as b (b.stage.id)}
	{#if b.rounds.length}
		<section class="grid gap-3 rounded-2xl border border-line bg-white p-4 shadow-sm sm:p-5">
			<h2 class="flex items-center gap-2 font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase"><Swords size={18} aria-hidden="true" /> {b.stage.name}</h2>
			<BracketView rounds={b.rounds as never} link={(id) => `/tournaments/${tn.path}/games/${id}`} />
		</section>
	{/if}
{/each}
