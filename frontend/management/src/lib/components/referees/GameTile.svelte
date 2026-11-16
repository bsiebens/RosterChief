<script lang="ts">
	import { AlertTriangle, CalendarDays, MapPin, UserPlus } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { kindIcon } from '#lib/officiating-icons.ts';
	import { fmtDay, fmtTime, gameLabel, REF_KINDS, type Game, type RefKind } from '#lib/referees.ts';

	/** A game at a glance: when, who, where, how full each kind is and what needs attention. The whole tile opens the manager. */
	let { game, onopen }: { game: Game; onopen: (game: Game) => void } = $props();

	const rows = (k: RefKind) => (k === 'referee' ? game.referees : game.officials);
	const max = (k: RefKind) => (k === 'referee' ? game.max_referees : game.max_officials);
	const needs = (k: RefKind) => (k === 'referee' ? game.needs_referees : game.needs_officials);
	const short = (k: RefKind) => (k === 'referee' ? game.referee_short : game.official_short);
	const kinds = $derived(REF_KINDS.filter((k) => needs(k) || rows(k).length));
	const signups = $derived(game.signups.length);
	const open = $derived(game.referee_short || game.official_short);
</script>

<article class="card transition-colors hover:border-club {open ? 'border-l-[3px] border-l-warn' : ''}">
	<button class="flex h-full w-full flex-col gap-2.5 p-3.5 text-left" type="button" onclick={() => onopen(game)} aria-label={t('referees.manage_named', { game: gameLabel(game) })}>
		<span class="flex items-center justify-between gap-2">
			<span class="flex items-center gap-1.5 font-display text-sm font-bold tracking-[.06em] text-ink uppercase"><CalendarDays size={13} class="text-dim" aria-hidden="true" />{fmtDay(game.start)}<span class="font-mono text-xs font-normal tracking-normal text-muted normal-case">{fmtTime(game.start)}</span></span>
			<span class="flex items-center gap-1">
				{#if game.fees_pending}<span class="badge badge-xs badge-info">{t('referees.status.fees')}</span>{/if}
			</span>
		</span>
		<span class="block min-w-0">
			<span class="block truncate font-display text-lg leading-tight font-extrabold tracking-[.03em] text-ink uppercase">{gameLabel(game)}</span>
			{#if game.location_name}<span class="mt-0.5 flex items-center gap-1 truncate text-xs text-muted"><MapPin size={12} class="shrink-0" aria-hidden="true" />{game.location_name}</span>{/if}
		</span>
		<span class="mt-auto flex flex-wrap items-center gap-1.5">
			{#each kinds as k (k)}
				{@const Icon = kindIcon[k]}
				<span class="badge gap-1 {short(k) ? 'badge-warning' : 'badge-success'}" title={t(`referees.kind.${k}`)}><Icon size={11} aria-hidden="true" /><span class="font-mono">{rows(k).length}/{max(k)}</span></span>
			{/each}
			{#if signups}<span class="badge badge-info gap-1" title={t('referees.signups.title')}><UserPlus size={11} aria-hidden="true" />{t('referees.signups.chip', { n: signups })}</span>{/if}
			{#if game.conflict_count}<span class="badge badge-warning gap-1" title={t('referees.conflict.chip_title')}><AlertTriangle size={11} aria-hidden="true" />{game.conflict_count}</span>{/if}
			<span class="ml-auto font-display text-xs font-bold tracking-[.1em] text-club-dark uppercase">{t('referees.manage')} &rsaquo;</span>
		</span>
	</button>
</article>
