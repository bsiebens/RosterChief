<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { ArrowLeft, Plus, UserPlus, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { PersonResult } from '../../../routes/(app)/teams/[id]/add/+page.server.ts';

	import type { ActionData, PageData } from '../../../routes/(app)/teams/[id]/add/$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const team = $derived(data.team);
	const seasonId = $derived(team?.season_id ?? '');
	const playerPositions = $derived((data.positions ?? []).filter((p) => !p.staff_position));
	const staffPositions = $derived((data.positions ?? []).filter((p) => p.staff_position));
	const isStaff = (positionId: string) => !!data.positions?.find((p) => p.id === positionId)?.staff_position;

	type Row = { key: number; memberId: string; positionId: string; jersey: string; captain: boolean; alternate: boolean; error: string | null };
	let nextKey = 0;
	const blank = (): Row => ({ key: nextKey++, memberId: '', positionId: '', jersey: '', captain: false, alternate: false, error: null });
	let rows = $state<Row[]>([blank(), blank(), blank()]);
	let busy = $state(false);

	const filled = $derived(rows.filter((r) => r.memberId));
	// Payload: only rows with a person; staff rows carry no number or captain flags.
	const payload = $derived(
		JSON.stringify(
			filled.map((r) => ({
				member_id: r.memberId,
				position_id: r.positionId || null,
				jersey_number: !isStaff(r.positionId) && r.jersey !== '' ? Number(r.jersey) : null,
				is_captain: !isStaff(r.positionId) && r.captain,
				is_alternate_captain: !isStaff(r.positionId) && r.alternate && !r.captain
			}))
		)
	);
	// Only members with an active membership in the team's season; everyone found is that.
	const pickerQuery = $derived(`role=member&status=active${seasonId ? `&season=${seasonId}` : ''}`);
	const back = $derived(`/teams/${team?.id}${data.season ? `?season=${data.season}` : ''}`);

	function remove(key: number) {
		rows = rows.length > 1 ? rows.filter((r) => r.key !== key) : [blank()];
	}

	usePageHeader(() => ({ title: t('teampage.add.title'), context: team?.name ?? '', actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href={back}><ArrowLeft size={16} /> {t('teampage.add.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('teampage.add.title'))}</title></svelte:head>

{#if team}
	<form
		method="POST"
		action="?/add{data.season ? `&season=${data.season}` : ''}"
		class="card card-body gap-4"
		use:enhance={() => {
			busy = true;
			return async ({ result, update }) => {
				if (result.type === 'success' && (result.data as { result?: { rows: PersonResult[] } } | undefined)?.result) {
					// Keep only the rows that did not go in, each with its reason.
					const out = (result.data as { result: { rows: PersonResult[] } }).result.rows;
					const sent = filled;
					rows = sent.flatMap((r, i) => (out[i]?.ok ? [] : [{ ...r, error: out[i]?.error ?? null }]));
					if (!rows.length) rows = [blank()];
				} else await update({ reset: false });
				busy = false;
			};
		}}>
		<p class="text-sm text-muted">{t('teampage.add.intro')}</p>
		{#if form?.error}<Alert kind="error">{form.error}</Alert>{/if}
		{#if form?.result && form.result.added}<Alert kind="success">{t('teampage.add.partial', { n: form.result.added })}</Alert>{/if}
		<input type="hidden" name="rows" value={payload} />

		<div class="overflow-x-auto">
			<table class="table">
				<thead>
					<tr>
						<th class="min-w-64">{t('teampage.add.col.member')}</th>
						<th class="min-w-56">{t('teams.f.position')}</th>
						<th class="w-28">{t('teampage.add.col.jersey')}</th>
						<th class="w-24 text-center">{t('teampage.add.col.captain')}</th>
						<th class="w-24 text-center">{t('teampage.add.col.alternate')}</th>
						<th class="w-10"></th>
					</tr>
				</thead>
				<tbody>
					{#each rows as row, i (row.key)}
						{@const staff = isStaff(row.positionId)}
						<tr class={row.error ? 'bg-row-warn' : ''}>
							<td class="align-top">
								<EntityPicker kind="member" name="member_{row.key}" label={t('teampage.add.col.member')} compact extra={pickerQuery} bind:value={row.memberId} />
								{#if row.error}<span class="mt-1 block text-xs text-danger">{row.error}</span>{/if}
							</td>
							<td class="align-top">
								<select class="select w-full" aria-label={t('teams.f.position')} bind:value={row.positionId}>
									<option value="">{t('teampage.add.select')}</option>
									<optgroup label={t('teampage.add.player_positions')}>
										{#each playerPositions as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
									</optgroup>
									<optgroup label={t('teampage.add.staff_positions')}>
										{#each staffPositions as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
									</optgroup>
								</select>
							</td>
							<td class="align-top"><input class="input w-full" type="number" min="0" aria-label={t('teampage.add.col.jersey')} bind:value={row.jersey} disabled={staff} /></td>
							<td class="text-center align-middle"><input type="checkbox" class="checkbox" aria-label={t('teams.captain')} bind:checked={row.captain} disabled={staff} /></td>
							<td class="text-center align-middle"><input type="checkbox" class="checkbox" aria-label={t('teams.alt_captain')} bind:checked={row.alternate} disabled={staff || row.captain} /></td>
							<td class="align-middle"><button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('teampage.add.remove_row', { n: i + 1 })} onclick={() => remove(row.key)}><X size={16} /></button></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<div class="flex flex-wrap items-center gap-3">
			<button class="btn btn-outline gap-2" type="button" onclick={() => (rows = [...rows, blank()])}><Plus size={16} /> {t('teampage.add.another')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy || !filled.length}><UserPlus size={16} /> {t('teampage.add.submit')}{#if filled.length} ({filled.length}){/if}</button>
		</div>
	</form>
{/if}
