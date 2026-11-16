<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import CriteriaFields from '#lib/components/evaluations/CriteriaFields.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const checklist = $derived(data.checklist);
	let busy = $state(false);
	let picked = $state('');
	// A new form (and so empty inputs) after every successful save.
	let round = $state(0);
	const errs = $derived((form && 'fields' in form ? form.fields : {}) as Record<string, string>);
	const general = $derived(form && 'message' in form && form.message && !Object.keys(errs).some((k) => k !== '__all__') ? String(form.message) : '');
	const saved = $derived(form && 'saved' in form ? String(form.saved) : '');
	const current = $derived(data.seasons.find((s) => s.is_current)?.id ?? data.seasons[0]?.id ?? '');

	usePageHeader(() => ({ title: t('eval.fill.title'), context: data.checklist.name, actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href={data.player ? `/members/${data.player.id}?tab=evaluations` : '/evaluations'}><ArrowLeft size={16} /> {t('eval.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('eval.fill.title'))}</title></svelte:head>

<div class="grid gap-4">
	{#if saved}<Alert kind="success">{t('eval.fill.saved', { name: saved })}</Alert>{/if}
	{#if !checklist.is_active}<Alert kind="warning">{t('eval.fill.inactive')}</Alert>{/if}
	{#if general}<Alert kind="error">{general}</Alert>{/if}

	{#key round}
		<form
			method="POST"
			class="grid gap-5"
			novalidate
			use:enhance={() => {
				busy = true;
				return async ({ update, result }) => {
					await update({ reset: false });
					busy = false;
					if (result.type === 'success') {
						picked = '';
						round++;
						window.scrollTo({ top: 0 });
					} else if (result.type === 'failure') queueMicrotask(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
				};
			}}>
			<div class="grid items-start gap-5 xl:grid-cols-3">
				<div class="grid content-start gap-5 xl:col-span-2">
					<section class="card card-body gap-5">
			<div class="grid gap-4 md:grid-cols-2">
				<div>
					{#if data.player}
						<p class="label-text mb-1">{t('eval.fill.player')}</p>
						<p class="rounded-lg border border-line bg-surface px-3 py-2 font-semibold text-ink">{data.player.name}</p>
						<input type="hidden" name="player_id" value={data.player.id} />
					{:else}
						<EntityPicker kind="member" name="player_id" label={t('eval.fill.player')} bind:value={picked} error={errs.player_id} />
					{/if}
				</div>
				<div class="form-control">
					<label class="label" for="season"><span class="label-text">{t('eval.fill.season')}</span></label>
					<select id="season" name="season_id" class="select select-bordered w-full">
						{#each data.seasons as s (s.id)}<option value={s.id} selected={s.id === current}>{s.name}</option>{/each}
					</select>
					{#if errs.season_id}<span class="mt-1 block text-xs text-error">{errs.season_id}</span>{/if}
				</div>
			</div>
			{#if checklist.fields.some((f) => f.required && f.is_active)}<p class="text-xs text-muted">{t('eval.fill.required_hint')}</p>{/if}
			<CriteriaFields fields={checklist.fields} errors={errs} />
			<label class="grid gap-1 text-sm font-semibold">{t('eval.public_note')}<textarea class="textarea textarea-bordered w-full font-normal" name="public_note" rows="3" maxlength="2000"></textarea><span class="text-xs font-normal text-muted">{t('eval.public_note_help')}</span></label>
			{#if errs.__all__ || errs.checklist_id}<p class="text-sm text-error">{errs.__all__ || errs.checklist_id}</p>{/if}
					</section>
				</div>
				<div class="grid content-start gap-5 xl:sticky xl:top-20">
					<section class="card card-body gap-3">
						<div class="grid gap-2">
							<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy || !checklist.is_active}><Save size={16} /> {t('eval.fill.submit')}</button>
							<a class="btn btn-outline w-full" href={data.player ? `/members/${data.player.id}?tab=evaluations` : '/evaluations'}>{t('common.cancel')}</a>
						</div>
					</section>
				</div>
			</div>
		</form>
	{/key}
</div>
