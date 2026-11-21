<script lang="ts">
	import { ArrowLeft, FileDown, Pencil, Plus, Trash2, Utensils } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDate, fmtDay, numOrNull, Runner, strOrNull, TBASE, type Person } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const roster = $derived(data.roster);
	const canOperate = $derived(tn.can_operate);
	const run = new Runner();
	const base = $derived(`/tournaments/${tn.id}/teams/${data.teamId}`);

	type Form = { first_name: string; last_name: string; jersey_number: string; role: string; position_label: string; date_of_birth: string; license_number: string; dietary_restrictions: string; lunch_days: string[] };
	const blank = (): Form => ({ first_name: '', last_name: '', jersey_number: '', role: 'player', position_label: '', date_of_birth: '', license_number: '', dietary_restrictions: '', lunch_days: [] });
	let open = $state(false);
	let editing = $state<Person | null>(null);
	let f = $state<Form>(blank());
	let deleting = $state<Person | null>(null);
	let deleteOpen = $state(false);

	function openForm(p: Person | null) {
		editing = p;
		f = p
			? { first_name: p.first_name, last_name: p.last_name, jersey_number: p.jersey_number === null ? '' : String(p.jersey_number), role: p.role, position_label: p.position_label, date_of_birth: p.date_of_birth ?? '', license_number: p.license_number, dietary_restrictions: p.dietary_restrictions, lunch_days: [...p.lunch_days] }
			: blank();
		run.clear();
		open = true;
	}
	// Names are only editable on foreign entries (member-backed entries follow the member).
	const namesLocked = $derived(!!editing && !!editing.member_id);
	async function save(e: SubmitEvent) {
		e.preventDefault();
		const body = {
			...(namesLocked ? {} : { first_name: f.first_name.trim(), last_name: f.last_name.trim() }),
			jersey_number: numOrNull(f.jersey_number),
			role: f.role,
			position_label: f.position_label.trim(),
			date_of_birth: f.role === 'staff' ? null : strOrNull(f.date_of_birth),
			license_number: f.license_number.trim(),
			dietary_restrictions: f.dietary_restrictions.trim(),
			...(roster?.lunch_mode === 'optional' ? { lunch_days: f.lunch_days } : {})
		};
		const ok = editing ? await run.patch(`${base}/roster/${editing.id}`, body) : await run.post(`${base}/roster`, body);
		if (ok) open = false;
	}
	async function remove() {
		if (deleting && (await run.del(`${base}/roster/${deleting.id}`))) deleteOpen = false;
	}
	const toggleDay = (d: string) => (f.lunch_days = f.lunch_days.includes(d) ? f.lunch_days.filter((x) => x !== d) : [...f.lunch_days, d]);
	/** Lunch days can also be flipped straight in the table when lunch is optional. */
	const setLunch = (p: Person, day: string, on: boolean) => run.put(`${base}/roster/${p.id}/lunch`, { lunch_days: on ? [...p.lunch_days, day] : p.lunch_days.filter((d) => d !== day) });

	usePageHeader(() => ({ title: roster?.team.name ?? tn.name, context: tn.name, actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/api/v1{base}/roster/pdf" target="_blank" rel="noopener"><FileDown size={16} /> {t('tn.roster.pdf')}</a>
	{#if roster && roster.lunch_mode !== 'none'}<a class="btn btn-outline gap-2" href="/api/v1{base}/lunch/pdf" target="_blank" rel="noopener"><Utensils size={16} /> {t('tn.roster.lunch_pdf')}</a>{/if}
	{#if canOperate}<button class="btn btn-primary gap-2" type="button" onclick={() => openForm(null)}><Plus size={16} /> {t('tn.roster.add')}</button>{/if}
{/snippet}

<svelte:head><title>{pageTitle(roster?.team.name ?? t('tn.tab.teams'))}</title></svelte:head>

<div class="grid gap-4">
	<a class="link inline-flex w-fit items-center gap-1 text-sm" href="{TBASE}/{tn.id}/teams"><ArrowLeft size={14} /> {t('tn.roster.back')}</a>
	{#if run.error && !open && !deleteOpen}<Alert kind="error">{run.error}</Alert>{/if}
	{#if !roster}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#if roster.min_age !== null || roster.max_age !== null}
			<Alert kind="info">{t('tn.roster.age_range', { min: roster.min_age ?? '—', max: roster.max_age ?? '—' })}</Alert>
		{/if}
		<div class="card overflow-x-auto">
			<table class="table">
				<thead>
					<tr>
						<th class="w-12">#</th>
						<th>{t('tn.roster.name')}</th>
						<th>{t('tn.roster.role')}</th>
						<th class="hidden md:table-cell">{t('tn.roster.dob')}</th>
						<th class="hidden lg:table-cell">{t('tn.roster.license')}</th>
						<th>{t('tn.roster.dietary')}</th>
						{#if roster.lunch_mode !== 'none'}<th>{t('tn.roster.lunch')}</th>{/if}
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each roster.entries as p (p.id)}
						<tr class="hover:bg-paper">
							<td class="font-mono text-xs">{p.jersey_number ?? '—'}</td>
							<td>{p.name}{#if p.age_flagged} <span class="badge badge-warning badge-xs">{t('tn.roster.age_flag')}</span>{/if}</td>
							<td>{t(`tn.role.${p.role}`)}{p.position_label ? ` · ${p.position_label}` : ''}</td>
							<td class="hidden font-mono text-xs md:table-cell">{fmtDate(p.date_of_birth)}</td>
							<td class="hidden font-mono text-xs lg:table-cell">{p.license_number || '—'}</td>
							<td class="text-xs">{p.dietary_restrictions || '—'}</td>
							{#if roster.lunch_mode !== 'none'}
								<td>
									{#if roster.lunch_mode === 'optional' && canOperate}
										<div class="flex flex-wrap gap-1">
											{#each roster.dates as d (d)}
												<label class="flex items-center gap-1 text-xs"><input type="checkbox" class="checkbox checkbox-xs" checked={p.lunch_days.includes(d)} disabled={run.busy} onchange={(e) => setLunch(p, d, e.currentTarget.checked)} />{fmtDate(d, { weekday: 'short', day: 'numeric' })}</label>
											{/each}
										</div>
									{:else}
										<span class="font-mono text-xs">{p.lunch_days.length}/{roster.dates.length}</span>
									{/if}
								</td>
							{/if}
							<td class="text-right whitespace-nowrap">
								{#if canOperate}
									<button class="btn btn-ghost btn-xs btn-square text-muted" type="button" aria-label={t('tn.roster.edit_named', { name: p.name })} onclick={() => openForm(p)}><Pencil size={14} /></button>
									<button class="btn btn-ghost btn-xs btn-square text-error" type="button" aria-label={t('tn.roster.delete_named', { name: p.name })} onclick={() => { deleting = p; run.clear(); deleteOpen = true; }}><Trash2 size={14} /></button>
								{/if}
							</td>
						</tr>
					{:else}
						<tr><td colspan="8" class="py-8 text-center text-muted">{t('tn.roster.empty')}</td></tr>
					{/each}
				</tbody>
			</table>
		</div>

		{#if roster.lunch.length}
			<section class="card card-body gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Utensils size={16} class="shrink-0" aria-hidden="true" /> {t('tn.roster.lunch_card')}</h2>
				<div class="overflow-x-auto">
					<table class="table table-sm">
						<thead><tr><th>{t('tn.roster.day')}</th><th class="text-right">{t('tn.roster.players')}</th><th class="text-right">{t('tn.roster.staff')}</th><th>{t('tn.roster.dietary')}</th></tr></thead>
						<tbody>
							{#each roster.lunch as l (l.date)}
								<tr><td>{fmtDay(l.date)}</td><td class="text-right font-mono text-xs">{l.player_count}</td><td class="text-right font-mono text-xs">{l.staff_count}</td><td class="text-xs">{Array.isArray(l.dietary_notes) ? l.dietary_notes.join('; ') : l.dietary_notes || '—'}</td></tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}
	{/if}
</div>

<Modal bind:open title={editing ? t('tn.roster.edit') : t('tn.roster.add')}>
	<form class="grid gap-3" onsubmit={save}>
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('tn.roster.first_name')} bind:value={f.first_name} error={run.fields.first_name} required disabled={namesLocked} />
			<Field label={t('tn.roster.last_name')} bind:value={f.last_name} error={run.fields.last_name} required disabled={namesLocked} />
		</div>
		<div class="grid grid-cols-2 gap-3">
			<div class="form-control w-full">
				<label class="label" for="r-role"><span class="label-text">{t('tn.roster.role')}</span></label>
				<select id="r-role" class="select select-bordered w-full" bind:value={f.role}>
					<option value="player">{t('tn.role.player')}</option>
					<option value="staff">{t('tn.role.staff')}</option>
				</select>
			</div>
			<Field label={t('tn.roster.jersey')} type="number" min="0" bind:value={f.jersey_number} error={run.fields.jersey_number} />
		</div>
		<Field label={t('tn.roster.position')} bind:value={f.position_label} error={run.fields.position_label} />
		{#if f.role === 'player'}<Field label={t('tn.roster.dob')} type="date" bind:value={f.date_of_birth} error={run.fields.date_of_birth} />{/if}
		<Field label={t('tn.roster.license')} bind:value={f.license_number} error={run.fields.license_number} />
		<Field label={t('tn.roster.dietary')} bind:value={f.dietary_restrictions} error={run.fields.dietary_restrictions} />
		{#if roster?.lunch_mode === 'optional'}
			<fieldset class="form-control">
				<legend class="label"><span class="label-text">{t('tn.roster.lunch_days')}</span></legend>
				<div class="flex flex-wrap gap-3">
					{#each roster.dates as d (d)}
						<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" checked={f.lunch_days.includes(d)} onchange={() => toggleDay(d)} />{fmtDay(d)}</label>
					{/each}
				</div>
				{#if run.fields.lunch_days}<span class="mt-1 block text-xs text-error">{run.fields.lunch_days}</span>{/if}
			</fieldset>
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('tn.roster.delete_title')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.roster.delete_body', { name: deleting?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={remove}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
