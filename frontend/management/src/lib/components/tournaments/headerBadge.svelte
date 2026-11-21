<script lang="ts" module>
	import { page } from '$app/state';
	import { t } from '#lib/i18n.svelte.ts';
	import { statusBadge, type TournamentDetail } from '#lib/tournaments.svelte.ts';

	/** The tournament's status pill for the page header's `badge` slot; it reads the tournament the `[id]` layout loaded. */
	export { statusPill };
</script>

{#snippet statusPill()}
	{@const tn = (page.data as { tournament?: TournamentDetail }).tournament}
	{#if tn}<span class="badge {statusBadge(tn.status)} self-center">{t(`tn.status.${tn.status}`)}</span>{/if}
{/snippet}
