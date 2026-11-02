<script lang="ts">
	import { Mail, MailX, Construction } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import { withZone } from '#lib/time.ts';
	import type { Switches } from '#lib/features.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';

	let { switches, canEdit, onchange }: { switches: Switches; canEdit: boolean; onchange: () => void } = $props();

	let maintOpen = $state(false);
	let mailOpen = $state(false);
	let message = $state('');
	let error = $state('');
	let notice = $state('');
	let busy = $state(false);

	const maint = $derived(switches.maintenance);
	const mail = $derived(switches.email_suppression);

	async function put(path: string, body: unknown, done: string) {
		busy = true;
		error = '';
		const r = await platform('PUT', path, body);
		busy = false;
		if (!r.ok) {
			error = r.fields.message ?? r.message ?? 'The switch could not be changed.';
			return false;
		}
		notice = done;
		onchange();
		return true;
	}

	function askMaintenance() {
		if (maint.is_active) return void turnOffMaintenance();
		message = maint.message ?? '';
		error = notice = '';
		maintOpen = true;
	}
	async function turnOffMaintenance() {
		notice = '';
		await put('/switches/maintenance', { active: false }, 'Maintenance mode is off.');
	}
	async function confirmMaintenance(e: SubmitEvent) {
		e.preventDefault();
		if (!message.trim()) return (error = 'Write the message visitors will see.');
		if (await put('/switches/maintenance', { active: true, message: message.trim() }, 'Maintenance mode is on.')) maintOpen = false;
	}

	function askMail() {
		error = notice = '';
		if (mail.is_active) void put('/switches/email-suppression', { active: false }, 'Automated email is flowing again.');
		else mailOpen = true;
	}
	async function confirmMail() {
		if (await put('/switches/email-suppression', { active: true }, 'Automated email is paused.')) mailOpen = false;
	}
	const since = (s: { started_at: string | null; started_by_email: string | null }) => `since ${withZone(s.started_at)}${s.started_by_email ? ` by ${s.started_by_email}` : ''}`;
</script>

{#if notice}<Alert kind="success">{notice}</Alert>{/if}
{#if error && !maintOpen && !mailOpen}<Alert kind="error">{error}</Alert>{/if}

<div class="grid gap-4 md:grid-cols-2">
	<div class="card {maint.is_active ? 'border-club' : ''}">
		<div class="card-body gap-3">
			<div class="card-title"><Construction size={16} aria-hidden="true" /> Maintenance mode <span class="badge {maint.is_active ? 'badge-error' : 'badge-ghost'}">{maint.is_active ? 'on' : 'off'}</span></div>
			<p class="text-sm text-muted">Shows visitors a maintenance message instead of the app. Turn it on while deploying or repairing the platform.</p>
			{#if maint.is_active}
				<div class="rounded border border-club bg-row-warn p-3 text-sm">
					<div class="text-ink">{maint.message}</div>
					<div class="mt-1 font-mono text-[11px] text-muted">{since(maint)}</div>
				</div>
			{/if}
			{#if canEdit}
				<div>
					<button class="btn btn-sm {maint.is_active ? 'btn-success' : 'btn-error btn-outline'}" onclick={askMaintenance} disabled={busy}>{maint.is_active ? 'Turn maintenance off' : 'Turn maintenance on…'}</button>
				</div>
			{/if}
		</div>
	</div>

	<div class="card {mail.is_active ? 'border-warn' : ''}">
		<div class="card-body gap-3">
			<div class="card-title">
				{#if mail.is_active}<MailX size={16} aria-hidden="true" />{:else}<Mail size={16} aria-hidden="true" />{/if} Pause automated email
				<span class="badge {mail.is_active ? 'badge-warning' : 'badge-ghost'}">{mail.is_active ? 'paused' : 'flowing'}</span>
			</div>
			<p class="text-sm text-muted">Stops reminders, digests and other automated mail. Account mail such as password resets and sign-in links still goes out.</p>
			{#if mail.is_active}<div class="rounded border border-warn bg-row-warn p-3 font-mono text-[11px] text-muted">Paused {since(mail)}</div>{/if}
			{#if canEdit}
				<div>
					<button class="btn btn-sm {mail.is_active ? 'btn-success' : 'btn-warning btn-outline'}" onclick={askMail} disabled={busy}>{mail.is_active ? 'Resume automated email' : 'Pause automated email…'}</button>
				</div>
			{/if}
		</div>
	</div>
</div>
{#if !canEdit}<p class="text-xs text-muted">Only superusers can change the switches.</p>{/if}

<Modal bind:open={maintOpen} title="Turn maintenance mode on">
	<form class="flex flex-col gap-4" onsubmit={confirmMaintenance}>
		<Alert kind="warning">Visitors, including club administrators, will see the message below instead of the app until you turn this off.</Alert>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="form-control">
			<label class="label" for="maint-msg"><span class="label-text">Message shown to visitors</span></label>
			<textarea id="maint-msg" class="textarea textarea-bordered w-full" rows="3" required bind:value={message} placeholder="We are upgrading the platform and will be back shortly."></textarea>
		</div>
		<div class="flex justify-end gap-2">
			<button type="button" class="btn btn-ghost btn-sm" onclick={() => (maintOpen = false)}>Cancel</button>
			<button class="btn btn-error btn-sm" disabled={busy}>Turn on maintenance</button>
		</div>
	</form>
</Modal>

<Modal bind:open={mailOpen} title="Pause automated email">
	<div class="flex flex-col gap-4">
		<Alert kind="warning">Reminders, digests and other automated mail will not be sent while paused. Account mail (password reset, sign-in links) still goes out.</Alert>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="flex justify-end gap-2">
			<button class="btn btn-ghost btn-sm" onclick={() => (mailOpen = false)}>Cancel</button>
			<button class="btn btn-warning btn-sm" disabled={busy} onclick={confirmMail}>Pause email</button>
		</div>
	</div>
</Modal>
