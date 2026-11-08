<script lang="ts">
	import { AlertTriangle, Search, UserPlus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { api } from '#lib/management-api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { fmtDay, fmtRange, fmtTime, gameLabel, type Candidate, type Game, type RefKind, type TypeRef } from '#lib/referees.ts';

	/** Assign a club member (eligible people, double bookings flagged and sorted below the free ones) or an external person to a
	 *  game. A member with another event at the same time needs an explicit "Assign anyway". Staff can also just register a sign-up. */
	let {
		open = $bindable(false),
		game,
		kind,
		types,
		preselect = '',
		signupOnly = false,
		onassigned
	}: { open: boolean; game: Game | null; kind: RefKind; types: TypeRef[]; preselect?: string; signupOnly?: boolean; onassigned: () => void } = $props();

	let mode = $state<'member' | 'external'>('member');
	let candidates = $state<Candidate[] | null>(null);
	let failed = $state(false);
	let q = $state('');
	let picked = $state('');
	let externalName = $state('');
	let typeId = $state('');
	let asSignup = $state(false);
	let busy = $state(false);
	let error = $state('');

	$effect(() => {
		if (!open || !game) return;
		const id = game.id;
		const k = kind;
		mode = 'member';
		candidates = null;
		failed = false;
		q = '';
		picked = preselect;
		externalName = '';
		typeId = '';
		asSignup = signupOnly;
		error = '';
		load(id, k, '');
	});

	/** The eligible people, plus (with a search) any other member matching it: anyone can be assigned by hand. */
	function load(id: string, k: RefKind, search: string) {
		api<Candidate[]>('GET', `/referee-management/games/${id}/candidates`, { query: { kind: k, q: search } }).then((res) => {
			if (!open || game?.id !== id || search !== q.trim()) return;
			candidates = res.data ?? [];
			failed = !res.ok;
		});
	}
	let timer: ReturnType<typeof setTimeout> | undefined;
	function onSearch() {
		clearTimeout(timer);
		timer = setTimeout(() => game && load(game.id, kind, q.trim()), 250);
	}

	const shown = $derived(candidates ?? []);
	const current = $derived((candidates ?? []).find((c) => c.id === picked) ?? null);
	const conflicted = $derived(mode === 'member' && !asSignup && !!current?.conflicts.length);
	// A sign-up is for people who could be assigned anyway.
	$effect(() => {
		if (current && !current.eligible) asSignup = false;
	});
	const canSubmit = $derived(mode === 'member' ? !!picked : !!externalName.trim());

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!game || !canSubmit) return;
		busy = true;
		error = '';
		let res;
		if (mode === 'member' && asSignup) res = await api('POST', `/referee-management/games/${game.id}/signups`, { body: { kind, member_id: picked } });
		else {
			const body = { kind, referee_type_id: typeId || null, force: conflicted, ...(mode === 'member' ? { member_id: picked } : { external_name: externalName.trim() }) };
			res = await api('POST', `/referee-management/games/${game.id}/assignments`, { body });
		}
		busy = false;
		if (res.ok) {
			open = false;
			onassigned();
		} else error = Object.values(res.fields)[0] || res.message || t('referees.assign.failed');
	}
</script>

<Modal bind:open title={t('referees.assign_title', { kind: t(`referees.kind_one.${kind}`) })}>
	{#snippet aside()}
		<div class="flex w-fit items-center gap-1 rounded-full bg-primary p-1" role="group">
			{#each ['member', 'external'] as m (m)}
				<button type="button" aria-pressed={mode === m} class="rounded-full px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {mode === m ? 'bg-primary-content text-primary' : 'text-primary-dim hover:text-primary-content'}" onclick={() => (mode = m as 'member' | 'external')}>{t(`referees.assign.${m}`)}</button>
			{/each}
		</div>
	{/snippet}
	{#if game}
		<form class="grid gap-4" onsubmit={submit}>
			<p class="text-sm text-muted">{t('referees.assign_for', { game: gameLabel(game), when: `${fmtDay(game.start)} ${fmtTime(game.start)}` })}</p>

			{#if mode === 'member'}
				<div class="grid gap-2">
					<div class="relative">
						<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
						<input type="search" class="input w-full pl-9" aria-label={t('referees.assign.search')} placeholder={t('referees.assign.search')} bind:value={q} oninput={onSearch} />
					</div>
					{#if candidates === null}
						<p class="text-sm text-muted">{t('referees.assign.loading')}</p>
					{:else if failed}
						<Alert kind="error">{t('referees.assign.failed')}</Alert>
					{:else if !candidates.length}
						<Alert kind="info">{q.trim() ? t('referees.assign.no_match') : t('referees.assign.empty')}</Alert>
					{:else}
						<ul class="max-h-72 overflow-y-auto rounded-lg border border-line" role="radiogroup" aria-label={t('referees.assign.member')}>
							{#each shown as c (c.id)}
								<li class="border-b border-rule last:border-b-0">
									<label class="flex cursor-pointer items-start gap-3 px-3 py-2 hover:bg-surface {picked === c.id ? 'bg-row-sel' : ''}">
										<input type="radio" class="mt-1" name="candidate" value={c.id} bind:group={picked} />
										<span class="min-w-0 flex-1">
											<span class="flex flex-wrap items-center gap-2">
												<span class="font-semibold">{c.name}</span><span class="text-xs text-muted">{c.level}</span>
												{#if c.signed_up}<span class="badge badge-xs badge-info gap-1"><UserPlus size={10} aria-hidden="true" />{t('referees.signups.badge')}</span>{/if}
												{#if !c.eligible}<span class="badge badge-xs badge-ghost">{t('referees.assign.not_eligible')}</span>{/if}
												{#if c.expired}<span class="badge badge-xs badge-warning">{t('referees.assign.expired')}</span>{/if}
											</span>
											{#each c.conflicts as x (x.event_id + x.role)}
												<span class="mt-1 flex items-center gap-1 text-xs text-warn-text"><AlertTriangle size={12} class="shrink-0" aria-hidden="true" /><span><span class="font-semibold">{x.title}</span> · {fmtRange(x)}</span></span>
											{/each}
										</span>
									</label>
								</li>
							{/each}
						</ul>
						<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={asSignup} disabled={!!current && !current.eligible} />{t('referees.assign.signup_only')}</label>
					{/if}
				</div>
			{:else}
				<div class="form-control w-full">
					<label class="label" for="external-name"><span class="label-text">{t('referees.assign.external_name')}</span></label>
					<input id="external-name" class="input input-bordered w-full" autocomplete="off" bind:value={externalName} />
					<span class="label-text-alt mt-1 block">{t('referees.assign.external_help')}</span>
				</div>
			{/if}

			{#if types.length && !(mode === 'member' && asSignup)}
				<div class="form-control w-full">
					<label class="label" for="assign-type"><span class="label-text">{t('referees.assign.type')}</span></label>
					<select id="assign-type" class="select select-bordered w-full" bind:value={typeId}>
						<option value="">{t('referees.no_type')}</option>
						{#each types as ty (ty.id)}<option value={ty.id}>{ty.name}</option>{/each}
					</select>
				</div>
			{/if}

			{#if conflicted && current}
				<Alert kind="warning" title={t('referees.conflict.title')}>{t('referees.conflict.body', { name: current.name, titles: current.conflicts.map((c) => c.title).join(', ') })}</Alert>
			{/if}
			{#if error}<Alert kind="error">{error}</Alert>{/if}
			<div class="modal-action mt-1 flex items-center justify-end gap-2">
				{#if mode === 'member' && !picked && candidates?.length}<span class="mr-auto text-xs text-muted">{t('referees.assign.pick')}</span>{/if}
				<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
				<button class="btn {conflicted ? 'btn-warning' : 'btn-primary'}" type="submit" disabled={busy || !canSubmit}>{asSignup && mode === 'member' ? t('referees.signups.register_confirm') : conflicted ? t('referees.assign_anyway') : t('referees.assign.confirm')}</button>
			</div>
		</form>
	{/if}
</Modal>
