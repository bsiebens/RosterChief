<script lang="ts">
	import { IdCard, KeyRound, UsersRound, Plus, Trash2 } from '@lucide/svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Season } from '#lib/members/detail-types.ts';

	/**
	 * The extra steps of adding a person by hand: a season membership (active now, or pending in the sign-up queue), a family to join or
	 * guardians to add, and a login invite. The choices are posted as one hidden JSON field (`ob_payload`); everything is optional, so a
	 * form left alone adds just the person.
	 */
	let { seasons, email = '' }: { seasons: Season[]; email?: string } = $props();
	type Guardian = { uid: number; mode: 'existing' | 'new'; member_id: string; first_name: string; last_name: string; email: string; invite: boolean };

	const current = $derived(seasons.find((s) => s.is_current) ?? null);
	let enroll = $state(true);
	let seasonId = $state('');
	let status = $state<'active' | 'pending'>('active');
	let fee = $state('');
	let waived = $state(false);
	let familyMode = $state<'none' | 'join' | 'guardians'>('none');
	let familyId = $state('');
	let familyRole = $state<'child' | 'parent' | 'guardian' | 'other'>('child');
	let guardians = $state<Guardian[]>([]);
	let invite = $state(false);
	let next = 1;

	const noSeason = $derived(!current && !seasons.length);
	const addGuardian = () => (guardians = [...guardians, { uid: next++, mode: 'existing', member_id: '', first_name: '', last_name: '', email: '', invite: false }]);
	$effect(() => {
		if (familyMode === 'guardians' && !guardians.length) addGuardian();
	});

	const payload = $derived(
		JSON.stringify({
			membership: enroll && (current || seasonId) ? { season_id: seasonId || null, status, fee_amount: status === 'pending' && !waived ? fee || '0' : '0', waived: status === 'pending' && waived } : null,
			family: familyMode === 'join' && familyId ? { family_id: familyId, role: familyRole } : null,
			guardians:
				familyMode === 'guardians'
					? guardians
							.filter((g) => (g.mode === 'existing' ? g.member_id : g.first_name.trim() && g.last_name.trim()))
							.map((g) => (g.mode === 'existing' ? { member_id: g.member_id } : { first_name: g.first_name.trim(), last_name: g.last_name.trim(), email: g.email.trim(), invite: g.invite && !!g.email.trim() }))
					: [],
			invite: invite ? { send: true } : null
		})
	);
</script>

<input type="hidden" name="ob_payload" value={payload} />

<h2 class="mt-6 mb-3 border-b border-rule pb-1 font-display text-xs font-bold tracking-[.1em] text-muted uppercase flex items-center gap-2"><IdCard size={16} class="shrink-0" aria-hidden="true" /> {t('members.form.section.membership')}</h2>
{#if noSeason}<Alert kind="warning">{t('members.form.no_season')}</Alert>{/if}
<div class="grid gap-3" aria-disabled={noSeason}>
	<label class="flex cursor-pointer items-center gap-3 text-sm">
		<input type="checkbox" class="checkbox" bind:checked={enroll} disabled={noSeason} />
		<span>{t('members.form.enroll')}</span>
	</label>
	{#if enroll && !noSeason}
		<div class="grid gap-3 md:grid-cols-2">
			<div class="form-control">
				<label class="label" for="ob-season"><span class="label-text">{t('members.form.season')}</span></label>
				<select id="ob-season" class="select select-bordered w-full" bind:value={seasonId}>
					<option value="">{current ? t('members.form.season_current', { name: current.name }) : t('members.form.season_pick')}</option>
					{#each seasons as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
				</select>
			</div>
			<fieldset class="grid gap-1">
				<legend class="label-text mb-1">{t('members.form.status')}</legend>
				<label class="flex cursor-pointer items-start gap-2 text-sm"><input type="radio" class="radio radio-sm mt-0.5" bind:group={status} value="active" /><span>{t('members.form.status_active')}<span class="block text-xs text-muted">{t('members.form.status_active_help')}</span></span></label>
				<label class="flex cursor-pointer items-start gap-2 text-sm"><input type="radio" class="radio radio-sm mt-0.5" bind:group={status} value="pending" /><span>{t('members.form.status_pending')}<span class="block text-xs text-muted">{t('members.form.status_pending_help')}</span></span></label>
			</fieldset>
		</div>
		{#if status === 'pending'}
			<div class="grid items-end gap-3 md:grid-cols-2">
				<div class="form-control">
					<label class="label" for="ob-fee"><span class="label-text">{t('members.form.fee')}</span></label>
					<input id="ob-fee" class="input input-bordered w-full" type="number" min="0" step="0.01" bind:value={fee} disabled={waived} placeholder="0.00" />
				</div>
				<label class="flex cursor-pointer items-center gap-2 pb-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={waived} /> {t('members.form.waived')}</label>
			</div>
		{/if}
	{/if}
</div>

<h2 class="mt-6 mb-3 border-b border-rule pb-1 font-display text-xs font-bold tracking-[.1em] text-muted uppercase flex items-center gap-2"><UsersRound size={16} class="shrink-0" aria-hidden="true" /> {t('members.form.section.family')}</h2>
<div class="grid gap-3">
	<fieldset class="flex flex-wrap gap-x-5 gap-y-1">
		<legend class="sr-only">{t('members.form.section.family')}</legend>
		{#each ['none', 'join', 'guardians'] as m (m)}
			<label class="flex cursor-pointer items-center gap-2 text-sm"><input type="radio" class="radio radio-sm" bind:group={familyMode} value={m} /> {t(`members.form.family_${m}`)}</label>
		{/each}
	</fieldset>
	{#if familyMode === 'join'}
		<div class="grid items-end gap-3 md:grid-cols-2">
			<EntityPicker kind="family" name="_ob_family" label={t('members.form.family_pick')} bind:value={familyId} />
			<div class="form-control">
				<label class="label" for="ob-role"><span class="label-text">{t('members.form.family_role')}</span></label>
				<select id="ob-role" class="select select-bordered w-full" bind:value={familyRole}>
					{#each ['child', 'parent', 'guardian', 'other'] as r (r)}<option value={r}>{t(`members.form.role.${r}`)}</option>{/each}
				</select>
			</div>
		</div>
	{:else if familyMode === 'guardians'}
		<p class="text-xs text-muted">{t('members.form.guardians_help')}</p>
		{#each guardians as g (g.uid)}
			<div class="grid gap-3 rounded-lg border border-line p-3">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<fieldset class="flex gap-4">
						<legend class="sr-only">{t('members.form.guardian')}</legend>
						<label class="flex cursor-pointer items-center gap-2 text-sm"><input type="radio" class="radio radio-sm" bind:group={g.mode} value="existing" /> {t('members.form.guardian_existing')}</label>
						<label class="flex cursor-pointer items-center gap-2 text-sm"><input type="radio" class="radio radio-sm" bind:group={g.mode} value="new" /> {t('members.form.guardian_new')}</label>
					</fieldset>
					<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('members.form.guardian_remove')} onclick={() => (guardians = guardians.filter((x) => x.uid !== g.uid))}><Trash2 size={13} /></button>
				</div>
				{#if g.mode === 'existing'}
					<EntityPicker kind="member" name="_ob_guardian_{g.uid}" label={t('members.form.guardian_pick')} bind:value={g.member_id} />
				{:else}
					<div class="grid gap-3 md:grid-cols-3">
						<div class="form-control"><label class="label" for="g-f-{g.uid}"><span class="label-text">{t('members.form.first_name')}</span></label><input id="g-f-{g.uid}" class="input input-bordered w-full" bind:value={g.first_name} autocomplete="off" /></div>
						<div class="form-control"><label class="label" for="g-l-{g.uid}"><span class="label-text">{t('members.form.last_name')}</span></label><input id="g-l-{g.uid}" class="input input-bordered w-full" bind:value={g.last_name} autocomplete="off" /></div>
						<div class="form-control"><label class="label" for="g-e-{g.uid}"><span class="label-text">{t('members.form.email')}</span></label><input id="g-e-{g.uid}" type="email" class="input input-bordered w-full" bind:value={g.email} autocomplete="off" /></div>
					</div>
					<label class="flex w-fit cursor-pointer items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={g.invite} disabled={!g.email.trim()} /> {t('members.form.guardian_invite')}</label>
				{/if}
			</div>
		{/each}
		<button class="btn btn-outline btn-sm w-fit gap-1" type="button" onclick={addGuardian}><Plus size={14} /> {t('members.form.guardian_add')}</button>
	{/if}
</div>

<h2 class="mt-6 mb-3 border-b border-rule pb-1 font-display text-xs font-bold tracking-[.1em] text-muted uppercase flex items-center gap-2"><KeyRound size={16} class="shrink-0" aria-hidden="true" /> {t('members.form.section.login')}</h2>
<label class="flex cursor-pointer items-start gap-3 text-sm">
	<input type="checkbox" class="checkbox mt-0.5" bind:checked={invite} />
	<span>{t('members.form.invite')}<span class="block text-xs {invite && !email.trim() ? 'text-error' : 'text-muted'}">{invite && !email.trim() ? t('members.form.invite_needs_email') : t('members.form.invite_help')}</span></span>
</label>
