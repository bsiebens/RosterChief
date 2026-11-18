<script lang="ts">
	import { page } from '$app/state';
	import { t } from '#lib/i18n.svelte.ts';
	import { activePortalKey, ME_PAGES, portalItems, type PortalMode } from '#lib/portal-nav.ts';
	import type { Branding, Me } from '#lib/types.ts';
	import AppSwitcher from './AppSwitcher.svelte';
	import ClubCrest from './ClubCrest.svelte';

	/** The tablet navigation of the member and coach apps: the club crest, an icon rail and the app switcher. */
	let { me, branding, mode, shopEnabled = false }: { me: Me | null; branding: Branding; mode: PortalMode; shopEnabled?: boolean } = $props();
	const items = $derived(portalItems(mode, shopEnabled));
	const active = $derived(activePortalKey(page.url));
	const home = $derived(mode === 'parent' ? '/member' : '/coach');
</script>

<aside class="sticky top-0 z-30 flex h-dvh w-[84px] shrink-0 flex-col items-center gap-1 bg-sidebar-bg py-4">
	<a class="mb-3" href={home} aria-label={branding.name}><ClubCrest name={branding.name} logoUrl={branding.logo_url} size={34} /></a>
	<nav class="flex flex-col items-center gap-1" aria-label={t('shell.nav.label')}>
		{#each items as item (item.href)}
			{@const selected = active === item.key || (item.key === 'me' && ME_PAGES.includes(active))}
			<a class="rail-item {selected ? 'active' : ''}" href={item.href} aria-current={selected ? 'page' : undefined}>
				<item.icon size={22} class="shrink-0" aria-hidden="true" />
				{t(item.label)}
			</a>
		{/each}
	</nav>
	<div class="flex-1"></div>
	{#if me?.has_member_portal_role}<AppSwitcher member={mode === 'parent'} compact />{/if}
</aside>
