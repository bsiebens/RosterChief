<script lang="ts">
	import { Check, ChevronsUpDown, LayoutDashboard, UsersRound } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * Which side of the club the person is on: the member side or management. A trigger styled like the account menu, with a
	 * popover listing both. `tone="sidebar"` sits on the dark sidebar, `tone="light"` on the white/club-coloured mobile masthead.
	 * Management is the full back office on a desktop, the compact coach view on a phone or tablet.
	 */
	let { member, tone = 'sidebar' }: { member: boolean; tone?: 'sidebar' | 'light' } = $props();

	let menu: HTMLDetailsElement | undefined = $state();
	let desktop = $state(false);
	const close = () => menu && (menu.open = false);
	$effect(() => {
		const query = matchMedia('(min-width: 80rem) and (pointer: fine)');
		desktop = query.matches;
		const update = () => (desktop = query.matches);
		query.addEventListener('change', update);
		return () => query.removeEventListener('change', update);
	});

	const options = $derived([
		{ key: 'member', href: '/parent', label: t('portal.parent'), hint: t('portal.parent_hint'), icon: UsersRound, current: member },
		{ key: 'management', href: desktop ? '/' : '/coach', label: t('portal.management'), hint: t('portal.management_hint'), icon: LayoutDashboard, current: !member }
	]);
	const current = $derived(options.find((o) => o.current) ?? options[0]);
</script>

<svelte:window onclick={(e) => menu?.open && !menu.contains(e.target as Node) && close()} onkeydown={(e) => e.key === 'Escape' && close()} />

<details class="app-switcher relative {tone}" bind:this={menu}>
	<summary class="app-switcher-trigger" aria-label={t('portal.views')}>
		<span class="app-switcher-tile"><current.icon size={16} aria-hidden="true" /></span>
		<span class="min-w-0 flex-1">
			<span class="app-switcher-eyebrow">{t('portal.views')}</span>
			<span class="app-switcher-name">{current.label}</span>
		</span>
		<ChevronsUpDown size={15} class="shrink-0 opacity-60" aria-hidden="true" />
	</summary>
	<div class="app-switcher-menu" role="menu">
		{#each options as option (option.key)}
			<a class="app-switcher-option" href={option.href} role="menuitem" aria-current={option.current ? 'true' : undefined} onclick={close}>
				<span class="app-switcher-tile"><option.icon size={16} aria-hidden="true" /></span>
				<span class="min-w-0 flex-1">
					<span class="block text-[13px] font-semibold">{option.label}</span>
					<span class="block text-[11px] leading-snug opacity-65">{option.hint}</span>
				</span>
				{#if option.current}<Check size={15} class="shrink-0" aria-hidden="true" />{/if}
			</a>
		{/each}
	</div>
</details>

<style>
	.app-switcher {
		--sw-fg: var(--color-sidebar-fg);
		--sw-bg: var(--color-sidebar-bg);
		--sw-hover: var(--color-sidebar-hairline);
		--sw-tile: var(--color-sidebar-chip);
		--sw-border: var(--color-sidebar-hairline);
	}
	.app-switcher.light {
		--sw-fg: var(--color-ink);
		--sw-bg: var(--portal-panel, #ffffff);
		--sw-hover: var(--color-subhead);
		--sw-tile: var(--color-subhead);
		--sw-border: var(--color-line);
	}

	.app-switcher-trigger {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		min-height: 2.75rem;
		padding: 0.375rem 0.625rem;
		border: 1px solid var(--sw-border);
		border-radius: 0.625rem;
		color: var(--sw-fg);
		cursor: pointer;
		list-style: none;
	}
	.app-switcher-trigger::-webkit-details-marker { display: none; }
	.app-switcher-trigger:hover, .app-switcher[open] .app-switcher-trigger { background: var(--sw-hover); }

	.app-switcher-tile {
		display: grid;
		flex-shrink: 0;
		place-items: center;
		width: 1.875rem;
		height: 1.875rem;
		border-radius: 0.5rem;
		background: var(--sw-tile);
	}

	.app-switcher-eyebrow {
		display: block;
		font-family: var(--font-mono);
		font-size: 0.625rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		opacity: 0.6;
	}
	.app-switcher-name {
		display: block;
		overflow: hidden;
		font-size: 0.875rem;
		font-weight: 700;
		line-height: 1.2;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.app-switcher-menu {
		position: absolute;
		z-index: 40;
		top: calc(100% + 0.375rem);
		left: 0;
		right: 0;
		max-width: 22rem;
		padding: 0.375rem;
		border: 1px solid var(--sw-border);
		border-radius: 0.75rem;
		background: var(--sw-bg);
		color: var(--sw-fg);
		box-shadow: 0 10px 30px rgb(0 0 0 / 0.25);
	}
	.app-switcher-option {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		padding: 0.5rem;
		border-radius: 0.5rem;
	}
	.app-switcher-option:hover { background: var(--sw-hover); }
	.app-switcher-option[aria-current] .app-switcher-tile { background: var(--color-club); color: var(--color-club-content); }
</style>
