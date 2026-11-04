<script lang="ts">
	import { LANGS, LANG_LABELS, getLang } from '#lib/i18n.svelte.ts';
	import { changeLang } from '#lib/session.ts';

	/** Three-way language switch. `tone`: on the club sidebar, on the ink auth page, or on a light surface. */
	let { tone = 'light' }: { tone?: 'sidebar' | 'ink' | 'light' } = $props();
</script>

<div class="flex gap-1" role="group" aria-label="Language">
	{#each LANGS as l (l)}
		<button
			type="button"
			class="rounded px-2 py-1 font-mono text-[11px] uppercase {getLang() === l
				? 'bg-club text-club-content'
				: tone === 'sidebar'
					? 'text-sidebar-fg-dim hover:text-sidebar-fg'
					: tone === 'ink'
						? 'text-on-dark-dim hover:text-white'
						: 'text-muted hover:bg-rule'}"
			lang={l}
			title={LANG_LABELS[l]}
			aria-pressed={getLang() === l}
			onclick={() => changeLang(l)}>{l}</button
		>
	{/each}
</div>
