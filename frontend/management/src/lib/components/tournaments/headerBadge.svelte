<script lang="ts" module>
	import { t } from '#lib/i18n.svelte.ts';
	import { statusBadge, tournamentHeader } from '#lib/tournaments.svelte.ts';

	/** The tournament's status pill for the page header's `badge` slot; the `[id]` layout publishes the status in `tournamentHeader`. */
	export { statusPill };
</script>

{#snippet statusPill()}
	{#if tournamentHeader.status}<span class="badge {statusBadge(tournamentHeader.status)} self-center">{t(`tn.status.${tournamentHeader.status}`)}</span>{/if}
{/snippet}
