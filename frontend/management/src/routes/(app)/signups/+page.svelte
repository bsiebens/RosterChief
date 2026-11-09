<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { Ban, CheckCheck, Check, FileText, Shirt, UserCheck } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { documentsHref, feeBadge, type SignupRow } from '#lib/signups.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; only: string; season: string }>) => {
		const f = { ...data.filters, ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.only !== 'all') sp.set('only', f.only);
		if (f.season) sp.set('season', f.season);
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const navigate = (to: string) => goto(to, { reset: false });
	const filtered = $derived(!!data.filters.q || data.filters.only !== 'all' || !!data.filters.season);
	const tabList = $derived([
		{ key: 'all', label: t('signups.tab.all'), count: data.total },
		{ key: 'clean', label: t('signups.tab.clean'), count: data.clean },
		{ key: 'open', label: t('signups.tab.open'), count: data.total - data.clean, warn: true }
	]);

	let busy = $state(false);
	let target = $state<SignupRow | null>(null);
	let placeOpen = $state(false);
	let cancelOpen = $state(false);
	let allOpen = $state(false);
	const run = (close?: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close?.();
		};
	};

	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const approved = $derived(form && 'approved' in form ? Number(form.approved) : 0);
	const failure = $derived.by(() => {
		if (!form || 'done' in form || !('op' in form)) return '';
		const m = String(form.message ?? '');
		if (m.startsWith('requirements:')) return t('signups.err.requirements', { names: m.slice(13) });
		if (form.fields && 'fee_status' in (form.fields as object)) return t('signups.err.fee');
		return m || t('common.error.generic');
	});

	usePageHeader(() => ({ title: t('signups.title'), context: data.seasonName ?? '', actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" disabled={!data.clean} onclick={() => (allOpen = true)}><CheckCheck size={16} /> {t('signups.approve_all', { n: data.clean })}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('signups.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('signups.search')} searchPlaceholder={t('signups.search_placeholder')} searchClass="w-72" onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => navigate('?')}>
		<select class="select w-auto" aria-label={t('signups.season')} value={data.filters.season} onchange={(e) => navigate(href({ season: e.currentTarget.value }))}>
			<option value="">{t('signups.current_season')}</option>
			{#each data.seasons as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
		</select>
		{#snippet tabs()}
			<FilterTabs tabs={tabList} active={data.filters.only} label={t('signups.tabs')} hrefFor={(key) => href({ only: key })} onnavigate={navigate} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-7 py-5">
		{#if done === 'approve'}<Alert kind="success">{t('signups.approved_one')}</Alert>{/if}
		{#if done === 'approveClean'}<Alert kind="success">{t('signups.approved_n', { n: approved })}</Alert>{/if}
		{#if done === 'place'}<Alert kind="success">{t('signups.placed')}</Alert>{/if}
		{#if done === 'cancel'}<Alert kind="success">{form && 'result' in form && form.result === 'deleted' ? t('signups.cancelled_deleted') : t('signups.cancelled')}</Alert>{/if}
		{#if failure}<Alert kind="error" title={t('signups.action_failed')}>{failure}</Alert>{/if}

		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<UserCheck size={28} />
				<p>{filtered ? t('signups.empty_filtered') : t('signups.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('signups.col.person')}</th><th>{t('signups.col.signed_up')}</th><th>{t('signups.col.fee')}</th><th>{t('signups.col.open')}</th><th>{t('signups.col.teams')}</th><th><span class="sr-only">{t('signups.col.actions')}</span></th></tr></thead>
					<tbody>
						{#each data.rows as r (r.membership_id)}
							<tr class="hover:bg-surface">
								<td>
									<a class="link font-semibold" href="/members/{r.member.id}">{r.member.name}</a>
									{#if r.clean}<span class="badge badge-sm badge-success ml-2">{t('signups.ready')}</span>{/if}
									<p class="text-xs text-muted">{r.member.email || t('signups.no_email')}</p>
								</td>
								<td class="font-mono text-xs whitespace-nowrap text-muted">{r.signed_up_at ? formatDate(r.signed_up_at) : '–'}</td>
								<td>
									<span class="badge badge-sm {feeBadge(r.fee_status)}">{t(`signups.fee.${r.fee_status}`)}</span>
									{#if Number(r.remaining_balance) > 0}<p class="mt-0.5 font-mono text-xs text-muted">{t('signups.fee_due', { amount: r.remaining_balance })}</p>{/if}
								</td>
								<td>
									{#if r.open_requirements.length}
										<a class="flex flex-wrap items-center gap-1" href={documentsHref(r.member.id)} aria-label={t('signups.open_documents', { name: r.member.name })}>
											<FileText size={13} class="text-warn-text" aria-hidden="true" />
											{#each r.open_requirements as name (name)}<span class="badge badge-sm badge-warning">{name}</span>{/each}
										</a>
									{:else}<span class="text-sm text-muted">{t('signups.none_open')}</span>{/if}
								</td>
								<td class="text-sm">{r.teams.length ? r.teams.map((x) => x.name).join(', ') : '–'}</td>
								<td class="text-right whitespace-nowrap">
									<div class="flex justify-end gap-1">
										<form method="POST" action="?/approve" use:enhance={run()}>
											<input type="hidden" name="id" value={r.membership_id} />
											<button class="btn btn-primary btn-xs gap-1" type="submit" disabled={busy || !r.clean} title={r.clean ? '' : t('signups.approve_blocked')}><Check size={13} /> {t('signups.approve')}</button>
										</form>
										<button class="btn btn-outline btn-xs gap-1" type="button" onclick={() => ((target = r), (placeOpen = true))}><Shirt size={13} /> {t('signups.place')}</button>
										<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('signups.cancel_named', { name: r.member.name })} onclick={() => ((target = r), (cancelOpen = true))}><Ban size={13} /></button>
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>

<Modal bind:open={allOpen} title={t('signups.approve_all_title')}>
	<form method="POST" action="?/approveClean" class="grid gap-3" use:enhance={run(() => (allOpen = false))}>
		<input type="hidden" name="season" value={data.filters.season} />
		<p>{t('signups.approve_all_body', { n: data.clean })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (allOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><CheckCheck size={16} /> {t('signups.approve_all', { n: data.clean })}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={placeOpen} title={target ? t('signups.place_title', { name: target.member.name }) : ''}>
	<form method="POST" action="?/place" class="grid gap-3" use:enhance={run(() => (placeOpen = false))}>
		<input type="hidden" name="id" value={target?.membership_id ?? ''} />
		<p class="text-sm text-muted">{t('signups.place_help')}</p>
		<div class="form-control">
			<label class="label" for="place-team"><span class="label-text">{t('signups.team')}</span></label>
			<select id="place-team" name="team_id" class="select select-bordered w-full" required>
				<option value="" disabled selected>{t('signups.pick_team')}</option>
				{#each data.options.teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
			</select>
		</div>
		<div class="grid grid-cols-2 gap-3">
			<div class="form-control">
				<label class="label" for="place-pos"><span class="label-text">{t('signups.position')}</span></label>
				<select id="place-pos" name="position_id" class="select select-bordered w-full">
					<option value="">{t('signups.no_position')}</option>
					{#each data.options.positions as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
				</select>
			</div>
			<div class="form-control">
				<label class="label" for="place-jersey"><span class="label-text">{t('signups.jersey')}</span></label>
				<input id="place-jersey" name="jersey_number" type="number" min="0" class="input input-bordered w-full" />
			</div>
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (placeOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Shirt size={16} /> {t('signups.place')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={cancelOpen} title={t('signups.cancel_title')}>
	<form method="POST" action="?/cancel" class="grid gap-3" use:enhance={run(() => (cancelOpen = false))}>
		<input type="hidden" name="id" value={target?.membership_id ?? ''} />
		<p>{t('signups.cancel_body', { name: target?.member.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (cancelOpen = false)}>{t('signups.keep')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Ban size={16} /> {t('signups.cancel_confirm')}</button>
		</div>
	</form>
</Modal>
