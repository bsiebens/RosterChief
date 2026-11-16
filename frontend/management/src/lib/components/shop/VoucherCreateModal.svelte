<script lang="ts">
	import { enhance } from '$app/forms';
	import { Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import VoucherFields from '#lib/components/shop/VoucherFields.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { VoucherIn } from '#lib/shop.ts';

	/** Create one voucher, or several at once, from the list. Posts to the page's `?/create` action, which redirects to the new voucher (or back to the list for several). */
	let { open = $bindable(false), form }: { open: boolean; form: any } = $props();
	// A voucher expires a year from today unless told otherwise.
	const blank = (): VoucherIn => {
		const d = new Date();
		d.setFullYear(d.getFullYear() + 1);
		return { amount: '', expiry_date: d.toISOString().slice(0, 10), issued_to_id: null, notes: '', count: 1 };
	};
	let v = $state<VoucherIn>(blank());
	let tried = $state(false);
	let busy = $state(false);
	const mine = $derived(form?.op === 'create');
	const fields = $derived((mine && form?.fields ? form.fields : {}) as Record<string, string>);
	const errorText = $derived(mine && form?.status ? form.message || (form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed')) : '');
	const valid = () => Number(v.amount) > 0 && !!v.expiry_date && (v.count ?? 1) >= 1 && (v.count ?? 1) <= 200;
	$effect(() => {
		if (open) {
			v = blank();
			tried = false;
		}
	});
</script>

<Modal bind:open title={t('shop.vch.title_new')}>
	<form
		method="POST"
		action="?/create"
		class="grid gap-4"
		novalidate
		use:enhance={({ cancel }) => {
			if (!valid()) {
				tried = true;
				cancel();
				return;
			}
			busy = true;
			return async ({ result, update }) => {
				await update({ reset: false });
				busy = false;
				if (result.type === 'redirect') open = false;
			};
		}}>
		<input type="hidden" name="payload" value={JSON.stringify(v)} />
		{#if errorText}<Alert kind="error" title={t('shop.save_failed')}>{errorText}</Alert>{/if}
		<p class="text-sm text-muted">{t('shop.vch.new_help')}</p>
		<VoucherFields bind:v {fields} {tried} bulk />
		<p class="text-sm font-semibold">{(v.count ?? 1) > 1 ? t('shop.vch.will_create_n', { n: v.count ?? 1 }) : t('shop.vch.will_create_one')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Save size={16} /> {t('shop.vch.create')}</button>
		</div>
	</form>
</Modal>
