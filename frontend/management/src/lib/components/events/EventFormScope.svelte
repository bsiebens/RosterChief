<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import type { EditScope } from '#lib/events/types.ts';
	import { SCOPES, scopeBlocked, type ScopeLimits } from '#lib/events/form-types.ts';

	/**
	 * Which events of a series a change or a delete applies to (the API's `scope`). `limits` (edit only) disables what
	 * the API would refuse: a new recurrence needs "all"; a new date or the friendly flag only "this".
	 */
	let { scope = $bindable('this'), purpose, limits = { recurrence: false, dateMoved: false, friendly: false } }: { scope: EditScope; purpose: 'edit' | 'delete'; limits?: ScopeLimits } = $props();

	const uid = $props.id();
	const blocked = (s: EditScope, l: ScopeLimits) => (purpose === 'edit' ? scopeBlocked(s, l) : '');
	const impossible = $derived(purpose === 'edit' && SCOPES.every((s) => blocked(s, limits)));

	$effect.pre(() => {
		if (blocked(scope, limits)) scope = SCOPES.find((s) => !blocked(s, limits)) ?? scope;
	});
</script>

<fieldset class="grid gap-2">
	<legend class="mb-2 text-sm text-slate">{purpose === 'edit' ? t('eventform.scope.intro_edit') : t('eventform.scope.intro_delete')}</legend>
	{#each SCOPES as s (s)}
		{@const why = blocked(s, limits)}
		<label class="flex items-start gap-3 rounded-lg border p-3 {why ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'} {scope === s && !why ? 'border-primary bg-subhead' : 'border-line'}">
			<input type="radio" class="mt-1 size-4 accent-club" name="{uid}-scope" value={s} bind:group={scope} disabled={!!why} />
			<span class="grid gap-0.5">
				<span class="text-sm font-semibold text-ink">{t(`eventform.scope.${s}`)}</span>
				<span class="text-xs text-muted">{t(`eventform.scope.${s}_${purpose}_help`)}</span>
				{#if why}<span class="text-xs font-semibold text-warn-text">{why}</span>{/if}
			</span>
		</label>
	{/each}
	{#if purpose === 'edit'}<p class="mt-1 text-xs text-muted">{t('eventform.scope.times_note')}</p>{/if}
	{#if impossible}<p class="text-xs font-semibold text-error">{t('eventform.scope.impossible')}</p>{/if}
</fieldset>
