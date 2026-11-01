<script lang="ts">
	import { DAY_NAMES, build, describe, detect, type Preset } from '#lib/cron.ts';
	import type { Cron, PeriodicJob } from '#lib/dashboard.ts';
	import Alert from './Alert.svelte';
	import Modal from './Modal.svelte';

	let { job = $bindable(null), onsave }: { job: PeriodicJob | null; onsave: (name: string, cron: Cron) => Promise<string | null> } = $props();

	let open = $state(false);
	let preset = $state<Preset>('custom');
	let every = $state('5');
	let minute = $state('0');
	let time = $state('04:00');
	let weekday = $state('1');
	let day = $state('1');
	let custom = $state<Cron>({ minute: '*', hour: '*', day_of_month: '*', month_of_year: '*', day_of_week: '*' });
	let error = $state('');
	let busy = $state(false);

	// Opening the dialog for a job loads its current schedule into the inputs.
	$effect(() => {
		if (job?.schedule) {
			const d = detect(job.schedule);
			({ preset, every, minute, time, weekday, day } = d);
			custom = { minute: job.schedule.minute, hour: job.schedule.hour, day_of_month: job.schedule.day_of_month, month_of_year: job.schedule.month_of_year, day_of_week: job.schedule.day_of_week };
			error = '';
			open = true;
		}
	});

	const cron = $derived(build({ preset, every, minute, time, weekday, day }, custom));
	// Switching to "custom" starts from whatever the current preset produced, so nothing is lost.
	function choose(next: Preset) {
		if (next === 'custom') custom = { ...cron };
		preset = next;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!job) return;
		busy = true;
		error = (await onsave(job.name, cron)) ?? '';
		busy = false;
		if (!error) open = false;
	}

	const fields = [
		['minute', 'Minute', '0–59'],
		['hour', 'Hour', '0–23'],
		['day_of_month', 'Day of month', '1–31'],
		['month_of_year', 'Month', '1–12'],
		['day_of_week', 'Day of week', '0–6, Sun=0']
	] as const;
</script>

<Modal bind:open title={job ? `Schedule · ${job.label}` : 'Schedule'} onclose={() => (job = null)}>
	{#if job}
		<form class="flex flex-col gap-4" onsubmit={save}>
			{#if error}<Alert kind="error">{error}</Alert>{/if}

			<div class="form-control">
				<label class="label" for="preset"><span class="label-text">Run</span></label>
				<select id="preset" class="select select-bordered" value={preset} onchange={(e) => choose(e.currentTarget.value as Preset)}>
					<option value="minutes">Every few minutes</option>
					<option value="hourly">Every hour</option>
					<option value="daily">Every day</option>
					<option value="weekly">Every week</option>
					<option value="monthly">Every month</option>
					<option value="custom">Custom (cron)</option>
				</select>
			</div>

			{#if preset === 'minutes'}
				<div class="form-control">
					<label class="label" for="every"><span class="label-text">Every how many minutes</span></label>
					<input id="every" class="input input-bordered font-mono" type="number" min="1" max="59" bind:value={every} />
				</div>
			{:else if preset === 'hourly'}
				<div class="form-control">
					<label class="label" for="minute"><span class="label-text">At minute</span></label>
					<input id="minute" class="input input-bordered font-mono" type="number" min="0" max="59" bind:value={minute} />
				</div>
			{:else if preset === 'daily' || preset === 'weekly' || preset === 'monthly'}
				<div class="grid grid-cols-2 gap-3">
					{#if preset === 'weekly'}
						<div class="form-control">
							<label class="label" for="weekday"><span class="label-text">Day</span></label>
							<select id="weekday" class="select select-bordered" bind:value={weekday}>
								{#each DAY_NAMES as name, i (name)}<option value={String(i)}>{name}</option>{/each}
							</select>
						</div>
					{:else if preset === 'monthly'}
						<div class="form-control">
							<label class="label" for="day"><span class="label-text">Day of the month</span></label>
							<input id="day" class="input input-bordered font-mono" type="number" min="1" max="31" bind:value={day} />
						</div>
					{/if}
					<div class="form-control">
						<label class="label" for="time"><span class="label-text">At</span></label>
						<input id="time" class="input input-bordered font-mono" type="time" required bind:value={time} />
					</div>
				</div>
			{:else}
				<div class="grid grid-cols-5 gap-2">
					{#each fields as [key, label, hint] (key)}
						<div class="form-control">
							<label class="label" for={key}><span class="label-text text-xs">{label}</span></label>
							<input id={key} class="input input-bordered px-2 text-center font-mono" bind:value={custom[key]} placeholder="*" spellcheck="false" autocomplete="off" />
							<span class="label-text-alt mt-1 text-center text-[10px]">{hint}</span>
						</div>
					{/each}
				</div>
				<p class="text-xs text-muted">Numbers with <code class="font-mono">*</code>, <code class="font-mono">,</code>, <code class="font-mono">-</code> and <code class="font-mono">/</code>, for example <code class="font-mono">*/15</code> or <code class="font-mono">1,15</code>.</p>
			{/if}

			<div class="rounded border border-line bg-subhead px-3 py-2 text-sm">
				<span class="text-muted">Will run:</span> <strong class="text-ink">{describe(cron)}</strong>
				{#if job.schedule?.timezone}<span class="font-mono text-[11px] text-muted"> · {job.schedule.timezone}</span>{/if}
			</div>

			<div class="modal-action mt-0">
				<button type="button" class="btn btn-outline" onclick={() => (open = false)}>Cancel</button>
				<button type="submit" class="btn btn-primary" disabled={busy}>Save schedule</button>
			</div>
		</form>
	{/if}
</Modal>
