<script lang="ts">
	import { Check } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/** The three steps of the supplier flow, each linking to its tab, with how much is waiting at that step. */
	let { active, counts }: { active: 'to_order' | 'ordered' | 'received'; counts: { to_order: number; ordered: number; received: number } } = $props();
	const steps = ['to_order', 'ordered', 'received'] as const;
	const idx = $derived(steps.indexOf(active));
</script>

<nav aria-label={t('shop.prod.steps_label')} class="card card-body gap-3">
	<ol class="grid gap-3 md:grid-cols-3">
		{#each steps as s, i (s)}
			<li>
				<a
					href="?tab={s}"
					aria-current={active === s ? 'step' : undefined}
					class="flex h-full items-start gap-3 rounded-lg border p-3 transition-colors {active === s ? 'border-ink bg-surface' : 'border-line hover:border-ink'}">
					<span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full font-display text-sm font-extrabold {i < idx ? 'bg-success text-white' : active === s ? 'bg-ink text-white' : 'border border-line text-muted'}">
						{#if i < idx}<Check size={16} aria-hidden="true" />{:else}{i + 1}{/if}
					</span>
					<span class="min-w-0">
						<span class="flex flex-wrap items-center gap-2 font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{t(`shop.prod.step.${s}`)} <span class="badge badge-sm {counts[s] ? 'badge-info' : 'badge-neutral'}">{counts[s]}</span></span>
						<span class="mt-0.5 block text-xs text-muted">{t(`shop.prod.step.${s}_desc`)}</span>
					</span>
				</a>
			</li>
		{/each}
	</ol>
</nav>
