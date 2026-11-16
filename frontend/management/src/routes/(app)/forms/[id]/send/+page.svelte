<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, CalendarClock, Send, Users, ListChecks } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EventFormAudience from '#lib/components/events/EventFormAudience.svelte';
	import Field from '#lib/components/Field.svelte';
	import { blankState } from '#lib/events/form-types.ts';
	import type { SendIn } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { wallToIso } from '#lib/news.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const item = $derived(data.form);
	const uid = $props.id();

	// The audience is edited in the same state the event form uses, so its pickers can be reused as they are.
	let s = $state(blankState({ notify: true }));
	let opens = $state('');
	let closes = $state('');
	let max = $state('');
	// svelte-ignore state_referenced_locally
	let isPublic = $state(false);
	let notify = $state(true);
	let tried = $state(false);
	let busy = $state(false);

	const opensIso = $derived(wallToIso(opens));
	const closesIso = $derived(wallToIso(closes));
	const windowBad = $derived(!!opensIso && !!closesIso && closesIso <= opensIso);
	const maxNum = $derived(max.trim() === '' ? null : Math.floor(Number(max)));
	const maxBad = $derived(maxNum !== null && (!Number.isFinite(maxNum) || maxNum < 1));
	const noAudience = $derived(!s.club_wide && !s.team_ids.length && !s.group_ids.length && !s.invited.length);
	// A login-only form can never be filled in through a public link.
	const publicOn = $derived(isPublic && !item.login_required);

	const payload = $derived(
		JSON.stringify({
			team_ids: s.team_ids,
			group_ids: s.group_ids,
			club_wide: s.club_wide,
			invited_member_ids: s.invited.map((m) => m.id),
			excluded_member_ids: s.excluded.map((m) => m.id),
			opens_at: opensIso || null,
			closes_at: closesIso || null,
			max_submissions_per_user: maxNum,
			is_public: publicOn,
			notify
		} satisfies SendIn)
	);

	const fields = $derived(form?.fields ?? {});
	const errorText = $derived(form?.status ? form.message || (form.status === 403 ? t('forms.err.forbidden') : t('forms.err.failed')) : '');

	usePageHeader(() => ({ title: t('forms.send.title'), context: data.form.title, actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/forms/{item.id}"><ArrowLeft size={16} /> {t('forms.send.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('forms.send.title'))}</title></svelte:head>

<form
	method="POST"
	action="?/save"
	class="grid gap-5"
	novalidate
	use:enhance={({ cancel }) => {
		if (windowBad || maxBad) {
			tried = true;
			cancel();
			return;
		}
		busy = true;
		return async ({ update }) => {
			await update({ reset: false });
			busy = false;
		};
	}}>
	<input type="hidden" name="payload" value={payload} />
	{#if errorText}<Alert kind="error" title={t('forms.send.failed')}>{errorText}</Alert>{/if}
	{#if !item.is_active}<Alert kind="warning">{t('forms.send.inactive_form')}</Alert>{/if}
	{#if !item.fields.some((f) => f.is_active)}<Alert kind="warning">{t('forms.send.no_fields')} <a class="link font-semibold underline" href="/forms/{item.id}">{t('forms.send.edit_form')}</a></Alert>{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<section class="card card-body card-hl" aria-labelledby="{uid}-aud">
				<header class="mb-3 grid gap-0.5">
					<h2 id="{uid}-aud" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Users size={16} aria-hidden="true" /> {t('forms.send.card.audience')}</h2>
					<p class="text-xs text-muted">{t('forms.send.card.audience_desc')}</p>
				</header>
				<EventFormAudience bind:s options={data.options} {fields} />
				{#if tried && noAudience && !publicOn}<span class="mt-3 block text-xs text-warn-text">{t('forms.send.no_audience')}</span>{/if}
			</section>

			<section class="card card-body card-hl" aria-labelledby="{uid}-win">
				<header class="mb-3 grid gap-0.5">
					<h2 id="{uid}-win" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><CalendarClock size={16} aria-hidden="true" /> {t('forms.send.card.window')}</h2>
					<p class="text-xs text-muted">{t('forms.send.card.window_desc')}</p>
				</header>
				<div class="grid gap-4 md:grid-cols-2">
					<Field label={t('forms.send.opens')} type="datetime-local" bind:value={opens} help={t('forms.send.opens_help')} error={fields.opens_at} />
					<Field label={t('forms.send.closes')} type="datetime-local" bind:value={closes} help={t('forms.send.closes_help')} error={fields.closes_at || (windowBad ? t('forms.err.window') : '')} />
					<Field label={t('forms.send.max')} type="number" min="1" step="1" bind:value={max} help={t('forms.send.max_help')} error={fields.max_submissions_per_user || (maxBad ? t('forms.err.max') : '')} />
				</div>
			</section>
		</div>

		<div class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body card-hl gap-3" aria-labelledby="{uid}-opt">
				<h2 id="{uid}-opt" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListChecks size={16} aria-hidden="true" /> {t('forms.send.card.options')}</h2>
				<label class="flex items-start gap-3 rounded-lg border border-line p-3 {item.login_required ? 'opacity-60' : 'cursor-pointer'}">
					<input type="checkbox" class="checkbox mt-0.5" bind:checked={isPublic} disabled={item.login_required} aria-describedby="{uid}-pub-help" />
					<span class="grid gap-0.5">
						<span class="text-sm font-semibold text-ink">{t('forms.send.public')}</span>
						<span id="{uid}-pub-help" class="text-xs text-muted">{item.login_required ? t('forms.send.public_login') : t('forms.send.public_help')}</span>
					</span>
				</label>
				<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
					<input type="checkbox" class="checkbox mt-0.5" bind:checked={notify} />
					<span class="grid gap-0.5">
						<span class="text-sm font-semibold text-ink">{t('forms.send.notify')}</span>
						<span class="text-xs text-muted">{t('forms.send.notify_help')}</span>
					</span>
				</label>
				<div class="grid gap-2">
					<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy || !item.is_active}><Send size={16} /> {t('forms.send.go')}</button>
					<a class="btn btn-outline w-full" href="/forms/{item.id}">{t('common.cancel')}</a>
				</div>
			</section>
		</div>
	</div>
</form>
