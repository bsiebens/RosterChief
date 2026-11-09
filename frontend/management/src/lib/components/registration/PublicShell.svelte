<script lang="ts">
	import type { Snippet } from 'svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import LangSwitcher from '#lib/components/LangSwitcher.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { ClubBrand } from '#lib/registration-public.ts';

	/** The club-branded frame of the public registration pages: gradient header with the club name and crest, a narrow main column and the language switch. */
	let { title, kicker, club, wide = false, children }: { title: string; kicker: string; club: ClubBrand | null; wide?: boolean; children: Snippet } = $props();
	const width = $derived(wide ? 'max-w-3xl' : 'max-w-2xl');
</script>

<div class="min-h-screen bg-paper font-sans text-slate">
	<header class="bg-primary" style="background-image: linear-gradient(120deg, color-mix(in srgb, var(--color-club) 55%, var(--color-primary)) 0%, var(--color-primary) 62%);">
		<div class="mx-auto flex flex-wrap items-center justify-between gap-4 px-4 py-6 sm:px-6 sm:py-8 {width}">
			<div class="min-w-0">
				<p class="font-mono text-xs tracking-[.12em] text-primary-content uppercase opacity-80">{kicker}</p>
				<h1 class="mt-1 font-display text-3xl leading-tight font-extrabold tracking-[.02em] text-primary-content uppercase break-words sm:text-4xl">{title}</h1>
			</div>
			{#if club}
				<div class="flex items-center gap-3">
					<span class="hidden font-display text-xl leading-none font-extrabold tracking-[.06em] text-primary-content uppercase opacity-90 sm:inline">{club.name}</span>
					<ClubCrest name={club.name} logoUrl={club.logo_url} size={44} />
				</div>
			{/if}
		</div>
	</header>

	<main class="mx-auto grid gap-4 px-4 py-6 sm:px-6 {width}">{@render children()}</main>

	<footer class="mx-auto flex items-center justify-between gap-4 px-4 pb-8 sm:px-6 {width}">
		<p class="font-mono text-[11px] text-muted">{t('reg.footer')}</p>
		<LangSwitcher />
	</footer>
</div>
