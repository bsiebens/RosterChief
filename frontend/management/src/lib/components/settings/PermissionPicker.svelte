<script lang="ts">
	import { ChevronDown, ChevronRight, ShieldAlert } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { groupPermissions, type Permission } from '#lib/roles.ts';

	/**
	 * The permission catalogue, grouped by area as collapsed sections (all closed to start with) that show how many of their permissions
	 * are selected. The selection is submitted as hidden `permissions` inputs, so closed sections still count. A permission whose feature
	 * the club's plan lacks is shown but marked.
	 */
	let { permissions, selected = $bindable([]), disabled = false }: { permissions: Permission[]; selected?: string[]; disabled?: boolean } = $props();
	const groups = $derived(groupPermissions(permissions));
	let open = $state<Record<string, boolean>>({});
	const id = $props.id();
	const toggle = (code: string, on: boolean) => (selected = on ? [...selected, code] : selected.filter((c) => c !== code));
	const count = (items: Permission[]) => items.filter((p) => selected.includes(p.code)).length;
</script>

{#each selected as code (code)}<input type="hidden" name="permissions" value={code} />{/each}

<div class="divide-y divide-rule overflow-hidden rounded-lg border border-line">
	{#each groups as g (g.area)}
		{@const n = count(g.items)}
		<section>
			<button type="button" class="flex w-full items-center gap-2 bg-subhead px-3.5 py-2 text-left hover:bg-paper" aria-expanded={!!open[g.area]} aria-controls="{id}-{g.area}" onclick={() => (open[g.area] = !open[g.area])}>
				{#if open[g.area]}<ChevronDown size={16} aria-hidden="true" />{:else}<ChevronRight size={16} aria-hidden="true" />{/if}
				<span class="font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{t(`roles.area.${g.area}`)}</span>
				<span class="rounded-full border px-2 py-0.5 font-mono text-xs font-semibold {n ? 'border-club bg-white text-ink' : 'border-muted bg-white text-slate'}">{n} / {g.items.length}</span>
			</button>
			{#if open[g.area]}
				<ul id="{id}-{g.area}" class="divide-y divide-rule">
					{#each g.items as p (p.code)}
						<li>
							<label class="flex items-start gap-3 px-3.5 py-2 {disabled ? 'opacity-70' : 'cursor-pointer hover:bg-surface'}">
								<input type="checkbox" class="checkbox checkbox-sm mt-0.5" checked={selected.includes(p.code)} {disabled} onchange={(e) => toggle(p.code, e.currentTarget.checked)} />
								<span class="min-w-0 text-sm">
									<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-semibold text-ink">
										{p.label}
										{#if p.admin}<span class="badge badge-sm badge-warning gap-1"><ShieldAlert size={10} aria-hidden="true" /> {t('roles.admin')}</span>{/if}
										{#if !p.available}<span class="badge badge-sm badge-outline">{t('roles.needs_feature', { feature: p.feature_name ?? p.feature ?? '' })}</span>{/if}
									</span>
									<span class="block text-xs text-muted">{t(`roles.perm.${p.code}`)}</span>
								</span>
							</label>
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	{/each}
</div>
