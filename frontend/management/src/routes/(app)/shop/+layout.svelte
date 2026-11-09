<script lang="ts">
	import { CircleAlert } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	let { data, children } = $props();
	const closed = $derived(!!data.shopStatus && !data.shopStatus.open);
</script>

<!-- Full-bleed strip under the page header, so it is clear to everyone working in the shop that members cannot order right now.
     The -mt-6/mb-2 pair lines up with the full-bleed list pages (-my-6) so their filter bar sits directly under it. -->
{#if closed}
	<div role="status" class="-mx-7 -mt-6 mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-danger-border bg-danger-bg px-7 py-2 text-sm">
		<CircleAlert size={16} class="shrink-0 text-danger" aria-hidden="true" />
		<strong class="font-display tracking-[.06em] text-danger uppercase">{t('shop.banner.closed')}</strong>
		<span class="text-ink">{t('shop.banner.closed_sub')}</span>
		{#if data.shopStatus?.message}<span class="font-semibold text-ink">“{data.shopStatus.message}”</span>{/if}
		{#if data.me?.can_manage_shop_orders}<a class="btn btn-outline btn-error btn-sm ml-auto" href="/shop/settings">{t('shop.banner.settings')}</a>{/if}
	</div>
{/if}
{@render children()}
