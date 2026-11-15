<script lang="ts">
	import { CircleCheck, Clock, Lock } from '@lucide/svelte';
	import { api } from '#lib/management-api.ts';
	import type { PublicField, PublicForm, PublicFormStatus } from '#lib/form-response.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';

	let { form, endpoint, ondone }: { form: PublicForm; endpoint: string; ondone?: () => void } = $props();

	// svelte-ignore state_referenced_locally
	let status = $state<PublicFormStatus>(form.state.status);
	let busy = $state(false);
	let done = $state(false);
	let errors = $state<Record<string, string>>({});
	let formError = $state('');

	const when = (iso: string | null) => (iso ? new Intl.DateTimeFormat(getLang(), { dateStyle: 'long', timeStyle: 'short' }).format(new Date(iso)) : '');
	const fid = (f: PublicField) => `f-${f.key}`;
	const hasErr = (f: PublicField) => !!errors[f.key];

	/** Consecutive fields sharing a section are shown together under its heading. */
	const groups = $derived.by(() => {
		const out: { section: string; fields: PublicField[] }[] = [];
		for (const f of form.fields) {
			const last = out[out.length - 1];
			if (last && last.section === f.section) last.fields.push(f);
			else out.push({ section: f.section, fields: [f] });
		}
		return out;
	});

	const accept = $derived(form.allowed_file_extensions.join(','));
	const fileHint = $derived(t('pubform.file_hint', { mb: form.max_file_mb, types: form.allowed_file_extensions.join(' ') }));

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (busy) return;
		busy = true;
		errors = {};
		formError = '';
		const body = new FormData(e.currentTarget as HTMLFormElement);
		const res = await api('POST', endpoint, { form: body });
		busy = false;
		if (res.ok) {
			done = true;
			if (ondone) ondone();
			else window.scrollTo({ top: 0 });
			return;
		}
		if (res.code === 'closed' || res.code === 'not_open_yet' || res.code === 'inactive') {
			status = res.code;
			return;
		}
		if (res.status === 429) formError = t('pubform.error.rate_limited');
		else if (res.status === 0) formError = t('pubform.error.network');
		else if (Object.keys(res.fields).length) {
			errors = res.fields;
			formError = t('pubform.fix_errors');
			queueMicrotask(() => document.querySelector<HTMLElement>('[aria-invalid="true"], [data-invalid="true"]')?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
		} else formError = t('pubform.error.generic');
	}
</script>

{#if done}
	<div class="card card-body items-center gap-2 py-10 text-center" role="status">
		<CircleCheck size={36} class="text-success" aria-hidden="true" />
		<p class="font-display text-xl font-bold text-ink">{t('pubform.thanks.title')}</p>
		<p class="text-muted">{t('pubform.thanks.body')}</p>
	</div>
{:else if status !== 'open'}
	<div class="card card-body items-center gap-2 py-10 text-center" role="status">
		{#if status === 'inactive'}<Lock size={32} class="text-dim" aria-hidden="true" />{:else}<Clock size={32} class="text-dim" aria-hidden="true" />{/if}
		<p class="font-display text-xl font-bold text-ink">{t(`pubform.state.${status}.title`)}</p>
		{#if status === 'not_open_yet'}
			<p class="text-muted">{form.state.opens_at ? t('pubform.state.not_open_yet.body', { date: when(form.state.opens_at) }) : t('pubform.state.not_open_yet.body_unknown')}</p>
		{:else}
			<p class="text-muted">{t(`pubform.state.${status}.body`)}</p>
		{/if}
	</div>
{:else}
	<form class="card card-body gap-5" onsubmit={submit} novalidate enctype="multipart/form-data">
		{#if form.description}<p class="whitespace-pre-line text-muted">{form.description}</p>{/if}
		{#if form.state.closes_at}<p class="font-mono text-xs text-muted">{t('pubform.closes', { date: when(form.state.closes_at) })}</p>{/if}
		{#if form.fields.some((f) => f.required)}<p class="text-xs text-muted">{t('pubform.required_hint')}</p>{/if}

		{#each groups as g, gi (gi)}
			<div class="grid gap-4">
				{#if g.section}<h2 class="border-b border-rule pb-1 font-display text-base font-bold text-ink">{g.section}</h2>{/if}
				{#each g.fields as f (f.id)}
					{@const describedBy = `${fid(f)}-help ${fid(f)}-err`}
					<div class="grid gap-1" data-invalid={hasErr(f) ? 'true' : undefined}>
						{#if f.type === 'checkbox'}
							<label class="flex items-start gap-3 text-sm" for={fid(f)}>
								<input id={fid(f)} name={f.key} type="checkbox" class="checkbox mt-0.5" aria-invalid={hasErr(f)} aria-describedby={describedBy} />
								<span>{f.label}{#if f.required}<span class="text-error" aria-hidden="true"> *</span>{/if}</span>
							</label>
						{:else if f.type === 'choice' || f.type === 'multichoice'}
							<fieldset class="grid gap-2" aria-describedby={describedBy}>
								<legend class="mb-1 text-sm font-medium">{f.label}{#if f.required}<span class="text-error" aria-hidden="true"> *</span>{/if}</legend>
								{#each f.options as o (o.value)}
									<label class="flex items-center gap-3 rounded-md border border-rule px-3 py-2 text-sm">
										<input type={f.type === 'choice' ? 'radio' : 'checkbox'} class={f.type === 'choice' ? 'radio' : 'checkbox'} name={f.key} value={o.value} aria-invalid={hasErr(f)} />
										<span>{o.label}</span>
									</label>
								{/each}
							</fieldset>
						{:else}
							<label class="text-sm font-medium" for={fid(f)}>{f.label}{#if f.required}<span class="text-error" aria-hidden="true"> *</span>{/if}</label>
							{#if f.type === 'textarea'}
								<textarea id={fid(f)} name={f.key} rows="4" class="textarea textarea-bordered w-full" class:input-error={hasErr(f)} aria-invalid={hasErr(f)} aria-describedby={describedBy}></textarea>
							{:else if f.type === 'file'}
								<input id={fid(f)} name={f.key} type="file" {accept} class="file-input file-input-bordered w-full" class:input-error={hasErr(f)} aria-invalid={hasErr(f)} aria-describedby={describedBy} />
								<p class="text-xs text-muted">{fileHint}</p>
							{:else}
								<input
									id={fid(f)}
									name={f.key}
									type={f.type === 'number' ? 'text' : f.type}
									inputmode={f.type === 'number' ? 'decimal' : undefined}
									autocomplete={f.type === 'email' ? 'email' : 'off'}
									class="input input-bordered w-full"
									class:input-error={hasErr(f)}
									aria-invalid={hasErr(f)}
									aria-describedby={describedBy}
								/>
							{/if}
						{/if}
						{#if f.help_text}<p id="{fid(f)}-help" class="text-xs text-muted">{f.help_text}</p>{/if}
						{#if errors[f.key]}<p id="{fid(f)}-err" class="text-sm text-error" role="alert">{errors[f.key]}</p>{/if}
					</div>
				{/each}
			</div>
		{/each}

		{#if formError}<p class="rounded-md bg-error/10 px-3 py-2 text-sm text-error" role="alert">{formError}</p>{/if}
		<button type="submit" class="btn btn-primary w-full sm:w-auto sm:self-start" disabled={busy}>{busy ? t('pubform.submitting') : t('pubform.submit')}</button>
	</form>
{/if}
