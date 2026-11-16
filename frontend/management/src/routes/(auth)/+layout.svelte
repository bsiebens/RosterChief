<script lang="ts">
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import LangSwitcher from '#lib/components/LangSwitcher.svelte';

	let { data, children } = $props();
	const club = $derived(data.mode === 'club' ? data.branding : null);
</script>

<!-- Same entrance shell as the old app's _auth_base.html: a club header strip over one centred card on a dark page. -->
<div class="flex min-h-screen flex-col items-center gap-10 bg-ink px-4 py-14 font-sans text-slate">
	<div class="flex w-full max-w-md items-center justify-between gap-4 bg-ink py-1">
		<a class="flex min-w-0 items-center gap-2.5" href="/">
			{#if club}
				<ClubCrest name={club.name} logoUrl={club.logo_url} size={32} />
				<span class="min-w-0 truncate font-display text-lg font-extrabold tracking-[.08em] text-white uppercase">{club.name}</span>
			{:else}
				<img class="h-8 w-8 shrink-0" src="/images/rosterchief-white.svg" alt="" width="32" height="32" />
				<span class="font-display text-xl font-extrabold tracking-[.1em] text-white uppercase">RosterChief</span>
			{/if}
		</a>
		<LangSwitcher tone="ink" />
	</div>

	<main class="flex w-full max-w-md flex-1 flex-col justify-center gap-4">
		{@render children()}
	</main>

	<p class="font-mono text-[11px] text-on-dark-faint">&copy; {new Date().getFullYear()} RosterChief</p>
</div>
