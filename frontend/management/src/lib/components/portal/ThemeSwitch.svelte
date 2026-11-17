<script lang="ts">
	import { Moon, Smartphone, Sun } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { getTheme, setTheme, THEMES, type Theme } from '#lib/theme.ts';

	/** Light, dark, or follow the phone: applies the moment it is tapped and is remembered on this device. */
	let current = $state<Theme>('auto');
	onMount(() => (current = getTheme()));
	const pick = (theme: Theme) => {
		current = theme;
		setTheme(theme);
	};
	const icons = { auto: Smartphone, light: Sun, dark: Moon } as const;
</script>

<div class="grid gap-2">
	<p class="font-semibold text-ink" id="theme-label">{t('portal.theme')}</p>
	<div class="grid grid-cols-3 gap-1 rounded-box bg-subhead p-1" role="group" aria-labelledby="theme-label">
		{#each THEMES as theme (theme)}
			{@const Icon = icons[theme]}
			<button type="button" class="flex min-h-11 items-center justify-center gap-1.5 rounded-box text-sm font-semibold {current === theme ? 'bg-white text-ink shadow-sm' : 'text-muted'}" aria-pressed={current === theme} onclick={() => pick(theme)}>
				<Icon size={16} aria-hidden="true" />{t(`portal.theme_${theme}`)}
			</button>
		{/each}
	</div>
	<p class="text-xs text-muted">{t('portal.theme_help')}</p>
</div>
