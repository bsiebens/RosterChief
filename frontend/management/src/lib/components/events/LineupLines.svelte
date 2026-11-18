<script lang="ts">
	import { Check, FileDown, Save, Users, X } from '@lucide/svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';
	import LineupMeta from '#lib/components/events/LineupMeta.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { statusLabelKey, statusStyle, type LineupMember, type LineupTemplateGroup } from '#lib/events/types.ts';

	/**
	 * The "Lines" view of a line-up: the sport's template as cards (Goalies, Line 1 …), each slot holding one picked player; a player
	 * may hold several slots (a regular line plus power play …). Editable for managers (tap a slot, pick a player in a sheet);
	 * read-only for everyone else. Informational only. `slots` maps slot key ("<group>.<slot>") to member id; it is edited in
	 * place, the parent saves it with the picks. `pdfHref` shows a "Download PDF" link.
	 */
	let {
		template,
		members,
		slots = $bindable(),
		editable = false,
		busy = false,
		pdfHref = '',
		title = '',
		onsave = undefined,
		saveDisabled = false,
		hideActions = false
	}: { template: LineupTemplateGroup[]; members: LineupMember[]; slots: Record<string, string>; editable?: boolean; busy?: boolean; pdfHref?: string; title?: string; onsave?: () => void; saveDisabled?: boolean; /** The save and PDF buttons live somewhere else (the phone's bottom band). */ hideActions?: boolean } = $props();

	const isOut = (m: LineupMember) => ['absent', 'excused', 'dropout'].includes(m.status);
	const keyOf = (g: LineupTemplateGroup, s: { key: string }) => `${g.key}.${s.key}`;
	const known = $derived(new Set(template.flatMap((g) => g.slots.map((s) => keyOf(g, s)))));
	const total = $derived(known.size);
	const byId = $derived(new Map(members.map((m) => [m.member.id, m])));
	const occupant = (key: string) => byId.get(slots[key] ?? '');
	/** Slot keys (template order) each picked player holds. */
	const heldBy = $derived.by(() => {
		const out = new Map<string, string[]>();
		for (const g of template)
			for (const s of g.slots) {
				const id = slots[keyOf(g, s)];
				if (id && byId.has(id)) out.set(id, [...(out.get(id) ?? []), keyOf(g, s)]);
			}
		return out;
	});
	const filled = $derived([...heldBy.values()].reduce((n, keys) => n + keys.length, 0));
	const loose = $derived(members.filter((m) => !heldBy.has(m.member.id)));
	/** "Line 1, Power play 1": the groups of the given slot keys. */
	const groupLabels = (keys: string[]) => [...new Set(keys.map((k) => template.find((g) => g.key === k.split('.')[0])?.label ?? ''))].filter(Boolean).join(', ');

	let target = $state<{ key: string; label: string } | null>(null);
	const open = (g: LineupTemplateGroup, s: { key: string; label: string }) => {
		if (editable && !busy) target = { key: keyOf(g, s), label: `${g.label} · ${s.label}` };
	};
	function assign(m: LineupMember) {
		if (!target) return;
		slots = { ...slots, [target.key]: m.member.id };
		target = null;
	}
	function clear(key: string) {
		slots = Object.fromEntries(Object.entries(slots).filter(([k]) => k !== key));
		target = null;
	}
	/** The sheet offers every picked player, also ones already in another slot (they get an "also in" hint); out/unavailable ones stay disabled. */
	const candidates = $derived([...members].sort((a, b) => Number(b.available) - Number(a.available) || (a.jersey_number ?? 999) - (b.jersey_number ?? 999)));
</script>

{#snippet player(m: LineupMember)}
	<span class="w-6 shrink-0 text-right font-display font-extrabold text-ink">{m.jersey_number ?? ''}</span>
	<span class="min-w-0 flex-1">
		<span class="block truncate text-sm font-semibold text-ink {isOut(m) ? 'line-through decoration-1' : ''}">{m.member.name}</span>
	</span>
	<span class="badge badge-xs shrink-0 border {statusStyle(m.status)}">{t(statusLabelKey(m.status))}</span>
{/snippet}

<div class="@container grid min-w-0 gap-3">
	<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
		<div class="min-w-0">
			{#if title}<h3 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><Users size={16} class="shrink-0" aria-hidden="true" /> {title}</h3>{/if}
			<p class="text-sm font-semibold text-ink">{t('coach.lu_slots_filled', { n: filled, total })}{#if editable && !members.length} <span class="text-xs font-normal text-muted">· {t('coach.lu_lines_pick_first')}</span>{/if}</p>
		</div>
		<div class="ml-auto flex flex-wrap items-center justify-end gap-2">
			{#if editable && onsave && !hideActions}<button type="button" class="btn btn-primary btn-sm gap-1" disabled={busy || saveDisabled} onclick={onsave}><Save size={14} aria-hidden="true" /> {t('coach.lu_save_lines')}</button>{/if}
			{#if pdfHref && !hideActions}<a class="btn btn-outline btn-sm gap-1" href={pdfHref} target="_blank" rel="noopener"><FileDown size={14} aria-hidden="true" /> {t('coach.lu_pdf')}</a>{/if}
		</div>
	</div>
	<div class="grid min-w-0 grid-cols-1 items-start gap-3 @xl:grid-cols-2">
	{#each template as g (g.key)}
		<section class="card h-fit min-w-0 overflow-hidden break-inside-avoid">
			<h3 class="bg-subhead px-3.5 py-1.5 font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{g.label}</h3>
			<ul class="divide-y divide-rule">
				{#each g.slots as s (s.key)}
					{@const who = occupant(keyOf(g, s))}
					<li>
						<button type="button" class="flex min-h-9 w-full items-center gap-2 px-3 py-1 text-left {editable ? '' : 'cursor-default'}" disabled={!editable || busy} aria-label={t('coach.lu_slot_aria', { slot: `${g.label} · ${s.label}`, who: who ? who.member.name : t('coach.lu_slot_empty') })} onclick={() => open(g, s)}>
							<span class="w-8 shrink-0 truncate font-display text-xs font-bold tracking-wide text-muted uppercase">{s.label}</span>
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
		<section class="card h-fit min-w-0 overflow-hidden break-inside-avoid">
			<h3 class="bg-subhead px-3.5 py-1.5 font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('coach.lu_not_in_line')} · {loose.length}</h3>
			<ul class="divide-y divide-rule">
				{#each loose as m (m.member.id)}
					<li class="flex min-h-9 items-center gap-2 px-3 py-1">{@render player(m)}</li>
				{/each}
			</ul>
		</section>
	{/if}
	</div>
	<p class="text-xs text-muted">{t('coach.lu_lines_info')}</p>
</div>

{#if editable}
	<Sheet open={target !== null} title={target?.label ?? ''} onclose={() => (target = null)}>
		{#if target}
			<div class="grid gap-3 p-4 pt-0">
				<h2 class="font-display text-xl font-extrabold text-ink uppercase">{target.label}</h2>
				{#if slots[target.key]}
					<button type="button" class="btn btn-outline min-h-12 gap-2" onclick={() => clear(target!.key)}><X size={16} aria-hidden="true" /> {t('coach.lu_slot_clear')}</button>
				{/if}
				<ul class="divide-y divide-rule overflow-hidden rounded-box border border-line">
					{#each candidates as m (m.member.id)}
						{@const here = slots[target.key] === m.member.id}
						{@const also = (heldBy.get(m.member.id) ?? []).filter((k) => k !== target?.key)}
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
									{#if also.length}<span class="block truncate text-xs text-muted">{t('coach.lu_also_in', { lines: groupLabels(also) })}</span>{/if}
								</span>
								{#if here}<Check size={18} class="shrink-0 text-ok" aria-hidden="true" />{/if}
							</button>
						</li>
					{:else}
						<li class="px-4 py-6 text-center text-sm text-muted">{t('coach.lu_lines_pick_first')}</li>
					{/each}
				</ul>
			</div>
		{/if}
	</Sheet>
{/if}
