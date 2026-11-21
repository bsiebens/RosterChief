<script lang="ts">
	import { HandHelping, Pencil, Plus, Printer, Trash2, UserPlus, Wand2, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Toast from '#lib/components/Toast.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, fmtTime, fromLocalInput, headerSnapshot, numOrNull, Runner, TBASE, toLocalInput } from '#lib/tournaments.svelte.ts';
	import type { TaskDoc } from './+page.server.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const canOperate = $derived(tn.can_operate);
	const base = $derived(`/tournaments/${tn.id}/tasks`);
	const run = new Runner();

	// --- create / edit
	let open = $state(false);
	let editing = $state<TaskDoc | null>(null);
	let f = $state({ title: '', description: '', location_label: '', starts_at: '', ends_at: '', needed_quantity: '1' });
	function openForm(task: TaskDoc | null) {
		editing = task;
		f = task ? { title: task.title, description: task.description, location_label: task.location_label, starts_at: toLocalInput(task.starts_at), ends_at: toLocalInput(task.ends_at), needed_quantity: String(task.needed_quantity) } : { title: '', description: '', location_label: '', starts_at: '', ends_at: '', needed_quantity: '1' };
		run.clear();
		open = true;
	}
	async function save(e: SubmitEvent) {
		e.preventDefault();
		const body = { title: f.title.trim(), description: f.description, location_label: f.location_label.trim(), starts_at: fromLocalInput(f.starts_at), ends_at: fromLocalInput(f.ends_at), needed_quantity: numOrNull(f.needed_quantity) ?? 1 };
		if (await (editing ? run.patch(`${base}/${editing.id}`, body) : run.post(base, body))) open = false;
	}
	let delTask = $state<TaskDoc | null>(null);
	let delOpen = $state(false);
	async function remove() {
		if (delTask && (await run.del(`${base}/${delTask.id}`))) delOpen = false;
	}

	// --- generate shifts
	let genOpen = $state(false);
	let g = $state({ title: '', location_label: '', starts_at: '', ends_at: '', shift_minutes: '60', needed_quantity: '2' });
	async function generate(e: SubmitEvent) {
		e.preventDefault();
		const body = { title: g.title.trim(), location_label: g.location_label.trim(), starts_at: fromLocalInput(g.starts_at), ends_at: fromLocalInput(g.ends_at), shift_minutes: Number(g.shift_minutes), needed_quantity: Number(g.needed_quantity) };
		if (await run.post<{ created_count: number }>(`${base}/generate`, body, (d) => void (run.notice = t('tn.tasks.generated', { n: d?.created_count ?? 0 })), { notice: '' })) genOpen = false;
	}

	// --- assign
	let assignOpen = $state(false);
	let assignTask = $state<TaskDoc | null>(null);
	let memberId = $state('');
	async function assign(e: SubmitEvent) {
		e.preventDefault();
		if (assignTask && memberId && (await run.post(`${base}/${assignTask.id}/assign`, { member_id: memberId }))) {
			assignOpen = false;
			memberId = '';
		}
	}
	const unclaim = (task: TaskDoc, claimId: string) => run.del(`${base}/${task.id}/claims/${claimId}`);

	const hdr = headerSnapshot(() => ({ id: tn.id, canOperate }));
	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tasks.count', { n: data.tasks.length }), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="{TBASE}/{hdr.id}/tasks/print"><Printer size={16} /> {t('tn.tasks.print')}</a>
	{#if hdr.canOperate}
		<button class="btn btn-outline gap-2" type="button" onclick={() => { run.clear(); genOpen = true; }}><Wand2 size={16} /> {t('tn.tasks.generate')}</button>
		<button class="btn btn-primary gap-2" type="button" onclick={() => openForm(null)}><Plus size={16} /> {t('tn.tasks.add')}</button>
	{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('tn.tab.tasks'))}</title></svelte:head>

<div class="grid gap-3">
	{#if run.error && !open && !genOpen && !assignOpen && !delOpen}<Alert kind="error">{run.error}</Alert>{/if}
	{#if data.error}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if !data.tasks.length}
		<div class="card card-body items-center py-10 text-center text-muted"><HandHelping size={28} /><p>{t('tn.tasks.empty')}</p></div>
	{:else}
		{#each data.tasks as task (task.id)}
			<section class="card card-body gap-2">
				<div class="flex flex-wrap items-start gap-3">
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center gap-2">
							<strong class="font-display text-ink">{task.title}</strong>
							<span class="badge badge-sm {task.is_full ? 'badge-success' : 'badge-warning'}">{task.claimed_count}/{task.needed_quantity}</span>
							{#if task.location_label}<span class="badge badge-sm badge-outline">{task.location_label}</span>{/if}
						</div>
						<p class="font-mono text-xs text-muted">{fmtDateTime(task.starts_at)} – {fmtTime(task.ends_at)}</p>
						{#if task.description}<p class="text-sm text-muted">{task.description}</p>{/if}
					</div>
					{#if canOperate}
						<div class="flex items-center gap-1">
							<button class="btn btn-outline btn-xs gap-1" type="button" disabled={task.is_full} onclick={() => { assignTask = task; memberId = ''; run.clear(); assignOpen = true; }}><UserPlus size={13} /> {t('tn.tasks.assign')}</button>
							<button class="btn btn-ghost btn-xs btn-square text-muted" type="button" aria-label={t('tn.tasks.edit_named', { name: task.title })} onclick={() => openForm(task)}><Pencil size={14} /></button>
							<button class="btn btn-ghost btn-xs btn-square text-error" type="button" aria-label={t('tn.tasks.delete_named', { name: task.title })} onclick={() => { delTask = task; run.clear(); delOpen = true; }}><Trash2 size={14} /></button>
						</div>
					{/if}
				</div>
				{#if task.claims.length}
					<ul class="flex flex-wrap gap-2">
						{#each task.claims as c (c.id)}
							<li class="badge badge-lg gap-1 badge-outline">
								{c.label || c.member.name}
								{#if canOperate}<button type="button" class="ml-1 text-muted hover:text-error" aria-label={t('tn.tasks.unassign', { name: c.member.name })} disabled={run.busy} onclick={() => unclaim(task, c.id)}><X size={12} /></button>{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		{/each}
	{/if}
</div>

<Modal bind:open title={editing ? t('tn.tasks.edit') : t('tn.tasks.add')}>
	<form class="grid gap-3" onsubmit={save}>
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		<Field label={t('tn.tasks.f.title')} bind:value={f.title} error={run.fields.title} required />
		<Field label={t('tn.tasks.f.location')} bind:value={f.location_label} error={run.fields.location_label} />
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('tn.tasks.f.starts')} type="datetime-local" bind:value={f.starts_at} error={run.fields.starts_at} required />
			<Field label={t('tn.tasks.f.ends')} type="datetime-local" bind:value={f.ends_at} error={run.fields.ends_at} required />
		</div>
		<Field label={t('tn.tasks.f.needed')} type="number" min="1" bind:value={f.needed_quantity} error={run.fields.needed_quantity} />
		<div class="form-control w-full">
			<label class="label" for="task-desc"><span class="label-text">{t('tn.tasks.f.description')}</span></label>
			<textarea id="task-desc" class="textarea textarea-bordered min-h-20 w-full" bind:value={f.description}></textarea>
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={genOpen} title={t('tn.tasks.generate')}>
	<form class="grid gap-3" onsubmit={generate}>
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		<p class="text-sm text-muted">{t('tn.tasks.generate_help')}</p>
		<Field label={t('tn.tasks.f.title')} bind:value={g.title} error={run.fields.title} required />
		<Field label={t('tn.tasks.f.location')} bind:value={g.location_label} error={run.fields.location_label} />
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('tn.tasks.f.starts')} type="datetime-local" bind:value={g.starts_at} error={run.fields.starts_at} required />
			<Field label={t('tn.tasks.f.ends')} type="datetime-local" bind:value={g.ends_at} error={run.fields.ends_at} required />
		</div>
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('tn.tasks.f.shift')} type="number" min="5" bind:value={g.shift_minutes} error={run.fields.shift_minutes} required />
			<Field label={t('tn.tasks.f.needed')} type="number" min="1" bind:value={g.needed_quantity} error={run.fields.needed_quantity} required />
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (genOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('tn.tasks.generate')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={assignOpen} title={t('tn.tasks.assign_title', { name: assignTask?.title ?? '' })}>
	<form class="grid gap-3" onsubmit={assign}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<EntityPicker kind="member" name="member_id" label={t('tn.tasks.member')} bind:value={memberId} exclude={assignTask?.claims.map((c) => c.member.id) ?? []} />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (assignOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy || !memberId}>{t('tn.tasks.assign')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delOpen} title={t('tn.tasks.delete_title')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.tasks.delete_body', { name: delTask?.title ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={remove}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
<Toast message={run.notice} onhide={() => (run.notice = '')} />
