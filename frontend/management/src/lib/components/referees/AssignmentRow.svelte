<script lang="ts">
	import { untrack } from 'svelte';
	import { AlertTriangle, Check, Trash2 } from '@lucide/svelte';
	import { api } from '#lib/management-api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { eur, fmtRange, ROW_GRID, type Assignment, type RefKind, type TypeRef } from '#lib/referees.ts';

	/** One referee / official on a game: type, fee line (fee, km, rate per km, "no fee") with the total, save and remove. */
	let { row, kind, types, onchanged }: { row: Assignment; kind: RefKind; types: TypeRef[]; onchanged: () => void } = $props();

	let fee = $state('');
	let km = $state('');
	let rate = $state('');
	let noFee = $state(false);
	let typeId = $state('');
	let name = $state('');
	let busy = $state(false);
	let error = $state('');
	let confirming = $state(false);

	// Re-seed the inputs only when the saved values change (a save elsewhere on the page must not wipe unsaved edits here).
	const sig = $derived([row.fee, row.km, row.km_rate, row.no_fee, row.type_id, row.name].join('|'));
	$effect(() => {
		void sig;
		untrack(() => {
			fee = row.fee;
			km = row.km ?? '';
			rate = row.km_rate ?? '';
			noFee = row.no_fee;
			typeId = row.type_id ?? '';
			name = row.name;
		});
	});

	const num = (v: string | null) => (v === '' || v === null ? '' : String(Number(v)));
	const dirty = $derived(num(fee) !== num(row.fee) || num(km) !== num(row.km) || num(rate) !== num(row.km_rate) || noFee !== row.no_fee || typeId !== (row.type_id ?? '') || name.trim() !== row.name);
	const live = $derived(noFee ? 0 : Number(fee || 0) + Number(km || 0) * Number(rate || 0));
	const money = (v: string) => (v === '' ? null : v);

	async function save() {
		busy = true;
		error = '';
		const body: Record<string, unknown> = { fee: money(fee), km: money(km), km_rate: money(rate), no_fee: noFee, referee_type_id: typeId || null };
		if (row.is_external) body.name = name;
		const res = await api('PATCH', `/referee-management/assignments/${kind}/${row.id}`, { body });
		busy = false;
		if (res.ok) onchanged();
		else error = Object.values(res.fields)[0] || res.message || t('referees.save_failed');
	}

	async function remove() {
		busy = true;
		const res = await api('DELETE', `/referee-management/assignments/${kind}/${row.id}`);
		busy = false;
		if (res.ok) onchanged();
		else {
			confirming = false;
			error = res.message || t('referees.save_failed');
		}
	}
</script>

<div class="grid gap-1.5 border-t border-rule px-4 py-2.5 first:border-t-0 {row.fee_pending && !dirty ? 'bg-row-warn' : ''}">
	<div class="grid items-center gap-x-2 gap-y-2 {ROW_GRID}">
		<div class="min-w-0">
			{#if row.is_external}
				<input class="input input-sm w-full font-semibold" aria-label={t('referees.rename')} bind:value={name} />
			{:else}
				<a class="block truncate font-semibold hover:underline" href="/members/{row.member_id}">{row.name}</a>
			{/if}
		</div>

		{#if types.length}
			<select class="select select-sm w-full" aria-label={t('referees.col.type')} bind:value={typeId}>
				<option value="">{t('referees.no_type')}</option>
				{#each types as ty (ty.id)}<option value={ty.id}>{ty.name}</option>{/each}
			</select>
		{:else}
			<span></span>
		{/if}

		<label class="flex items-center gap-1.5 text-xs text-muted">
			<input type="checkbox" class="checkbox checkbox-sm" bind:checked={noFee} />
			{t('referees.no_fee')}
		</label>

		<input class="input input-sm w-full text-right font-mono" type="number" min="0" step="0.01" inputmode="decimal" aria-label={t('referees.col.fee')} placeholder={t('referees.col.fee')} disabled={noFee} bind:value={fee} />
		<input class="input input-sm w-full text-right font-mono" type="number" min="0" step="0.1" inputmode="decimal" aria-label={t('referees.col.km')} placeholder={t('referees.col.km')} disabled={noFee} bind:value={km} />
		<input class="input input-sm w-full text-right font-mono" type="number" min="0" step="0.0001" inputmode="decimal" aria-label={t('referees.col.rate')} placeholder={t('referees.col.rate')} disabled={noFee} bind:value={rate} />

		<div class="text-right font-mono text-sm font-semibold">{eur(live)}</div>

		<div class="flex items-center justify-end gap-1">
			{#if dirty}
				<button class="btn btn-primary btn-sm gap-1 px-2.5" type="button" disabled={busy} onclick={save}><Check size={13} aria-hidden="true" /> {busy ? t('referees.saving') : t('referees.save_row')}</button>
			{/if}
			{#if confirming}
				<button class="btn btn-error btn-sm" type="button" disabled={busy} onclick={remove}>{t('common.yes')}</button>
				<button class="btn btn-outline btn-sm" type="button" onclick={() => (confirming = false)}>{t('common.no')}</button>
			{:else}
				<button class="btn btn-ghost btn-square btn-sm" type="button" aria-label={t('referees.remove_named', { name: row.name })} title={t('referees.remove')} onclick={() => (confirming = true)}><Trash2 size={14} aria-hidden="true" /></button>
			{/if}
		</div>
	</div>
	<div class="flex flex-wrap items-center gap-1.5 empty:hidden">
		{#if row.is_external}<span class="badge badge-xs badge-outline">{t('referees.external')}</span>{/if}
		{#if row.fee_pending && !dirty}<span class="badge badge-xs badge-warning">{t('referees.pending')}</span>{/if}
		{#each row.conflicts as c (c.event_id + c.role)}
			<span class="badge badge-xs badge-warning gap-1 normal-case" title={t('referees.conflict.role.' + c.role)}><AlertTriangle size={10} aria-hidden="true" />{c.title} · {fmtRange(c)}</span>
		{/each}
	</div>
	{#if confirming}<p class="text-right text-xs text-muted">{t('referees.remove_confirm')}</p>{/if}
	{#if error}<p class="text-xs text-error" role="alert">{error}</p>{/if}
</div>
