<script lang="ts">
	import { untrack } from 'svelte';
	import { Search, X } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import { CATEGORIES, providerLabel, STATUS_BADGE, type PushTarget, type SendResponse, type UserPush } from '#lib/push.ts';
	import { fieldError } from '#lib/users.ts';

	let { initial, sandboxOn }: { initial: PushTarget | null; sandboxOn: boolean } = $props();

	const id = $props.id();
	let target = $state<PushTarget | null>(null);
	let reach = $state('');
	let q = $state('');
	let found = $state<PushTarget[]>([]);
	let searched = $state(false);
	let searchError = $state('');
	let title = $state('');
	let body = $state('');
	let data = $state('');
	let category = $state('general');
	let priority = $state('normal');
	let mode = $state<'sandbox' | 'live'>('sandbox');
	let confirmLive = $state(false);
	let busy = $state(false);
	let error = $state('');
	let fields = $state<Record<string, string>>({});
	let result = $state<SendResponse | null>(null);

	// Follow the ?target= link, but not every reload of the page data.
	$effect(() => {
		const key = initial ? `${initial.type}:${initial.id}:${initial.label}` : '';
		untrack(() => {
			if (key) target = initial;
		});
	});

	let timer: ReturnType<typeof setTimeout>;
	function search() {
		clearTimeout(timer);
		const text = q.trim();
		if (text.length < 2) {
			found = [];
			searched = false;
			return;
		}
		timer = setTimeout(async () => {
			const r = await platform<PushTarget[]>('GET', `/push/targets?q=${encodeURIComponent(text)}`);
			searchError = r.ok ? '' : (r.message ?? 'The search failed.');
			found = Array.isArray(r.data) ? r.data : [];
			searched = true;
		}, 300);
	}

	async function pick(t: PushTarget) {
		target = t;
		found = [];
		q = '';
		searched = false;
		result = null;
		reach = '';
		const r = await platform<UserPush>('GET', `/users/${t.id}/push`);
		if (r.ok && r.data) {
			const d = r.data.devices.filter((x) => x.enabled).length;
			reach = `${d} enabled device${d === 1 ? '' : 's'} and ${r.data.web_subscriptions.length} web subscription${r.data.web_subscriptions.length === 1 ? '' : 's'}.`;
		}
	}

	function parseData(): { ok: boolean; value: Record<string, unknown> | null } {
		const text = data.trim();
		if (!text) return { ok: true, value: null };
		try {
			const v = JSON.parse(text);
			if (v === null || typeof v !== 'object' || Array.isArray(v)) throw new Error();
			if (new Blob([text]).size > 2048) {
				fields.data = 'Keep the data under 2 KB.';
				return { ok: false, value: null };
			}
			return { ok: true, value: v };
		} catch {
			fields.data = 'This is not a valid JSON object, for example {"screen": "events"}.';
			return { ok: false, value: null };
		}
	}

	async function send(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		fields = {};
		result = null;
		if (!target) return (error = 'Choose who to send to.');
		if (!title.trim()) fields.title = 'Enter a title.';
		if (!body.trim()) fields.body = 'Enter a message.';
		const parsed = parseData();
		if (mode === 'live' && !confirmLive) fields.confirm_live = 'Tick this box to confirm.';
		if (Object.keys(fields).length) return;
		busy = true;
		const r = await platform<SendResponse>('POST', '/push/send', {
			target: { type: target.type, id: target.id },
			title: title.trim(),
			body: body.trim(),
			data: parsed.value,
			category,
			priority,
			mode,
			confirm_live: mode === 'live' && confirmLive
		});
		busy = false;
		if (!r.ok) {
			fields = { title: fieldError(r.fields, 'title') ?? '', body: fieldError(r.fields, 'body') ?? '', data: fieldError(r.fields, 'data') ?? '' };
			for (const k of Object.keys(fields)) if (!fields[k]) delete fields[k];
			return (error = r.message ?? 'The message could not be sent.');
		}
		result = r.data ?? null;
		confirmLive = false;
	}

	const who = $derived(target ? (target.type === 'user' ? 'Every enabled device and web subscription of this account.' : 'Only this one target.') : '');
</script>

<div class="card">
	<form class="card-body gap-4 text-sm" onsubmit={send} novalidate>
		<div class="card-title">Send a message</div>
		<p class="-mt-2 text-xs text-muted">Compose a push yourself and send it to one account or to a single device. User preferences and quiet hours are ignored.</p>

		<div>
			<div class="label"><span class="label-text">To</span></div>
			{#if target}
				<div class="flex flex-wrap items-center gap-2 rounded border border-edge px-3 py-2">
					<span class="badge badge-neutral">{target.type === 'user' ? 'account' : target.type === 'device' ? 'device' : 'web subscription'}</span>
					<span class="min-w-0 break-all font-semibold text-ink">{target.label}</span>
					{#if target.type !== 'user'}<span class="font-mono text-[11px] text-muted">{target.id}</span>{/if}
					<button type="button" class="btn btn-ghost btn-xs ml-auto" onclick={() => (target = null)}><X size={12} aria-hidden="true" /> Change</button>
				</div>
				<p class="mt-1 text-xs text-muted">{who} {reach}</p>
			{:else}
				<div class="relative">
					<Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" aria-hidden="true" />
					<input class="input input-bordered w-full pl-9" type="search" placeholder="Search an account by email or name" aria-label="Search accounts" bind:value={q} oninput={search} />
				</div>
				{#if searchError}<div class="mt-2"><Alert kind="error">{searchError}</Alert></div>{/if}
				{#if found.length}
					<ul class="mt-1 divide-y divide-line rounded border border-edge">
						{#each found as t (t.id)}
							<li><button type="button" class="w-full px-3 py-2 text-left hover:bg-subhead" onclick={() => pick(t)}>{t.label}</button></li>
						{/each}
					</ul>
				{:else if searched && !searchError}<p class="mt-1 text-xs text-muted">No accounts match.</p>{/if}
			{/if}
		</div>

		<div class="grid gap-3 sm:grid-cols-2">
			<div class="sm:col-span-2"><Field label="Title" bind:value={title} error={fields.title} maxlength={120} /></div>
			<div class="form-control w-full sm:col-span-2">
				<label class="label" for="{id}-body"><span class="label-text">Message</span></label>
				<textarea id="{id}-body" class="textarea textarea-bordered w-full {fields.body ? 'textarea-error' : ''}" rows="3" maxlength="500" bind:value={body} aria-invalid={fields.body ? 'true' : undefined}></textarea>
				{#if fields.body}<span class="mt-1 block text-xs text-club-dark">{fields.body}</span>{:else}<span class="label-text-alt mt-1 block">{body.length}/500</span>{/if}
			</div>
			<div class="form-control w-full sm:col-span-2">
				<label class="label" for="{id}-data"><span class="label-text">Data (optional JSON)</span></label>
				<textarea id="{id}-data" class="textarea textarea-bordered w-full font-mono text-xs {fields.data ? 'textarea-error' : ''}" rows="3" placeholder={'{"screen": "events"}'} bind:value={data} aria-invalid={fields.data ? 'true' : undefined}></textarea>
				{#if fields.data}<span class="mt-1 block text-xs text-club-dark">{fields.data}</span>{:else}<span class="label-text-alt mt-1 block">A JSON object of at most 2 KB, delivered to the app with the push.</span>{/if}
			</div>
			<div class="form-control w-full">
				<label class="label" for="{id}-category"><span class="label-text">Category</span></label>
				<select id="{id}-category" class="select select-bordered w-full" bind:value={category}>
					{#each CATEGORIES as [value, label] (value)}<option {value}>{label}</option>{/each}
				</select>
			</div>
			<div class="form-control w-full">
				<label class="label" for="{id}-priority"><span class="label-text">Priority</span></label>
				<select id="{id}-priority" class="select select-bordered w-full" bind:value={priority}>
					<option value="normal">Normal</option>
					<option value="critical">Critical</option>
				</select>
			</div>
		</div>

		<fieldset class="flex flex-col gap-2">
			<legend class="label-text mb-1">Send</legend>
			<label class="flex items-start gap-3">
				<input type="radio" class="radio mt-0.5" name="{id}-mode" value="sandbox" bind:group={mode} />
				<span><strong class="text-ink">Into the sandbox</strong><br /><span class="text-muted">Goes through the fake provider and shows up in the captured messages. Nobody is notified.</span></span>
			</label>
			<label class="flex items-start gap-3">
				<input type="radio" class="radio mt-0.5" name="{id}-mode" value="live" bind:group={mode} />
				<span><strong class="text-ink">Live</strong><br /><span class="text-muted">{sandboxOn ? 'The sandbox is on, so this is captured instead of sent.' : 'Goes through the real providers.'}</span></span>
			</label>
		</fieldset>

		{#if mode === 'live' && !sandboxOn}
			<Alert kind="error" title="This really notifies the user">
				The push is sent through APNs, FCM or web push and appears on the user's real devices.
				<label class="mt-2 flex items-start gap-3">
					<input type="checkbox" class="checkbox mt-0.5" bind:checked={confirmLive} aria-invalid={fields.confirm_live ? 'true' : undefined} />
					<span><strong>I understand and want to send this for real.</strong>{#if fields.confirm_live}<span class="block text-xs">{fields.confirm_live}</span>{/if}</span>
				</label>
			</Alert>
		{:else if mode === 'live'}
			<label class="flex items-start gap-3">
				<input type="checkbox" class="checkbox mt-0.5" bind:checked={confirmLive} />
				<span>Confirm sending live (the sandbox is on, so the message is only captured)</span>
			</label>
			{#if fields.confirm_live}<p class="text-xs text-club-dark">{fields.confirm_live}</p>{/if}
		{/if}

		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="flex justify-end">
			<button class="btn btn-sm {mode === 'live' ? 'btn-outline btn-error' : 'btn-outline'}" type="submit" disabled={busy}>{mode === 'live' ? 'Send live' : 'Send to sandbox'}</button>
		</div>

		{#if result}
			<div>
				<Alert kind={result.results.some((r) => r.status !== 'sent') ? 'warning' : 'success'}>
					{result.sandboxed ? 'Captured in the sandbox, nothing was sent.' : 'Sent through the real providers.'}
					{result.results.length ? '' : 'There was nothing to send to: the target has no enabled devices.'}
				</Alert>
				{#if result.results.length}
					<table class="table mt-2">
						<thead><tr><th>Channel</th><th>Provider</th><th>Target</th><th>Result</th><th class="text-right">Time</th></tr></thead>
						<tbody>
							{#each result.results as r, i (i)}
								<tr class="align-top">
									<td>{r.channel}</td>
									<td>{providerLabel(r.provider)}</td>
									<td class="font-mono text-xs break-all">{r.target_masked}</td>
									<td><span class="badge {STATUS_BADGE[r.status] ?? 'badge-ghost'}">{r.status}</span>{#if r.error}<div class="mt-1 font-mono text-xs break-words">{r.error}</div>{/if}</td>
									<td class="text-right font-mono text-xs">{r.duration_ms === null ? '—' : `${r.duration_ms} ms`}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{/if}
			</div>
		{/if}
	</form>
</div>
