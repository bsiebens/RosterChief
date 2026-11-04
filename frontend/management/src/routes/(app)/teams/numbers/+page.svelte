<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { goto } from '$app/navigation';
	import { enhance } from '$app/forms';
	import { Hash, Lock, LockOpen, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import NumberLegend from '#lib/components/teams/NumberLegend.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { canReserveNumbers } from '#lib/permissions.ts';
	import { STATUS_ORDER, TILE_STYLE } from '#lib/teams/status.ts';

	let { data, form } = $props();
	// The clicked number: opens the detail dialog (who has it, or reserve / release).
	type Overview = (typeof data.overview)[number];
	let openNum = $state<{ poolId: string; number: number } | null>(null);
	let dialogOpen = $state(false);
	let note = $state('');
	let busy = $state(false);
	const current = $derived.by(() => {
		if (!openNum) return null;
		const o = (data.overview ?? []).find((x) => (x.pool?.id ?? '') === openNum!.poolId);
		const n = o?.numbers.find((x) => x.number === openNum!.number);
		return o && n ? { o, n } : null;
	});
	const pick = (o: Overview, number: number) => {
		openNum = { poolId: o.pool?.id ?? '', number };
		note = '';
		dialogOpen = true;
	};
	const act = (name: string) => `?/${name}${data.filters.season ? `&season=${data.filters.season}` : ''}${data.filters.pool ? `&pool=${data.filters.pool}` : ''}`;
	const run = (after?: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') after?.();
		};
	};
	const err = (a: string) => (form?.numAction === a ? (form.numError as string) : undefined);

	const href = (patch: Partial<{ season: string; pool: string }>) => {
		const f = { ...data.filters, ...patch };
		const sp = new URLSearchParams();
		if (f.season) sp.set('season', f.season);
		if (f.pool) sp.set('pool', f.pool);
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.season || !!data.filters.pool);
	const holderText = (h: { first_name: string; last_name: string; team_name: string }) => `${`${h.first_name} ${h.last_name}`.trim()} (${h.team_name})`;

	usePageHeader(() => ({ title: t('teams.numbers.title'), context: t('teams.numbers.context') }));
</script>

<svelte:head><title>{pageTitle(t('teams.numbers.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-7 py-2.5">
		<select class="select w-auto" aria-label={t('teams.season')} value={data.filters.season} onchange={(e) => goto(href({ season: e.currentTarget.value }), { reset: false })}>
			<option value="">{t('members.list.season_current')}</option>
			{#each data.seasons as s (s.id)}<option value={s.id}>{s.name}{s.is_current ? ` (${t('members.list.current')})` : ''}</option>{/each}
		</select>
		<select class="select w-auto" aria-label={t('teams.f.pool')} value={data.filters.pool} onchange={(e) => goto(href({ pool: e.currentTarget.value }), { reset: false })}>
			<option value="">{t('teams.numbers.all_pools')}</option>
			{#each data.pools as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
		</select>
		{#if filtered}<a class="btn btn-outline gap-2" href="?"><X size={14} /> {t('members.list.clear')}</a>{/if}
		<div class="flex-1"></div>
		<NumberLegend statuses={STATUS_ORDER} />
	</div>

	<div class="grid gap-5 px-7 py-5">
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.overview.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Hash size={28} />
				<p>{t('teams.numbers.empty')}</p>
			</div>
		{/if}
		{#each data.overview as o (o.pool?.id ?? 'none')}
									<div class="card card-body">
				<div class="flex flex-wrap items-baseline justify-between gap-2">
					<h2 class="font-display text-sm font-extrabold tracking-[.06em] text-club-dark uppercase">{o.pool?.name ?? t('teams.numbers.no_pool')}</h2>
					{#if o.pool}
						<span class="font-mono text-xs text-muted">{o.pool.min_number}–{o.pool.max_number} · {t('teams.numbers.age_gap', { n: o.pool.min_age_gap_years })}</span>
					{/if}
				</div>
				{#if o.teams.length}<p class="text-xs text-muted">{o.teams.map((x) => x.name).join(', ')}</p>{/if}
				<div class="mt-2 flex flex-wrap gap-1.5">
					{#each o.numbers as n (n.number)}
						{@const tip = n.holders.length ? n.holders.map(holderText).join(', ') : (n.reservation?.note ?? t(`teams.num.${n.status}`))}
						<button
							type="button"
							class="inline-flex h-9 min-w-9 cursor-pointer items-center justify-center rounded border px-1 font-mono text-xs"
							style={TILE_STYLE[n.status]}
							title={tip}
							aria-label="{n.number}: {t(`teams.num.${n.status}`)}"
							onclick={() => pick(o, n.number)}>{n.number}</button>
					{/each}
				</div>
				<p class="mt-2 text-sm text-muted">{t('teams.numbers.hint')}</p>
			</div>
		{/each}
	</div>
</div>

<Modal bind:open={dialogOpen} title={current ? t('teams.numbers.dialog_title', { n: current.n.number }) : ''}>
	{#if current}
		{@const { o, n } = current}
		<div class="grid gap-3">
			<div class="flex flex-wrap items-center gap-2">
				<span class="inline-flex h-9 min-w-9 items-center justify-center rounded border px-2 font-mono text-sm" style={TILE_STYLE[n.status]}>{n.number}</span>
				<span class="badge badge-outline">{t(`teams.num.${n.status}`)}</span>
				<span class="text-sm text-muted">{o.pool?.name ?? t('teams.numbers.no_pool')}</span>
			</div>

			{#if n.holders.length}
				<div>
					<h3 class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('teams.numbers.held_by')}</h3>
					<ul class="mt-2 grid gap-2">
						{#each n.holders as h (h.member_id + h.team_id)}
							<li class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm">
								<a class="link font-semibold" href="/members/{h.member_id}">{`${h.first_name} ${h.last_name}`.trim()}</a>
								<span class="text-muted">·</span>
								<a class="link" href="/teams/{h.team_id}{data.filters.season ? `?season=${data.filters.season}` : ''}">{h.team_name}</a>
								{#if h.date_of_birth}<span class="text-muted">·</span><span class="font-mono text-xs text-muted">{h.date_of_birth}</span>{/if}
							</li>
						{/each}
					</ul>
				</div>
			{/if}

			{#if n.reservation}
				<div>
					<h3 class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('teams.num.reserved')}</h3>
					<p class="text-sm">{n.reservation.note || '—'}</p>
					<p class="text-xs text-muted">{n.reservation.reserved_by_name ? t('teams.numbers.reserved_by', { name: n.reservation.reserved_by_name }) : t('teams.numbers.reserved_by_unknown')}</p>
				</div>
				{#if err('release')}<Alert kind="error">{err('release')}</Alert>{/if}
				{#if n.reservation.can_release === false}
					<p class="text-sm text-muted">{t('teams.numbers.release_locked', { name: n.reservation.reserved_by_name ?? '—' })}</p>
					<div class="modal-action mt-1 flex justify-end"><button class="btn btn-outline" type="button" onclick={() => (dialogOpen = false)}>{t('common.cancel')}</button></div>
				{:else if o.pool}
					<form method="POST" action={act('release')} use:enhance={run(() => (dialogOpen = false))} class="modal-action mt-1 flex justify-end gap-2">
						<input type="hidden" name="pool" value={o.pool.id} />
						<input type="hidden" name="rid" value={n.reservation.id} />
						<button class="btn btn-outline" type="button" onclick={() => (dialogOpen = false)}>{t('common.cancel')}</button>
						<button class="btn btn-outline btn-error gap-2" type="submit" disabled={busy}><LockOpen size={16} /> {t('teams.numbers.release')}</button>
					</form>
				{/if}
			{:else if !n.holders.length}
				<p class="text-sm text-muted">{t('teams.numbers.nobody')}</p>
				{#if o.pool && canReserveNumbers(data.me)}
					<form method="POST" action={act('reserve')} use:enhance={run(() => (dialogOpen = false))} class="grid gap-3">
						<input type="hidden" name="pool" value={o.pool.id} />
						<input type="hidden" name="number" value={n.number} />
						{#if err('reserve')}<Alert kind="error">{err('reserve')}</Alert>{/if}
						<div class="form-control w-full">
							<label class="label" for="res-note"><span class="label-text">{t('teams.numbers.note')}</span></label>
							<input id="res-note" name="note" class="input input-bordered w-full" bind:value={note} placeholder={t('teams.numbers.note_placeholder')} autocomplete="off" />
						</div>
						<div class="modal-action mt-1 flex justify-end gap-2">
							<button class="btn btn-outline" type="button" onclick={() => (dialogOpen = false)}>{t('common.cancel')}</button>
							<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Lock size={16} /> {t('teams.numbers.reserve')}</button>
						</div>
					</form>
				{/if}
			{:else}
				<div class="modal-action mt-1 flex justify-end"><button class="btn btn-outline" type="button" onclick={() => (dialogOpen = false)}>{t('common.cancel')}</button></div>
			{/if}
		</div>
	{/if}
</Modal>
