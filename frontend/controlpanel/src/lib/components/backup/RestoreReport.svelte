<script lang="ts" module>
	export type ReportItem = { id: string; label: string; reasons: string[] };
	export type ReportSection = { key: string; name: string; group: string; total: number; blocked: number; truncated: number; items: ReportItem[] };
	export type Report = { total: number; restorable: number; blocked: number; club: string[]; sections: ReportSection[]; refs: Record<string, string[]> };

	/** The areas a backup is split into for the include/exclude switches, in display order. */
	export const GROUPS: Record<string, { name: string; hint: string }> = {
		accounts: { name: 'Accounts', hint: 'Logins of everyone in the club' },
		roles: { name: 'Roles & permissions', hint: 'What each role may do' },
		members: { name: 'Members & families', hint: 'People, families, memberships, fees, requirements' },
		teams: { name: 'Teams & rosters', hint: 'Teams, seasons’ rosters, staff' },
		events: { name: 'Events & calendar', hint: 'Games, training, attendance, referees, game data' },
		news: { name: 'News', hint: 'Articles and photos' },
		formbuilder: { name: 'Forms', hint: 'Forms, sends and submitted answers' },
		shop: { name: 'Shop', hint: 'Products, orders, discounts, vouchers' },
		registration: { name: 'Registrations & invoices', hint: 'Registrations, invoices and instalments' },
		evaluations: { name: 'Evaluations', hint: 'Player evaluations and meetings' },
		sponsoring: { name: 'Sponsors', hint: 'Sponsors and their logos' },
		club: { name: 'Other club data', hint: 'Seasons, locations, opponents' }
	};
	/** Accounts and roles are the club itself: they are always restored. */
	export const LOCKED = ['accounts', 'roles'];
</script>

<script lang="ts">
	import { ChevronDown, CircleAlert, CircleCheck, Lock } from '@lucide/svelte';

	let { report, excluded = $bindable([]) }: { report: Report; excluded: string[] } = $props();

	type Group = { key: string; name: string; hint: string; sections: ReportSection[]; total: number; blocked: number; locked: boolean };
	const groups = $derived.by(() => {
		const map = new Map<string, ReportSection[]>();
		for (const section of report.sections) map.set(section.group, [...(map.get(section.group) ?? []), section]);
		const order = Object.keys(GROUPS);
		return [...map.entries()]
			.sort(([a], [b]) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99))
			.map(([key, sections]): Group => ({
				key,
				name: GROUPS[key]?.name ?? key,
				hint: GROUPS[key]?.hint ?? '',
				sections,
				total: sections.reduce((n, s) => n + s.total, 0),
				blocked: sections.reduce((n, s) => n + s.blocked, 0),
				locked: LOCKED.includes(key)
			}));
	});
	const groupOf = (sectionKey: string) => report.sections.find((s) => s.key === sectionKey)?.group;
	const left = (key: string) => excluded.includes(key);
	const toggle = (key: string) => (excluded = left(key) ? excluded.filter((k) => k !== key) : [...excluded, key]);

	// Leaving an area out also drops what points at it: the included areas whose records reference a left-out one.
	const affected = $derived.by(() => {
		const hit = new Map<string, Set<string>>();
		for (const group of groups) {
			if (left(group.key)) continue;
			for (const section of group.sections)
				for (const target of report.refs[section.key] ?? []) {
					const owner = groupOf(target);
					if (owner && owner !== group.key && left(owner)) hit.set(group.key, new Set([...(hit.get(group.key) ?? []), GROUPS[owner]?.name ?? owner]));
				}
		}
		return hit;
	});
	const leftOut = $derived(groups.filter((g) => left(g.key)));

	let open = $state<Record<string, boolean>>({});
	const isOpen = (g: Group) => open[g.key] ?? g.blocked > 0;
</script>

<div class="flex flex-col gap-4">
	<div class="grid grid-cols-3 gap-3">
		<div class="rounded-lg border border-line bg-subhead p-3"><p class="font-mono text-[11px] text-muted uppercase">In the backup</p><p class="font-display text-2xl font-extrabold text-ink">{report.total}</p></div>
		<div class="rounded-lg border border-line bg-subhead p-3"><p class="font-mono text-[11px] text-muted uppercase">Can be restored</p><p class="font-display text-2xl font-extrabold text-ink">{report.restorable}</p></div>
		<div class="rounded-lg border p-3 {report.blocked ? 'border-danger-border bg-danger-bg' : 'border-line bg-subhead'}"><p class="font-mono text-[11px] text-muted uppercase">Cannot be restored</p><p class="font-display text-2xl font-extrabold {report.blocked ? 'text-danger' : 'text-ink'}">{report.blocked}</p></div>
	</div>

	{#if report.club.length}
		<div class="rounded-lg border border-danger-border bg-danger-bg p-3 text-sm text-danger">
			<p class="mb-1 font-semibold">Problems that stop the whole club from being restored</p>
			<ul class="list-disc pl-5">{#each report.club as problem (problem)}<li>{problem}</li>{/each}</ul>
		</div>
	{/if}

	<div class="divide-y divide-line overflow-hidden rounded-lg border border-line">
		{#each groups as group (group.key)}
			{@const off = left(group.key)}
			<div class={off ? 'bg-subhead/60' : ''}>
				<div class="flex items-center gap-3 px-3 py-2.5">
					<button type="button" class="btn btn-xs btn-outline h-7 w-7 shrink-0 p-0" aria-expanded={isOpen(group)} aria-label="{isOpen(group) ? 'Collapse' : 'Expand'} {group.name}" onclick={() => (open[group.key] = !isOpen(group))}>
						<ChevronDown size={14} class="transition-transform {isOpen(group) ? '' : '-rotate-90'}" aria-hidden="true" />
					</button>
					<div class="min-w-0 flex-1 {off ? 'opacity-60' : ''}">
						<p class="text-sm font-semibold text-ink">{group.name}</p>
						<p class="truncate text-xs text-muted">{group.hint}</p>
					</div>
					<span class="font-mono text-xs text-muted {off ? 'line-through' : ''}">{group.total - group.blocked} / {group.total}</span>
					{#if group.blocked && !off}
						<span class="inline-flex items-center gap-1 rounded-full border border-danger-border bg-danger-bg px-2 py-0.5 text-xs font-semibold text-danger"><CircleAlert size={12} aria-hidden="true" />{group.blocked} blocked</span>
					{:else if !off}
						<span class="inline-flex items-center gap-1 rounded-full border border-ok-border bg-ok-bg px-2 py-0.5 text-xs font-semibold text-ok-text"><CircleCheck size={12} aria-hidden="true" />OK</span>
					{/if}
					{#if group.locked}
						<span class="inline-flex w-28 items-center justify-end gap-1.5 text-xs text-muted"><Lock size={12} aria-hidden="true" />Always included</span>
					{:else}
						<label class="flex w-28 cursor-pointer items-center justify-end gap-2 text-xs font-semibold {off ? 'text-muted' : 'text-ink'}">
							{off ? 'Left out' : 'Include'}
							<input type="checkbox" class="toggle toggle-sm" checked={!off} onchange={() => toggle(group.key)} aria-label="Include {group.name}" />
						</label>
					{/if}
				</div>
				{#if affected.get(group.key)}
					<p class="border-t border-line bg-warn-bg px-3 py-1.5 pl-14 text-xs text-warn-text">Items here that belong to {[...(affected.get(group.key) ?? [])].join(', ')} are left out as well.</p>
				{/if}
				{#if isOpen(group) && !off}
					<div class="divide-y divide-line border-t border-line bg-white text-sm">
						{#each group.sections as section (section.key)}
							<div class="px-3 py-2 pl-14">
								<div class="flex items-center gap-3">
									<span class="min-w-0 flex-1 text-ink">{section.name}</span>
									<span class="font-mono text-xs text-muted">{section.total - section.blocked} / {section.total}</span>
								</div>
								{#each section.items.filter((i) => i.reasons.length) as item (item.id)}
									<div class="mt-1.5 flex gap-2 rounded-md bg-danger-bg px-2.5 py-1.5">
										<CircleAlert size={14} class="mt-0.5 shrink-0 text-danger" aria-hidden="true" />
										<div class="min-w-0"><p class="truncate text-xs font-medium text-ink">{item.label}</p>{#each item.reasons as reason (reason)}<p class="text-xs text-danger">{reason}</p>{/each}</div>
									</div>
								{/each}
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/each}
	</div>

	{#if leftOut.length}
		<p class="text-sm text-muted">Leaving out <strong class="text-ink">{leftOut.map((g) => g.name).join(', ')}</strong>, plus anything that cannot exist without {leftOut.length === 1 ? 'it' : 'them'}.</p>
	{/if}
</div>
