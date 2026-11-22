<script lang="ts">
	import { Swords } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { api } from '#lib/management-api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { Runner } from '#lib/tournaments.svelte.ts';
	import type { TTeamRef } from '#lib/tournaments.svelte.ts';

	type SeedData = { already_generated: boolean; placement: boolean; slot_count: number; slots: { type: 'team'; team_id: string }[]; teams: TTeamRef[]; groups: { id: string; name: string; stage_id: string }[] };
	type Slot = { type: 'team' | 'group' | 'skip'; team_id: string; group_id: string; rank: string };

	/** Seeds and generates a knockout stage: every slot gets a team, the team that finishes at a rank of a group, or is left out. */
	let { open = $bindable(false), tournamentId, stage, onDone }: { open: boolean; tournamentId: string; stage: { id: string; name: string } | null; onDone: (generated: number) => void } = $props();

	const run = new Runner();
	let seed = $state<SeedData | null>(null);
	let loadError = $state(false);
	let slots = $state<Slot[]>([]);
	let withPlacement = $state(false);

	$effect(() => {
		if (!open || !stage) return;
		const id = stage.id;
		seed = null;
		loadError = false;
		run.clear();
		withPlacement = false;
		api<SeedData>('GET', `/tournaments/${tournamentId}/stages/${id}/seed`).then((res) => {
			if (!res.ok || !res.data) {
				loadError = true;
				return;
			}
			const s = res.data;
			seed = s;
			slots = Array.from({ length: s.slot_count }, (_, i) => ({ type: 'team' as const, team_id: s.slots[i]?.team_id ?? '', group_id: '', rank: '1' }));
		});
	});

	/** Round 1 pairs slot i with slot size + 1 - i (size = the bracket size, a power of two); a missing opponent is a bye. */
	const size = $derived(Math.max(2, 2 ** Math.ceil(Math.log2(Math.max(slots.length, 2)))));
	const opponent = (n: number) => (size + 1 - n > slots.length ? '' : String(size + 1 - n));
	const used = $derived(new Set(slots.filter((s) => s.type === 'team' && s.team_id).map((s) => s.team_id)));
	const body = () => ({
		slots: slots.map((s) => (s.type === 'team' ? { type: 'team', team_id: s.team_id } : s.type === 'group' ? { type: 'group', group_id: s.group_id, rank: Number(s.rank) } : { type: 'skip' })),
		...(withPlacement ? { with_placement: true } : {})
	});
	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!stage) return;
		let n = 0;
		if (await run.post<{ generated_count: number }>(`/tournaments/${tournamentId}/stages/${stage.id}/seed`, body(), (d) => void (n = d?.generated_count ?? 0))) {
			open = false;
			onDone(n);
		}
	}
	/** Fill the slots in the order of the entered teams (a quick start). */
	/** Fill the slots with the group ranks, best first: A1, B1, A2, B2, ... so the seeds follow the group tables. */
	function fillByGroupRank() {
		const gs = seed?.groups ?? [];
		if (!gs.length) return;
		slots = slots.map((s, i) => ({ ...s, type: 'group', group_id: gs[i % gs.length].id, rank: String(Math.floor(i / gs.length) + 1) }));
	}
	const fillInOrder = () => seed && (slots = slots.map((s, i) => ({ ...s, type: 'team', team_id: seed!.teams[i]?.id ?? '' })));
</script>

<Modal bind:open title={t('tn.seed.title')} wide>
	{#if loadError}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if !seed}
		<p class="text-sm text-muted">{t('common.loading')}</p>
	{:else}
		<form class="grid gap-3" onsubmit={submit}>
			<p class="flex items-center gap-2 text-sm font-semibold text-ink"><Swords size={16} class="shrink-0" aria-hidden="true" /> {stage?.name}</p>
			{#if seed.already_generated}<Alert kind="warning">{t('tn.seed.already')}</Alert>{/if}
			<p class="text-xs text-muted">{t('tn.seed.help')}</p>
			{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
			<div class="flex flex-wrap gap-2">
				<button class="btn btn-outline btn-sm" type="button" onclick={fillInOrder}>{t('tn.seed.fill')}</button>
				{#if seed.groups.length}<button class="btn btn-outline btn-sm" type="button" onclick={fillByGroupRank}>{t('tn.seed.fill_groups')}</button>{/if}
			</div>
			<ol class="grid gap-2">
				{#each slots as s, i (i)}
					<li class="grid grid-cols-[3.5rem_7rem_1fr] items-center gap-2 sm:grid-cols-[3.5rem_8rem_1fr_6rem]">
						<span class="font-mono text-xs text-muted" title={t('tn.seed.plays')}>{i + 1} <span class="text-[10px]">v {opponent(i + 1) || t('tn.seed.bye')}</span></span>
						<select class="select select-bordered select-sm" aria-label={t('tn.seed.slot_type', { n: i + 1 })} bind:value={s.type}>
							<option value="team">{t('tn.seed.type_team')}</option>
							<option value="group">{t('tn.seed.type_group')}</option>
							<option value="skip">{t('tn.seed.type_skip')}</option>
						</select>
						{#if s.type === 'team'}
							<select class="select select-bordered select-sm sm:col-span-2" aria-label={t('tn.seed.team')} bind:value={s.team_id} required>
								<option value="" disabled>{t('tn.seed.pick_team')}</option>
								{#each seed.teams as team (team.id)}<option value={team.id} disabled={used.has(team.id) && s.team_id !== team.id}>{team.name}</option>{/each}
							</select>
						{:else if s.type === 'group'}
							<select class="select select-bordered select-sm" aria-label={t('tn.seed.group')} bind:value={s.group_id} required>
								<option value="" disabled>{t('tn.seed.pick_group')}</option>
								{#each seed.groups as g (g.id)}<option value={g.id}>{g.name}</option>{/each}
							</select>
							<input class="input input-bordered input-sm hidden sm:block" type="number" min="1" aria-label={t('tn.seed.rank')} placeholder={t('tn.seed.rank')} bind:value={s.rank} required />
						{:else}
							<span class="text-sm text-muted sm:col-span-2">{t('tn.seed.skip_note')}</span>
						{/if}
						{#if s.type === 'group'}<input class="input input-bordered input-sm col-span-3 sm:hidden" type="number" min="1" aria-label={t('tn.seed.rank')} placeholder={t('tn.seed.rank')} bind:value={s.rank} required />{/if}
					</li>
				{/each}
			</ol>
			{#if seed.placement}
				<p class="text-sm text-muted">{t('tn.seed.placement_on')}</p>
			{:else}
				<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={withPlacement} /> {t('tn.setup.placement')}</label>
			{/if}
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('tn.seed.generate')}</button>
			</div>
		</form>
	{/if}
</Modal>
