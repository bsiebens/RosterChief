<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { ArrowLeft, Swords } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { Runner, TBASE } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const seed = $derived(data.seed);
	const run = new Runner();

	type Slot = { type: 'team' | 'group' | 'skip'; team_id: string; group_id: string; rank: string };
	const initial = (): Slot[] => {
		const s = data.seed;
		if (!s) return [];
		return Array.from({ length: s.slot_count }, (_, i) => ({ type: 'team' as const, team_id: s.slots[i]?.team_id ?? '', group_id: '', rank: '1' }));
	};
	// svelte-ignore state_referenced_locally
	let slots = $state<Slot[]>(initial());
	let withPlacement = $state(false);
	$effect(() => {
		data.seed;
		untrack(() => (slots = initial()));
	});

	const used = $derived(new Set(slots.filter((s) => s.type === 'team' && s.team_id).map((s) => s.team_id)));
	const body = () => ({
		slots: slots.map((s) => (s.type === 'team' ? { type: 'team', team_id: s.team_id } : s.type === 'group' ? { type: 'group', group_id: s.group_id, rank: Number(s.rank) } : { type: 'skip' })),
		...(withPlacement ? { with_placement: true } : {})
	});
	async function submit(e: SubmitEvent) {
		e.preventDefault();
		let n = 0;
		if (await run.post<{ generated_count: number }>(`/tournaments/${tn.id}/stages/${data.stageId}/seed`, body(), (d) => void (n = d?.generated_count ?? 0), { reload: false })) {
			await goto(`${TBASE}/${tn.id}/setup?generated=${n}`);
		}
	}
	/** Fill the slots in the order of the entered teams (a quick start). */
	const fillInOrder = () => seed && (slots = slots.map((s, i) => ({ ...s, type: 'team', team_id: seed.teams[i]?.id ?? '' })));

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.seed.title') }));
</script>

<svelte:head><title>{pageTitle(t('tn.seed.title'))}</title></svelte:head>

<div class="grid max-w-3xl gap-4">
	<a class="link inline-flex w-fit items-center gap-1 text-sm" href="{TBASE}/{tn.id}/setup"><ArrowLeft size={14} /> {t('tn.seed.back')}</a>
	{#if !seed}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#if seed.already_generated}<Alert kind="warning">{t('tn.seed.already')}</Alert>{/if}
		<form class="card card-body card-hl gap-3" onsubmit={submit}>
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Swords size={16} class="shrink-0" aria-hidden="true" /> {t('tn.seed.title')}</h2>
			<p class="text-xs text-muted">{t('tn.seed.help')}</p>
			{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
			<div><button class="btn btn-outline btn-sm" type="button" onclick={fillInOrder}>{t('tn.seed.fill')}</button></div>
			<ol class="grid gap-2">
				{#each slots as s, i (i)}
					<li class="grid grid-cols-[2rem_7rem_1fr] items-center gap-2 sm:grid-cols-[2rem_8rem_1fr_6rem]">
						<span class="font-mono text-xs text-muted">{i + 1}</span>
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
			<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={withPlacement} /> {t('tn.setup.placement')}</label>
			<div class="flex justify-end"><button class="btn btn-primary" type="submit" disabled={run.busy}>{t('tn.seed.generate')}</button></div>
		</form>
	{/if}
</div>
