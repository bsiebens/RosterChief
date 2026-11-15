<script lang="ts">
	import { Check, X } from '@lucide/svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';
	import LineupMeta from '#lib/components/events/LineupMeta.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { statusLabelKey, statusStyle, type LineupMember, type LineupTemplateGroup } from '#lib/events/types.ts';

	/**
	 * The "Lines" view of a line-up: the sport's template as cards (Goalies, Line 1 …), each slot holding one picked player.
	 * Editable for managers (tap a slot, pick a player in a sheet); read-only for everyone else. Informational only.
	 * `slots` maps member id to slot key ("<group>.<slot>"); it is edited in place, the parent saves it with the picks.
	 */
	let {
		template,
		members,
		slots = $bindable(),
		editable = false,
		busy = false
	}: { template: LineupTemplateGroup[]; members: LineupMember[]; slots: Record<string, string>; editable?: boolean; busy?: boolean } = $props();

	const isOut = (m: LineupMember) => ['absent', 'excused', 'dropout'].includes(m.status);
	const keyOf = (g: LineupTemplateGroup, s: { key: string }) => `${g.key}.${s.key}`;
	const known = $derived(new Set(template.flatMap((g) => g.slots.map((s) => keyOf(g, s)))));
	const total = $derived(known.size);
	const occupant = (key: string) => members.find((m) => slots[m.member.id] === key);
	const filled = $derived(members.filter((m) => known.has(slots[m.member.id] ?? '')).length);
	const loose = $derived(members.filter((m) => !known.has(slots[m.member.id] ?? '')));
	const labelOf = (key: string) => {
		const [gk, sk] = key.split('.');
		const g = template.find((x) => x.key === gk);
		const s = g?.slots.find((x) => x.key === sk);
		return g && s ? `${g.label} · ${s.label}` : '';
	};

	let target = $state<{ key: string; label: string } | null>(null);
	const open = (g: LineupTemplateGroup, s: { key: string; label: string }) => {
		if (editable && !busy) target = { key: keyOf(g, s), label: `${g.label} · ${s.label}` };
	};
	function assign(m: LineupMember) {
		if (!target) return;
		slots = { ...slots, [m.member.id]: target.key };
		target = null;
	}
	function clear(key: string) {
		const next = { ...slots };
		for (const id of Object.keys(next)) if (next[id] === key) delete next[id];
		slots = next;
		target = null;
	}
	/** The sheet: only players who are not in another slot yet (plus the one already in this slot). Moving someone means clearing their slot first. */
	const candidates = $derived(
		members
			.filter((m) => !known.has(slots[m.member.id] ?? '') || slots[m.member.id] === target?.key)
			.sort((a, b) => Number(b.available) - Number(a.available) || (a.jersey_number ?? 999) - (b.jersey_number ?? 999))
	);
	const placedElsewhere = $derived(members.filter((m) => known.has(slots[m.member.id] ?? '') && slots[m.member.id] !== target?.key).length);
</script>

{#snippet player(m: LineupMember)}
	<span class="w-6 shrink-0 text-right font-display font-extrabold text-ink">{m.jersey_number ?? ''}</span>
	<span class="min-w-0 flex-1">
		<span class="block truncate text-sm font-semibold text-ink {isOut(m) ? 'line-through decoration-1' : ''}">{m.member.name}</span>
	</span>
	<span class="badge badge-xs shrink-0 border {statusStyle(m.status)}">{t(statusLabelKey(m.status))}</span>
{/snippet}

<div class="grid min-w-0 gap-3">
	<p class="flex flex-wrap items-center justify-between gap-2 text-sm font-semibold text-ink">
		<span>{t('coach.lu_slots_filled', { n: filled, total })}</span>
		{#if editable && !members.length}<span class="text-xs font-normal text-muted">{t('coach.lu_lines_pick_first')}</span>{/if}
	</p>
	{#each template as g (g.key)}
		<section class="card overflow-hidden">
			<h3 class="bg-subhead px-3.5 py-1.5 font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{g.label}</h3>
			<ul class="divide-y divide-rule">
				{#each g.slots as s (s.key)}
					{@const who = occupant(keyOf(g, s))}
					<li>
						<button type="button" class="flex min-h-12 w-full items-center gap-3 px-3.5 py-1.5 text-left {editable ? '' : 'cursor-default'}" disabled={!editable || busy} aria-label={t('coach.lu_slot_aria', { slot: `${g.label} · ${s.label}`, who: who ? who.member.name : t('coach.lu_slot_empty') })} onclick={() => open(g, s)}>
							<span class="w-10 shrink-0 font-display text-xs font-bold tracking-wide text-muted uppercase">{s.label}</span>
							{#if who}
								{@render player(who)}
							{:else}
								<span class="flex-1 text-sm text-muted italic">{t('coach.lu_slot_empty')}</span>
							{/if}
						</button>
					</li>
				{/each}
			</ul>
		</section>
	{/each}
	{#if loose.length}
		<section class="card overflow-hidden">
			<h3 class="bg-subhead px-3.5 py-1.5 font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('coach.lu_not_in_line')} · {loose.length}</h3>
			<ul class="divide-y divide-rule">
				{#each loose as m (m.member.id)}
					<li class="flex min-h-12 items-center gap-3 px-3.5 py-1.5">{@render player(m)}</li>
				{/each}
			</ul>
		</section>
	{/if}
	<p class="text-xs text-muted">{t('coach.lu_lines_info')}</p>
</div>

{#if editable}
	<Sheet open={target !== null} title={target?.label ?? ''} onclose={() => (target = null)}>
		{#if target}
			<div class="grid gap-3 p-4 pt-0">
				<h2 class="font-display text-xl font-extrabold text-ink uppercase">{target.label}</h2>
				{#if occupant(target.key)}
					<button type="button" class="btn btn-outline min-h-12 gap-2" onclick={() => clear(target!.key)}><X size={16} aria-hidden="true" /> {t('coach.lu_slot_clear')}</button>
				{/if}
				<ul class="divide-y divide-rule overflow-hidden rounded-box border border-line">
					{#each candidates as m (m.member.id)}
						{@const here = slots[m.member.id] === target.key}
						{@const locked = !m.available}
						<li>
							<button type="button" class="flex min-h-14 w-full items-center gap-3 px-3.5 py-2 text-left {locked ? 'cursor-not-allowed bg-subhead/60 opacity-60' : ''} {here ? 'bg-club/10' : ''}" disabled={locked} onclick={() => assign(m)}>
								<span class="w-6 shrink-0 text-right font-display font-extrabold text-ink">{m.jersey_number ?? ''}</span>
								<span class="min-w-0 flex-1">
									<span class="block truncate text-sm font-semibold text-ink {isOut(m) ? 'line-through decoration-1' : ''}">{m.member.name}</span>
									<span class="flex flex-wrap items-center gap-x-2">
										<span class="badge badge-xs border {statusStyle(m.status)}">{t(statusLabelKey(m.status))}</span>
										<LineupMeta {m} class="min-w-0" />
									</span>
								</span>
								{#if here}<Check size={18} class="shrink-0 text-ok" aria-hidden="true" />{/if}
							</button>
						</li>
					{:else}
						<li class="px-4 py-6 text-center text-sm text-muted">{t('coach.lu_lines_pick_first')}</li>
					{/each}
				</ul>
				{#if placedElsewhere}<p class="text-xs text-muted">{t('coach.lu_slot_hidden', { n: placedElsewhere })}</p>{/if}
			</div>
		{/if}
	</Sheet>
{/if}
