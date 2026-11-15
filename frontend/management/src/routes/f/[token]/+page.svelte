<script lang="ts">
	import { Copyright } from '@lucide/svelte';
	import { page } from '$app/state';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import LangSwitcher from '#lib/components/LangSwitcher.svelte';
	import FormResponse from '#lib/components/forms/FormResponse.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const form = $derived(data.form);
	const club = $derived(data.mode === 'club' ? data.branding : null);
</script>

<svelte:head>
	<title>{pageTitle(form.title)}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

<div class="min-h-screen bg-paper font-sans text-slate">
	<header class="bg-primary" style="background-image: linear-gradient(120deg, color-mix(in srgb, var(--color-club) 55%, var(--color-primary)) 0%, var(--color-primary) 62%);">
		<div class="mx-auto flex max-w-2xl flex-wrap items-center justify-between gap-4 px-4 py-6 sm:px-6 sm:py-8">
			<div class="min-w-0">
				<p class="font-mono text-xs tracking-[.12em] text-primary-content uppercase opacity-80">{t('pubform.kicker')}</p>
				<h1 class="mt-1 font-display text-3xl leading-tight font-extrabold tracking-[.02em] text-primary-content uppercase break-words sm:text-4xl">{form.title}</h1>
			</div>
			{#if club}
				<div class="flex items-center gap-3">
					<span class="hidden font-display text-xl leading-none font-extrabold tracking-[.06em] text-primary-content uppercase opacity-90 sm:inline">{club.name}</span>
					<ClubCrest name={club.name} logoUrl={club.logo_url} size={44} />
				</div>
			{/if}
		</div>
	</header>

	<main class="mx-auto grid max-w-2xl gap-4 px-4 py-6 sm:px-6">
		<FormResponse {form} endpoint={`/public/forms/${encodeURIComponent(page.params.token ?? '')}`} />
	</main>

	<footer class="mx-auto flex max-w-2xl items-center justify-between gap-4 px-4 pb-8 sm:px-6">
		<p class="flex items-center gap-1 font-mono text-[11px] text-muted"><Copyright size={12} aria-hidden="true" /> RosterChief {new Date().getFullYear()}</p>
		<LangSwitcher />
	</footer>
</div>
