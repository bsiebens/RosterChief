<script lang="ts">
	import Standings from '#lib/components/tournaments/Standings.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const s = $derived(data.standings);
</script>

<svelte:head><title>{pageTitle(`${s.tournament.name} · ${t('tp.nav.standings')}`)}</title></svelte:head>

<Standings standings={s.standings} brackets={s.brackets} slug={s.tournament.slug} />
