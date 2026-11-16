<script lang="ts">
	import { SearchX, ServerOff } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import LangSwitcher from '#lib/components/LangSwitcher.svelte';

	let { data } = $props();
	const missing = $derived(data.reason === 'not_found');
</script>

<svelte:head><title>{missing ? t('shell.hostError.not_found.title') : t('shell.hostError.unreachable.title')} · RosterChief</title></svelte:head>

<div class="flex min-h-screen flex-col items-center gap-10 bg-ink px-4 py-14 font-sans text-slate">
	<div class="flex w-full max-w-md items-center justify-between gap-4 py-1">
		<span class="flex items-center gap-2.5">
			<img class="h-8 w-8 shrink-0" src="/images/rosterchief-white.svg" alt="" width="32" height="32" />
			<span class="font-display text-xl font-extrabold tracking-[.1em] text-white uppercase">RosterChief</span>
		</span>
		<LangSwitcher tone="ink" />
	</div>
	<main class="flex w-full max-w-md flex-1 flex-col justify-center">
		<div class="card">
			<div class="card-body gap-3 p-7">
				{#if missing}<SearchX size={28} class="text-muted" aria-hidden="true" />{:else}<ServerOff size={28} class="text-muted" aria-hidden="true" />{/if}
				<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">
					{missing ? t('shell.hostError.not_found.title') : t('shell.hostError.unreachable.title')}
				</h1>
				<p class="text-sm text-muted">{missing ? t('shell.hostError.not_found.body') : t('shell.hostError.unreachable.body')}</p>
				{#if !missing}<a class="btn btn-primary mt-2 self-start" href="/" data-sveltekit-reload>{t('shell.hostError.retry')}</a>{/if}
			</div>
		</div>
	</main>
	<p class="font-mono text-[11px] text-on-dark-faint">&copy; {new Date().getFullYear()} RosterChief</p>
</div>
