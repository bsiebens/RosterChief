<script lang="ts">
	import { AlertTriangle, CalendarDays, FileText, Minus, Plus, UserPlus, X } from '@lucide/svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { api } from '#lib/management-api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { eur, fmtDay, fmtRange, fmtTime, gameLabel, REF_KINDS, ROW_GRID, type Game, type RefKind, type Signup, type TypeRef } from '#lib/referees.ts';
	import AssignmentRow from './AssignmentRow.svelte';

	/** The full manager of one game: assign, capacity, fee lines, sign-ups and the payment form. Loads the detail (conflicts, who can be assigned). */
	let {
		open = $bindable(false),
		game: tile,
		types,
		version,
		onchanged,
		onassign,
		onform
	}: { open: boolean; game: Game | null; types: Record<RefKind, TypeRef[]>; version: number; onchanged: () => void; onassign: (game: Game, kind: RefKind, memberId?: string, signupOnly?: boolean) => void; onform: (game: Game, kind: RefKind) => void } = $props();

	let detail = $state<Game | null>(null);
	let busy = $state(false);
	let error = $state('');

	async function reload() {
		if (!tile) return;
		const id = tile.id;
		const res = await api<Game>('GET', `/referee-management/games/${id}`);
		if (res.ok && tile?.id === id) detail = res.data ?? null;
	}
	$effect(() => {
		void version;
		if (open && tile) reload();
		else detail = null;
	});
	// The loaded detail wins; until it arrives (or when it is another game's) show the tile's own data.
	const game = $derived(detail && detail.id === tile?.id ? detail : tile);

	const rows = (g: Game, k: RefKind) => (k === 'referee' ? g.referees : g.officials);
	const max = (g: Game, k: RefKind) => (k === 'referee' ? g.max_referees : g.max_officials);
	const needs = (g: Game, k: RefKind) => (k === 'referee' ? g.needs_referees : g.needs_officials);
	const short = (g: Game, k: RefKind) => (k === 'referee' ? g.referee_short : g.official_short);

	async function capacity(g: Game, kind: RefKind, delta: number) {
		busy = true;
		await api('PATCH', `/referee-management/games/${g.id}/capacity`, { body: { [kind === 'referee' ? 'max_referees' : 'max_officials']: max(g, kind) + delta } });
		busy = false;
		onchanged();
	}
	async function assignSignup(g: Game, s: Signup) {
		if (s.conflicts.length) return onassign(g, s.kind, s.member_id);
		busy = true;
		error = '';
		const res = await api('POST', `/referee-management/games/${g.id}/assignments`, { body: { kind: s.kind, member_id: s.member_id } });
		busy = false;
		if (res.ok) onchanged();
		else error = Object.values(res.fields)[0] || res.message || t('referees.assign.failed');
	}
	async function dropSignup(s: Signup) {
		busy = true;
		await api('DELETE', `/referee-management/signups/${s.kind}/${s.id}`);
		busy = false;
		onchanged();
	}
</script>

<Modal bind:open wide quiet title={game ? gameLabel(game) : ''}>
	{#if game}
		{@const g = game}
		{@const sections = REF_KINDS.filter((k) => needs(g, k) || rows(g, k).length)}
		<div class="grid gap-5">
			<p class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
				<span class="font-mono">{fmtDay(g.start)} {fmtTime(g.start)}</span>
				{#if g.location_name}<span>{g.location_name}</span>{/if}
				{#if g.fees_pending}<span class="badge badge-sm badge-info">{t('referees.status.fees')}</span>{/if}
				<a class="btn btn-outline btn-sm ml-auto gap-2" href="/calendar/events/{g.id}"><CalendarDays size={14} aria-hidden="true" /> {t('referees.open_event')}</a>
			</p>
			{#if error}<p class="text-sm text-error" role="alert">{error}</p>{/if}

			{#each sections as kind (kind)}
				<section aria-label={t(`referees.kind.${kind}`)} class="card overflow-hidden">
					<div class="flex flex-wrap items-center gap-2 border-b border-line bg-subhead px-4 py-2.5">
						<h3 class="font-display text-sm font-bold tracking-[.1em] text-ink uppercase">{t(`referees.kind.${kind}`)}</h3>
						<span class="badge badge-sm {short(g, kind) ? 'badge-warning' : 'badge-success'}">{t('referees.slots', { n: rows(g, kind).length, max: max(g, kind) })}</span>
						<div class="flex-1"></div>
						{#if needs(g, kind)}
							<span class="flex items-center" title={t('referees.slots_capacity')}>
								<button class="btn btn-ghost btn-square btn-xs" type="button" disabled={busy || max(g, kind) <= rows(g, kind).length} aria-label={t('referees.capacity_less')} onclick={() => capacity(g, kind, -1)}><Minus size={12} aria-hidden="true" /></button>
								<button class="btn btn-ghost btn-square btn-xs" type="button" disabled={busy || max(g, kind) >= 20} aria-label={t('referees.capacity_more')} onclick={() => capacity(g, kind, 1)}><Plus size={12} aria-hidden="true" /></button>
							</span>
							<button class="btn btn-primary btn-xs" type="button" disabled={rows(g, kind).length >= max(g, kind)} onclick={() => onassign(g, kind)}><UserPlus size={12} aria-hidden="true" /> {t('referees.assign')}</button>
						{/if}
						<button class="btn btn-outline btn-xs" type="button" onclick={() => onform(g, kind)}><FileText size={12} aria-hidden="true" /> {t('referees.form')}</button>
					</div>
					{#if rows(g, kind).length}
						<div class="hidden gap-x-2 border-b border-rule px-4 py-1.5 font-display text-[10px] font-bold tracking-[.1em] text-muted uppercase lg:grid {ROW_GRID}" aria-hidden="true">
							<span>{t('referees.col.person')}</span><span>{t('referees.col.type')}</span><span></span><span class="text-right">{t('referees.col.fee')}</span><span class="text-right">{t('referees.col.km')}</span><span class="text-right">{t('referees.col.rate')}</span><span class="text-right">{t('referees.col.total')}</span><span></span>
						</div>
						{#each rows(g, kind) as row (row.id)}<AssignmentRow {row} {kind} types={types[kind] ?? []} onchanged={onchanged} />{/each}
						<p class="border-t border-rule px-4 py-2 text-right font-mono text-xs text-muted">{t('referees.col.total')}: <span class="font-semibold text-ink">{eur(rows(g, kind).reduce((sum, r) => sum + Number(r.total), 0))}</span></p>
					{:else}
						<p class="px-4 py-4 text-sm text-muted">{needs(g, kind) ? t('referees.nobody') : t('referees.not_needed')}</p>
					{/if}
				</section>
			{/each}

			<section aria-label={t('referees.signups.title')} class="card overflow-hidden">
				<div class="flex items-center gap-2 border-b border-line bg-subhead px-4 py-2.5"><h3 class="font-display text-sm font-bold tracking-[.1em] text-ink uppercase">{t('referees.signups.title')}</h3><div class="flex-1"></div>{#each REF_KINDS.filter((k) => needs(g, k)) as k (k)}<button class="btn btn-outline btn-xs" type="button" onclick={() => onassign(g, k, undefined, true)}><UserPlus size={12} aria-hidden="true" /> {t('referees.signups.register', { kind: t(`referees.kind_one.${k}`) })}</button>{/each}</div>
				{#if g.signups.length}
					<ul>
						{#each g.signups as s (s.kind + s.id)}
							<li class="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-rule px-4 py-2.5 last:border-b-0">
								<a class="font-semibold hover:underline" href="/members/{s.member_id}">{s.name}</a>
								<span class="badge badge-xs badge-ghost">{t(`referees.kind_one.${s.kind}`)}</span>
								{#each s.conflicts as c (c.event_id + c.role)}
									<span class="badge badge-xs badge-warning gap-1 normal-case"><AlertTriangle size={10} aria-hidden="true" />{c.title} · {fmtRange(c)}</span>
								{/each}
								<span class="flex-1"></span>
								{#if s.can_assign}
									<button class="btn btn-primary btn-xs" type="button" disabled={busy} onclick={() => assignSignup(g, s)}>{s.conflicts.length ? t('referees.assign_anyway') : t('referees.assign')}</button>
								{:else}
									<span class="text-xs text-muted">{t('referees.signups.cannot')}</span>
								{/if}
								<button class="btn btn-ghost btn-square btn-xs" type="button" disabled={busy} aria-label={t('referees.signups.remove', { name: s.name })} onclick={() => dropSignup(s)}><X size={13} aria-hidden="true" /></button>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="px-4 py-4 text-sm text-muted">{t('referees.signups.none')}</p>
				{/if}
			</section>
		</div>
	{/if}
	<div class="modal-action mt-4"><button type="button" class="btn btn-outline" onclick={() => (open = false)}>{t('common.close')}</button></div>
</Modal>
