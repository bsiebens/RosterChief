<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import type { LineupMember } from '#lib/events/types.ts';

	/** The line under a player in the line-up pickers: the reason when they are out, and their season turnout (hidden without data). */
	let { m, class: klass = '' }: { m: LineupMember; class?: string } = $props();
	const out = $derived(['absent', 'excused', 'dropout'].includes(m.status));
	const parts = $derived(
		[
			out && m.note ? t('coach.lu_out_reason', { reason: m.note }) : '',
			m.games_rate != null ? t('coach.lu_rate_games', { n: m.games_rate }) : '',
			m.practices_rate != null ? t('coach.lu_rate_practices', { n: m.practices_rate }) : ''
		].filter(Boolean)
	);
</script>

{#if parts.length}<span class="block truncate text-xs text-muted {klass}">{parts.join(' · ')}</span>{/if}
