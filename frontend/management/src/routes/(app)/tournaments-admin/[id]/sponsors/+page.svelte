<script lang="ts">
	import { Handshake, ImageIcon, Pencil, Plus, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import NoticeAlert from '#lib/components/NoticeAlert.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { api } from '#lib/management-api.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { Runner } from '#lib/tournaments.svelte.ts';
	import type { SponsorDoc } from './+page.server.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const doc = $derived(data.sponsors);
	const base = $derived(`/tournaments/${tn.id}/sponsors`);
	const run = new Runner();

	const toggleClub = (use: boolean) => run.put(`${base}/settings`, { use_club_sponsors: use }, undefined, { notice: t('tn.sponsors.saved') });

	let open = $state(false);
	let editing = $state<SponsorDoc | null>(null);
	let f = $state({ name: '', url: '' });
	let logo = $state<HTMLInputElement | undefined>();
	function openForm(s: SponsorDoc | null) {
		editing = s;
		f = { name: s?.name ?? '', url: s?.url ?? '' };
		run.clear();
		open = true;
	}
	async function save(e: SubmitEvent) {
		e.preventDefault();
		const picked = logo?.files?.[0];
		const ok = await run.run(
			async () => {
				const res = editing ? await api<SponsorDoc>('PATCH', `${base}/${editing.id}`, { body: { name: f.name.trim(), url: f.url.trim() } }) : await api<SponsorDoc>('POST', base, { body: { name: f.name.trim(), url: f.url.trim() } });
				if (res.ok && picked && res.data) {
					const form = new FormData();
					form.set('logo', picked);
					return api('POST', `${base}/${res.data.id}/logo`, { form });
				}
				return res;
			},
			undefined,
			{ notice: t('tn.sponsors.saved') }
		);
		if (ok) {
			open = false;
			if (logo) logo.value = '';
		}
	}
	let del = $state<SponsorDoc | null>(null);
	let delOpen = $state(false);
	async function remove() {
		if (del && (await run.del(`${base}/${del.id}`, undefined, { notice: t('tn.sponsors.removed') }))) delOpen = false;
	}

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.sponsors') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.sponsors'))}</title></svelte:head>

<div class="grid gap-4">
	<NoticeAlert message={run.notice} onhide={() => (run.notice = '')} />
	{#if data.error || !doc}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#if run.error && !open && !delOpen}<Alert kind="error">{run.error}</Alert>{/if}
		<section class="card card-body card-hl gap-3">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Handshake size={16} aria-hidden="true" /> {t('tn.sponsors.club_title')}</h2>
			<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
				<input type="checkbox" class="checkbox mt-0.5" checked={doc.use_club_sponsors} disabled={run.busy} onchange={(e) => toggleClub(e.currentTarget.checked)} />
				<span class="min-w-0">
					<span class="block text-sm font-semibold text-ink">{t('tn.sponsors.use_club')}</span>
					<span class="block text-xs text-muted">{t('tn.sponsors.use_club_help', { n: doc.club_sponsor_count })}</span>
				</span>
			</label>
		</section>
		<section class="card card-body card-hl gap-3">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Handshake size={16} aria-hidden="true" /> {t('tn.sponsors.own_title')}</h2>
				<button class="btn btn-primary btn-sm gap-1" type="button" onclick={() => openForm(null)}><Plus size={14} /> {t('tn.sponsors.add')}</button>
			</div>
			<p class="text-xs text-muted">{t('tn.sponsors.own_help')}</p>
			{#if doc.results.length}
				<div class="overflow-x-auto rounded-lg border border-line">
					<table class="table w-full">
						<thead><tr><th class="w-24">{t('tn.sponsors.logo')}</th><th>{t('tn.sponsors.name')}</th><th>{t('tn.sponsors.url')}</th><th class="w-24"></th></tr></thead>
						<tbody>
							{#each doc.results as s (s.id)}
								<tr class="hover:bg-paper">
									<td class="align-middle">{#if s.logo_url}<img src={s.logo_url} alt="" class="h-10 w-20 object-contain" />{:else}<span class="flex h-10 w-20 items-center justify-center rounded border border-dashed border-line text-muted"><ImageIcon size={16} aria-hidden="true" /></span>{/if}</td>
									<td class="align-middle font-semibold text-ink">{s.name}</td>
									<td class="align-middle text-xs"><span class="block max-w-xs truncate">{s.url || '—'}</span></td>
									<td class="align-middle">
										<div class="flex items-center justify-end gap-1">
											<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.sponsors.edit')} title={t('tn.sponsors.edit')} onclick={() => openForm(s)}><Pencil size={13} /></button>
											<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.sponsors.delete')} title={t('tn.sponsors.delete')} onclick={() => { del = s; run.clear(); delOpen = true; }}><Trash2 size={13} /></button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<p class="text-sm text-muted">{t('tn.sponsors.none')}</p>
			{/if}
		</section>
	{/if}
</div>

<Modal bind:open title={editing ? t('tn.sponsors.edit') : t('tn.sponsors.add')}>
	<form class="grid gap-3" onsubmit={save}>
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		<Field label={t('tn.sponsors.name')} bind:value={f.name} error={run.fields.name} required />
		<Field label={t('tn.sponsors.url')} type="url" placeholder="https://" bind:value={f.url} error={run.fields.url} />
		<div class="form-control">
			<label class="label-text mb-1 text-sm font-medium" for="sp-logo">{t('tn.sponsors.logo')}</label>
			<input id="sp-logo" bind:this={logo} type="file" class="file-input file-input-bordered w-full" accept="image/*" />
			{#if run.fields.logo}<span class="text-xs text-error">{run.fields.logo}</span>{/if}
			{#if editing?.logo_url}<span class="mt-1 text-xs text-muted">{t('tn.sponsors.replace_logo')}</span>{/if}
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delOpen} title={t('tn.sponsors.delete')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.sponsors.delete_body', { name: del?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={remove}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
