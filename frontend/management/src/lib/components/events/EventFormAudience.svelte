<script lang="ts">
	import { Building2, Shield, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EventFormMembers from '#lib/components/events/EventFormMembers.svelte';
	import type { EventOptions } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { FormState } from '#lib/events/form-types.ts';

	/**
	 * Who is invited: teams and groups (only those GET /events/options offers: what the user may schedule for), the
	 * whole club (settings holders only; excludes teams and groups), plus extra and excluded members.
	 */
	let { s = $bindable(), options, fields = {}, gameNeedsTeam = false }: { s: FormState; options: EventOptions; fields?: Record<string, string>; gameNeedsTeam?: boolean } = $props();

	const limited = $derived(!options.can_club_wide);
	const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
	function setClubWide(on: boolean) {
		s.club_wide = on;
		if (on) {
			s.team_ids = [];
			s.group_ids = [];
		}
	}
</script>

{#snippet box(title: string, Icon: typeof Users, items: { id: string; name: string }[], selected: string[], onToggle: (id: string) => void, error?: string)}
	<fieldset class="grid gap-2" disabled={s.club_wide}>
		<legend class="mb-1 flex items-center gap-2 font-display text-sm font-bold tracking-wide text-ink uppercase"><Icon size={15} aria-hidden="true" /> {title}{#if selected.length}<span class="font-mono text-xs font-normal tracking-normal text-muted normal-case">· {selected.length}</span>{/if}</legend>
		<!-- Columns follow the box's own width (1 / 2 / at most 3), so the list fits a narrow side card as well as a full row. -->
		<div class="@container max-h-64 overflow-y-auto rounded-lg border p-2 {error ? 'border-danger' : 'border-line'} {s.club_wide ? 'opacity-50' : ''}">
			<div class="grid grid-cols-1 gap-1 @[18rem]:grid-cols-2 @[32rem]:grid-cols-3">
				{#each items as item (item.id)}
					<label class="flex min-w-0 cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-surface" title={item.name}>
						<input type="checkbox" class="checkbox" checked={selected.includes(item.id)} onchange={() => onToggle(item.id)} />
						<span class="truncate">{item.name}</span>
					</label>
				{/each}
			</div>
		</div>
		{#if error}<span class="block text-xs text-error">{error}</span>{/if}
	</fieldset>
{/snippet}

<div class="grid gap-5">
	{#if limited}<Alert kind="info">{t('eventform.audience.limited')}</Alert>{/if}

	{#if options.can_club_wide}
		<label class="flex cursor-pointer items-start gap-3 rounded-lg border p-3 {s.club_wide ? 'border-primary bg-subhead' : 'border-line'}">
			<input type="checkbox" class="checkbox mt-0.5" checked={s.club_wide} onchange={(e) => setClubWide(e.currentTarget.checked)} />
			<span class="grid gap-0.5">
				<span class="flex items-center gap-2 text-sm font-semibold text-ink"><Building2 size={15} aria-hidden="true" /> {t('eventform.audience.club_wide')}</span>
				<span class="text-xs text-muted">{t('eventform.audience.club_wide_help')}</span>
			</span>
		</label>
		{#if fields.club_wide}<span class="-mt-3 block text-xs text-error">{fields.club_wide}</span>{/if}
	{/if}

	<div class="grid gap-5">
		{#if options.teams.length}
			{@render box(t('eventform.audience.teams'), Shield, options.teams, s.team_ids, (id) => (s.team_ids = toggle(s.team_ids, id)), fields.team_ids || (gameNeedsTeam ? t('eventform.err.game_team') : undefined))}
		{:else if gameNeedsTeam || fields.team_ids}
			<span class="block text-xs text-error">{fields.team_ids || t('eventform.err.game_team')}</span>
		{/if}
		{#if options.groups.length}
			{@render box(t('eventform.audience.groups'), Users, options.groups, s.group_ids, (id) => (s.group_ids = toggle(s.group_ids, id)), fields.group_ids)}
		{/if}
	</div>

	<div class="grid gap-5 border-t border-rule pt-4">
		<div class="grid gap-1">
			<EventFormMembers label={t('eventform.audience.invited')} help={t('eventform.audience.invited_help')} bind:items={s.invited} exclude={s.excluded.map((m) => m.id)} error={fields.invited_member_ids} />
		</div>
		<div class="grid gap-1">
			<EventFormMembers label={t('eventform.audience.excluded')} help={t('eventform.audience.excluded_help')} bind:items={s.excluded} exclude={s.invited.map((m) => m.id)} error={fields.excluded_member_ids} />
		</div>
	</div>
</div>
