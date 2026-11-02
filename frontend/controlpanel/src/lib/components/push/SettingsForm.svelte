<script lang="ts">
	import { untrack } from 'svelte';
	import { platform } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import type { PushConfigSettings } from '#lib/push.ts';
	import { fieldError } from '#lib/users.ts';

	let { settings, onsaved, saved = $bindable(false) }: { settings: PushConfigSettings; onsaved: () => void; saved?: boolean } = $props();

	const NUMBERS: { key: keyof PushConfigSettings; label: string; help?: string }[] = [
		{ key: 'outbox_max_attempts', label: 'Outbox max attempts', help: 'How often an outbox entry is retried before it is marked failed.' },
		{ key: 'sent_retention_days', label: 'Keep sent deliveries (days)' },
		{ key: 'failed_retention_days', label: 'Keep failed deliveries (days)' },
		{ key: 'stale_device_days', label: 'Device is stale after (days)', help: 'Not seen for this long.' },
		{ key: 'disabled_device_retention_days', label: 'Delete disabled devices after (days)' },
		{ key: 'unseen_device_retention_days', label: 'Delete never-seen devices after (days)' },
		{ key: 'degraded_below_percent', label: 'Degraded below success rate (%)' },
		{ key: 'down_below_percent', label: 'Down below success rate (%)', help: 'Must be lower than the degraded threshold.' },
		{ key: 'health_min_requests', label: 'Minimum requests for a health verdict', help: 'Below this a provider is shown as quiet.' }
	];

	const emailsText = (v: string[] | string) => (Array.isArray(v) ? v.join(', ') : (v ?? ''));
	const fromSettings = (s: PushConfigSettings) => ({
		delays: s.retry_delays_minutes.join(', '),
		emails: emailsText(s.alert_emails),
		nums: Object.fromEntries(NUMBERS.map((n) => [n.key, String(s[n.key])])) as Record<string, string>
	});

	let f = $state(untrack(() => fromSettings(settings)));
	let busy = $state(false);
	let error = $state('');
	let fields = $state<Record<string, string>>({});

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		saved = false;
		fields = {};
		const delays = f.delays
			.split(',')
			.map((x) => x.trim())
			.filter(Boolean);
		if (!delays.length || delays.length > 10 || delays.some((d) => !/^\d+$/.test(d) || Number(d) < 1 || Number(d) > 1440)) {
			fields.retry_delays_minutes = 'Enter 1 to 10 whole numbers of minutes between 1 and 1440, separated by commas.';
		}
		const body: Record<string, unknown> = { retry_delays_minutes: delays.map(Number) };
		for (const n of NUMBERS) {
			const t = f.nums[n.key].trim();
			if (!/^\d+$/.test(t)) fields[n.key] = 'Enter a whole number.';
			else body[n.key] = Number(t);
		}
		const list = f.emails
			.split(/[,\s;]+/)
			.map((x) => x.trim())
			.filter(Boolean);
		body.alert_emails = Array.isArray(settings.alert_emails) ? list : list.join(', ');
		if (Object.keys(fields).length) return;
		busy = true;
		const r = await platform<PushConfigSettings>('PATCH', '/push/config', body);
		busy = false;
		if (!r.ok) {
			for (const k of Object.keys(body)) {
				const m = fieldError(r.fields, k);
				if (m) fields[k] = m;
			}
			return (error = Object.keys(fields).length ? 'Some settings need attention.' : (r.message ?? 'The settings could not be saved.'));
		}
		if (r.data) f = fromSettings(r.data);
		saved = true;
		onsaved();
	}
</script>

<form class="flex flex-col gap-4" onsubmit={submit} novalidate aria-label="Push settings">
	{#if error}<Alert kind="error">{error}</Alert>{/if}

	<div class="grid gap-4 lg:grid-cols-3">
	<fieldset class="card" disabled={busy}>
		<div class="card-body gap-3">
			<legend class="card-title float-left w-full">Retries and alerts</legend>
			<div class="grid gap-3">
				<Field label="Retry delays (minutes)" bind:value={f.delays} error={fields.retry_delays_minutes} help="Comma separated, for example 1, 5, 15, 60. One retry per delay." />
				<Field label="Alert recipients" bind:value={f.emails} error={fields.alert_emails} help="Email addresses, separated by commas. They are mailed when a provider is down." />
				{#each NUMBERS.slice(0, 1) as n (n.key)}<Field label={n.label} inputmode="numeric" bind:value={f.nums[n.key]} error={fields[n.key]} help={n.help} />{/each}
			</div>
		</div>
	</fieldset>

	<fieldset class="card" disabled={busy}>
		<div class="card-body gap-3">
			<legend class="card-title float-left w-full">Health thresholds</legend>
			<div class="grid gap-3">
				{#each NUMBERS.slice(6) as n (n.key)}<Field label={n.label} inputmode="numeric" bind:value={f.nums[n.key]} error={fields[n.key]} help={n.help} />{/each}
			</div>
		</div>
	</fieldset>

	<fieldset class="card" disabled={busy}>
		<div class="card-body gap-3">
			<legend class="card-title float-left w-full">Retention and devices</legend>
			<div class="grid gap-3">
				{#each NUMBERS.slice(1, 6) as n (n.key)}<Field label={n.label} inputmode="numeric" bind:value={f.nums[n.key]} error={fields[n.key]} help={n.help} />{/each}
			</div>
		</div>
	</fieldset>
	</div>

	<div class="flex justify-end"><button class="btn btn-outline btn-sm" type="submit" disabled={busy}>Save settings</button></div>
</form>
