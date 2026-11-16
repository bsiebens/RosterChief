<script lang="ts">
	import { enhance } from '$app/forms';
	import { Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import DiscountFields from '#lib/components/shop/DiscountFields.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { emptyDiscount, type DiscountIn } from '#lib/shop.ts';

	/** Create a discount from the list. Posts to the page's `?/create` action, which redirects to the new discount. `form` is the page's action result (for the errors). */
	let { open = $bindable(false), form }: { open: boolean; form: any } = $props();
	let v = $state<DiscountIn>(emptyDiscount());
	let tried = $state(false);
	let busy = $state(false);
	const mine = $derived(form?.op === 'create');
	const fields = $derived((mine && form?.fields ? form.fields : {}) as Record<string, string>);
	const errorText = $derived(mine && form?.status ? form.message || (form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed')) : '');
	const valid = () => !!v.name.trim() && !!v.code.trim() && Number(v.discount_amount) > 0 && (v.discount_type !== 'percentage' || Number(v.discount_amount) <= 100) && !(v.valid_from && v.valid_until && v.valid_until < v.valid_from);
	$effect(() => {
		if (open) {
			v = emptyDiscount();
			tried = false;
		}
	});
</script>

<Modal bind:open title={t('shop.disc.title_new')}>
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
		<p class="text-sm text-muted">{t('shop.disc.new_help')}</p>
		<DiscountFields bind:v {fields} {tried} />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Save size={16} /> {t('shop.disc.create')}</button>
		</div>
	</form>
</Modal>
