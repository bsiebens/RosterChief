<script lang="ts">
	import { page } from '$app/state';

	const TABS = [
		{ href: '/push', label: 'Monitoring' },
		{ href: '/push/sandbox', label: 'Sandbox' },
		{ href: '/push/config', label: 'Configuration' }
	];
	const active = (href: string) => (href === '/push' ? page.url.pathname === '/push' : page.url.pathname.startsWith(href));
</script>

<nav class="flex gap-1 border-b border-line" aria-label="Push sections">
	{#each TABS as tab (tab.href)}
		<a
			class="-mb-px border-b-2 px-4 py-2 font-mono text-xs {active(tab.href) ? 'border-ice bg-white text-ink' : 'border-transparent text-muted hover:text-ink'}"
			href={tab.href}
			aria-current={active(tab.href) ? 'page' : undefined}>{tab.label}</a
		>
	{/each}
</nav>
