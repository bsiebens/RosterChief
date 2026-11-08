<script lang="ts">
	import { ClipboardList, Hand, Pencil, Plus, Trash2, UserPlus, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api, type ApiCall } from '#lib/management-api.ts';
	import type { Task, TaskList } from '#lib/events/types.ts';

	/** The Tasks tab: what has to be done and who took it; managers add / edit / delete tasks and claim for members. */
	let { eventId, initial }: { eventId: string; initial: TaskList } = $props();

	// svelte-ignore state_referenced_locally
	let list = $state<TaskList>($state.snapshot(initial));
	$effect(() => {
		list = $state.snapshot(initial);
	});

	let errorMsg = $state('');
	let busy = $state(false);
	const failText = (res: ApiCall<unknown>) => res.fields.__all__ || res.fields.title || res.fields.needed_quantity || res.fields.member_id || res.message || t('common.error.generic');
	const put = (task: Task) => (list.tasks = list.tasks.map((x) => (x.id === task.id ? task : x)));
	async function reload() {
		const res = await api<TaskList>('GET', `/events/${eventId}/tasks`);
		if (res.ok && res.data) list = res.data;
	}
	async function run<T>(fn: () => Promise<ApiCall<T>>, done?: (data: T | undefined) => void | Promise<void>) {
		busy = true;
		errorMsg = '';
		const res = await fn();
		busy = false;
		if (res.ok) await done?.(res.data);
		else errorMsg = failText(res);
		return res;
	}

	// Add / edit
	let formOpen = $state(false);
	let editing = $state<Task | null>(null);
	let title = $state('');
	let description = $state('');
	let quantity = $state('1');
	let notify = $state(false);
	let formError = $state('');
	let formFields = $state<Record<string, string>>({});
	function openForm(task: Task | null) {
		editing = task;
		title = task?.title ?? '';
		description = task?.description ?? '';
		quantity = String(task?.needed_quantity ?? 1);
		notify = false;
		formError = '';
		formFields = {};
		formOpen = true;
	}
	async function saveForm(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		const body = { title: title.trim(), description: description.trim(), needed_quantity: Math.max(1, Number(quantity) || 1) };
		const res = editing ? await api<Task>('PATCH', `/events/${eventId}/tasks/${editing.id}`, { body }) : await api<Task>('POST', `/events/${eventId}/tasks`, { body: { ...body, notify } });
		busy = false;
		if (res.ok && res.data) {
			if (editing) put(res.data);
			else list.tasks = [...list.tasks, res.data];
			formOpen = false;
		} else {
			formFields = res.fields;
			formError = res.fields.__all__ || (!Object.keys(res.fields).length ? res.message || t('common.error.generic') : '');
		}
	}

	// Delete
	let deleteOpen = $state(false);
	let deleting = $state<Task | null>(null);
	async function confirmDelete() {
		if (!deleting) return;
		const id = deleting.id;
		const res = await run(() => api('DELETE', `/events/${eventId}/tasks/${id}`), () => {
			list.tasks = list.tasks.filter((x) => x.id !== id);
		});
		if (res.ok) deleteOpen = false;
	}

	// Claims
	let claimOpen = $state(false);
	let claimTask = $state<Task | null>(null);
	let claimMember = $state('');
	let claimError = $state('');
	function openClaim(task: Task) {
		claimTask = task;
		claimMember = '';
		claimError = '';
		claimOpen = true;
	}
	async function claimFor(e: SubmitEvent) {
		e.preventDefault();
		if (!claimTask || !claimMember) return;
		busy = true;
		const res = await api<Task>('POST', `/events/${eventId}/tasks/${claimTask.id}/claim`, { query: { member_id: claimMember } });
		busy = false;
		if (res.ok && res.data) {
			put(res.data);
			claimOpen = false;
		} else claimError = failText(res);
	}
	const claimSelf = (task: Task) => run(() => api<Task>('POST', `/events/${eventId}/tasks/${task.id}/claim`), (d) => {
		if (d) put(d);
	});
	const unclaim = (task: Task, memberId: string | null) => run(() => api('DELETE', `/events/${eventId}/tasks/${task.id}/claim`, { query: { member_id: memberId } }), reload);
</script>

<div class="grid gap-4">
	{#if errorMsg}<Alert kind="error">{errorMsg}</Alert>{/if}
	<section class="card overflow-hidden">
	<header class="flex items-center justify-between gap-3 px-5 py-3">
		<h2 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><ClipboardList size={16} aria-hidden="true" /> {t('events.tab.tasks')} <span class="font-mono text-xs font-normal text-muted">· {list.tasks.length}</span></h2>
		{#if list.can_manage}<button class="btn btn-primary btn-sm gap-2" type="button" onclick={() => openForm(null)}><Plus size={14} /> {t('events.tasks.add')}</button>{/if}
	</header>
	{#each list.tasks as task (task.id)}
		<article class="grid gap-2 border-t border-rule px-5 py-4">
			<div class="flex flex-wrap items-start justify-between gap-2">
				<div class="min-w-0">
					<h3 class="font-semibold text-ink">{task.title}</h3>
					{#if task.description}<p class="text-sm whitespace-pre-line text-slate">{task.description}</p>{/if}
				</div>
				<div class="flex items-center gap-1.5">
					<span class="badge badge-sm {task.open_slots ? 'badge-warning' : 'badge-success'}">{t('events.tasks.slots', { claimed: task.claimed_count, needed: task.needed_quantity })}</span>
					{#if list.can_manage}
						<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('events.tasks.edit_named', { name: task.title })} title={t('common.edit')} onclick={() => openForm(task)}><Pencil size={13} /></button>
						<button class="btn btn-ghost btn-xs btn-square text-danger" type="button" aria-label={t('events.tasks.delete_named', { name: task.title })} title={t('common.delete')} onclick={() => ((deleting = task), (deleteOpen = true))}><Trash2 size={13} /></button>
					{/if}
				</div>
			</div>
			<ul class="flex flex-wrap gap-1.5" aria-label={t('events.tasks.claims')}>
				{#each task.claims as c (c.id)}
					<li class="inline-flex items-center gap-1 rounded-full border border-line bg-paper py-0.5 pr-1 pl-2.5 text-xs">
						<span class="font-semibold text-ink">{c.label}</span>
						{#if c.member_name && c.member_name !== c.label}<span class="text-muted">({c.member_name})</span>{/if}
						{#if list.can_manage && c.member_id}
							<button class="btn btn-ghost btn-circle btn-sm -my-0.5 size-8 min-h-8 hover:bg-error/10 hover:text-error" type="button" disabled={busy} aria-label={t('events.tasks.unclaim_named', { name: c.member_name ?? c.label })} onclick={() => unclaim(task, c.member_id)}><X size={16} strokeWidth={2.5} /></button>
						{/if}
					</li>
				{:else}
					<li class="text-xs text-muted">{t('events.tasks.nobody')}</li>
				{/each}
			</ul>
			<div class="flex flex-wrap gap-2">
				{#if task.claimed_by_me}
					<button class="btn btn-outline btn-xs gap-1" type="button" disabled={busy} onclick={() => unclaim(task, null)}><X size={12} /> {t('events.tasks.give_back')}</button>
				{:else if task.open_slots > 0}
					<button class="btn btn-outline btn-xs gap-1" type="button" disabled={busy} onclick={() => claimSelf(task)}><Hand size={12} /> {t('events.tasks.take')}</button>
				{/if}
				{#if list.can_manage && task.open_slots > 0}
					<button class="btn btn-outline btn-xs gap-1" type="button" onclick={() => openClaim(task)}><UserPlus size={12} /> {t('events.tasks.claim_for')}</button>
				{/if}
			</div>
		</article>
	{:else}
		<div class="flex flex-col items-center gap-1 border-t border-rule px-5 py-10 text-center text-muted"><ClipboardList size={28} aria-hidden="true" /><p>{t('events.tasks.none')}</p></div>
	{/each}
	</section>
</div>

<Modal bind:open={formOpen} title={editing ? t('events.tasks.edit') : t('events.tasks.add')}>
	<form class="grid gap-3" onsubmit={saveForm}>
		{#if formError}<Alert kind="error">{formError}</Alert>{/if}
		<Field label={t('events.tasks.f.title')} name="title" bind:value={title} required error={formFields.title} autocomplete="off" />
		<label class="form-control w-full">
			<span class="label"><span class="label-text">{t('events.tasks.f.description')}</span></span>
			<textarea class="textarea w-full {formFields.description ? 'textarea-error' : ''}" rows="3" bind:value={description}></textarea>
			{#if formFields.description}<span class="mt-1 block text-xs text-error">{formFields.description}</span>{/if}
		</label>
		<Field label={t('events.tasks.f.quantity')} name="needed_quantity" type="number" min="1" bind:value={quantity} required error={formFields.needed_quantity} />
		{#if !editing}
			<label class="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" class="checkbox" bind:checked={notify} /> {t('events.tasks.f.notify')}</label>
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (formOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('events.tasks.delete')}>
	<div class="grid gap-3">
		<p>{t('events.tasks.delete_body', { name: deleting?.title ?? '' })}</p>
		{#if deleting?.claims.length}<Alert kind="warning">{t('events.tasks.delete_claimed')}</Alert>{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={busy} onclick={confirmDelete}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>

<Modal bind:open={claimOpen} title={t('events.tasks.claim_for')}>
	<form class="grid gap-3" onsubmit={claimFor}>
		{#if claimError}<Alert kind="error">{claimError}</Alert>{/if}
		<p class="text-sm">{claimTask?.title}</p>
		<EntityPicker kind="member" name="member_id" label={t('events.tasks.member')} bind:value={claimMember} exclude={claimTask?.claims.map((c) => c.member_id ?? '') ?? []} />
		<p class="text-xs text-muted">{t('events.tasks.claim_help')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (claimOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy || !claimMember}>{t('events.tasks.claim')}</button>
		</div>
	</form>
</Modal>
