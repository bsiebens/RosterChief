<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { Camera, Check, ChevronRight, Shirt, Trash2, UserRound } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import FollowTeams from '#lib/components/portal/FollowTeams.svelte';
	import SaveStatus from '#lib/components/portal/SaveStatus.svelte';
	import StatsShare from '#lib/components/portal/StatsShare.svelte';
	import ProfileOverview from '#lib/components/portal/ProfileOverview.svelte';
	import { formatDay, parseIsoDate } from '#lib/events/types.ts';
	import PersonFilter from '#lib/components/PersonFilter.svelte';
	import Field from '#lib/components/Field.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { ProfilePerson } from '#lib/portal.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	// Me > Follow teams opens this page on that one block (`?view=follow`); the phone profile itself no longer carries it.
	// On a phone each of these is its own menu on Me and opens the profile page on just that block: `?view=follow|memberships|vouchers|clubs`.
	const view = $derived(page.url.searchParams.get('view') ?? '');
	const VIEW_TITLE: Record<string, string> = { follow: 'portal.follow_title', memberships: 'portal.mem_title', vouchers: 'portal.vouchers', clubs: 'portal.switch_club' };
	usePageHeader(() => ({ title: t(VIEW_TITLE[view] ?? 'portal.profile') }));

	// svelte-ignore state_referenced_locally
	let who = $state(data.people.find((person) => person.is_self)?.id ?? data.people[0]?.id ?? '');
	// One editable copy per person, made up front: state must not be changed while the page renders.
	const seed = () => Object.fromEntries(data.people.map((person) => [person.id, { ...person }])) as Record<string, ProfilePerson>;
	// svelte-ignore state_referenced_locally
	let drafts = $state<Record<string, ProfilePerson>>(seed());
	$effect(() => {
		drafts = seed();
	});
	let busy = $state('');
	let editing = $state(false);
	const teamLine = (person: ProfilePerson) => person.teams.map((team) => `${team.name}${team.jersey_number !== null ? ` · #${team.jersey_number}` : ''}`).join(', ');
	let saved = $state('');
	let errors = $state<Record<string, Record<string, string>>>({});
	let messages = $state<Record<string, string>>({});
	const fields = ['first_name', 'last_name', 'date_of_birth', 'phone', 'emergency_phone', 'photo_consent'] as const;
	const dirty = (person: ProfilePerson) => fields.some((key) => (drafts[person.id]?.[key] ?? '') !== (person[key] ?? ''));
	const reset = (person: ProfilePerson) => {
		drafts[person.id] = { ...person };
		errors[person.id] = {};
		messages[person.id] = '';
		saved = '';
	};
	const draft = (person: ProfilePerson) => drafts[person.id] ?? person;

	async function save(person: ProfilePerson) {
		const d = draft(person);
		busy = person.id;
		saved = '';
		errors[person.id] = {};
		messages[person.id] = '';
		const result = await api<ProfilePerson>('PATCH', `/portal/me/profile/${person.id}`, {
			body: { first_name: d.first_name, last_name: d.last_name, date_of_birth: d.date_of_birth || null, phone: d.phone, emergency_phone: d.emergency_phone, photo_public_consent: d.photo_consent }
		});
		busy = '';
		if (result.ok) {
			saved = person.id;
			await invalidateAll();
		} else {
			errors[person.id] = result.fields;
			messages[person.id] = result.message ?? t('common.error.generic');
		}
	}

	async function photo(person: ProfilePerson, file: File | undefined | null) {
		if (!file) return;
		const form = new FormData();
		form.append('photo', file);
		busy = person.id;
		const result = await api('POST', `/portal/me/profile/${person.id}/photo`, { form });
		busy = '';
		messages[person.id] = result.ok ? '' : (result.fields.photo ?? result.message ?? t('common.error.generic'));
		if (result.ok) await invalidateAll();
	}

	async function removePhoto(person: ProfilePerson) {
		busy = person.id;
		await api('DELETE', `/portal/me/profile/${person.id}/photo`);
		busy = '';
		await invalidateAll();
	}
</script>

<svelte:head><title>{pageTitle(t('portal.profile'))}</title></svelte:head>

{#snippet editor(person: ProfilePerson)}
	{@const d = draft(person)}
		<form class="card overflow-hidden" onsubmit={(e) => { e.preventDefault(); void save(person); }}>
			<div class="flex flex-wrap items-center gap-4 border-b border-line bg-subhead px-5 py-4">
				<ClubCrest name={person.name} logoUrl={person.photo_url} size={64} />
				<div class="min-w-0 flex-1">
					<h2 class="truncate font-display text-xl font-extrabold text-ink uppercase">{person.name}</h2>
					<p class="text-xs text-muted">{person.is_self ? t('portal.profile_you') : t('portal.profile_child')}</p>
				</div>
				<div class="flex flex-wrap gap-2">
					<label class="btn btn-outline btn-sm cursor-pointer gap-1.5"><Camera size={14} aria-hidden="true" />{t('portal.photo_change')}
						<input class="sr-only" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy === person.id} onchange={(e) => { void photo(person, e.currentTarget.files?.[0]); e.currentTarget.value = ''; }} />
					</label>
					{#if person.photo_url}<button class="btn btn-outline btn-error btn-sm gap-1.5" type="button" disabled={busy === person.id} onclick={() => removePhoto(person)}><Trash2 size={14} aria-hidden="true" />{t('portal.photo_remove')}</button>{/if}
				</div>
			</div>
			<div class="grid gap-4 p-5">
				{#if messages[person.id] && !Object.keys(errors[person.id] ?? {}).length}<Alert kind="error">{messages[person.id]}</Alert>{/if}
				<div class="grid gap-4 sm:grid-cols-2">
					<Field name="first_name" label={t('portal.first_name')} bind:value={d.first_name} error={errors[person.id]?.first_name} required />
					<Field name="last_name" label={t('portal.last_name')} bind:value={d.last_name} error={errors[person.id]?.last_name} required />
					<Field name="date_of_birth" type="date" label={t('portal.date_of_birth')} bind:value={d.date_of_birth as string} error={errors[person.id]?.date_of_birth} />
					<Field name="phone" type="tel" label={t('portal.phone')} bind:value={d.phone} error={errors[person.id]?.phone} />
					<Field name="emergency_phone" type="tel" label={t('portal.emergency_phone')} bind:value={d.emergency_phone} error={errors[person.id]?.emergency_phone} />
					<div class="grid content-start gap-1"><span class="text-sm font-semibold text-ink">{t('portal.email')}</span><div class="input flex items-center bg-subhead text-muted">{person.email || t('portal.email_managed')}</div></div>
				</div>
				<label class="flex items-start gap-2 text-sm"><input type="checkbox" class="checkbox mt-0.5" bind:checked={d.photo_consent} /><span>{t('portal.photo_consent')}</span></label>
				{#if person.teams.length}
					<div>
						<h3 class="mb-1.5 font-mono text-[11px] tracking-[.14em] text-muted uppercase">{t('portal.teams')}</h3>
						<ul class="flex flex-wrap gap-2">
							{#each person.teams as team (team.id)}
								<li class="inline-flex items-center gap-2 rounded-full border border-line px-3 py-1 text-sm"><Shirt size={14} class="text-muted" aria-hidden="true" /><span class="font-semibold text-ink">{team.name}</span>{#if team.jersey_number !== null}<span class="font-mono text-xs text-muted">#{team.jersey_number}</span>{/if}{#if team.captain}<span class="badge badge-sm badge-outline">C</span>{/if}</li>
							{/each}
						</ul>
					</div>
				{/if}
				<div class="flex flex-wrap items-center gap-3 border-t border-line pt-4">
					<button class="btn btn-primary" type="submit" disabled={busy === person.id || !dirty(person)}>{t('portal.save_changes')}</button>
					<button class="btn btn-outline" type="button" disabled={busy === person.id || !dirty(person)} onclick={() => reset(person)}>{t('common.cancel')}</button>
					<SaveStatus status={busy === person.id ? 'saving' : saved === person.id && !dirty(person) ? 'saved' : 'idle'} dirty={dirty(person)} compact />
				</div>
			</div>
		</form>
{/snippet}

<div class="grid min-w-0 gap-5 md:hidden">
	{#if view === 'follow'}
		{#if data.follows}<FollowTeams initial={data.follows} />{/if}
	{:else if view === 'memberships' || view === 'vouchers' || view === 'clubs'}
		{#if data.overview}<ProfileOverview overview={data.overview} people={data.people} show={[view]} />{/if}
	{:else}
	<section class="grid gap-2" aria-label={t('portal.family')}>
		<h2 class="font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.family_people')}</h2>
		<div class="card divide-y divide-rule overflow-hidden">
			{#each data.people as person (person.id)}
				<button type="button" class="flex min-h-17 w-full items-center gap-3 px-4 py-2 text-left" aria-pressed={who === person.id} onclick={() => { who = person.id; editing = false; }}>
					<ClubCrest name={person.name} logoUrl={person.photo_url} size={44} />
					<span class="min-w-0 flex-1"><b class="block truncate text-ink">{person.name}</b><span class="block truncate text-sm text-muted">{person.is_self ? t('portal.profile_you') : teamLine(person) || t('portal.profile_child')}</span></span>
					{#if who === person.id}<span class="grid size-6 place-items-center rounded-full bg-club text-club-content"><Check size={14} aria-hidden="true" /></span>{:else}<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />{/if}
				</button>
			{/each}
		</div>
	</section>

	{#each data.people.filter((person) => person.id === who) as person (person.id)}
		<section class="grid gap-2" aria-label={t('portal.details_of', { name: person.first_name })}>
			<h2 class="flex items-baseline font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.details_of', { name: person.first_name })}<button type="button" class="link-more ml-auto" onclick={() => (editing = !editing)}>{editing ? t('common.cancel') : t('common.edit')}</button></h2>
			{#if editing}
				{@render editor(person)}
			{:else}
				<div class="card divide-y divide-rule overflow-hidden">
					{#each [[t('portal.date_of_birth'), person.date_of_birth ? formatDay(parseIsoDate(person.date_of_birth) ?? person.date_of_birth) : '–'], [t('portal.email'), person.email || t('portal.email_managed')], [t('portal.phone'), person.phone || '–'], [t('portal.emergency_phone'), person.emergency_phone || '–']] as [label, value] (label)}
						<div class="flex min-h-14 flex-col justify-center px-4 py-2"><span class="text-sm text-muted">{label}</span><b class="break-words text-ink">{value}</b></div>
					{/each}
				</div>
				{#if teamLine(person)}<p class="px-0.5 text-sm text-muted">{teamLine(person)}</p>{/if}
			{/if}
		</section>

		<section class="grid gap-2">
			<h2 class="font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.permissions')}</h2>
			<div class="card overflow-hidden">
				<label class="flex min-h-16 items-center gap-3 px-4 py-2"><span class="min-w-0 flex-1"><b class="block text-ink">{t('portal.photo_consent_short')}</b><span class="block text-sm text-muted">{draft(person).photo_consent ? t('portal.allowed') : t('portal.not_allowed')}</span></span><input type="checkbox" class="toggle" checked={draft(person).photo_consent} disabled={busy === person.id} onchange={(e) => { drafts[person.id].photo_consent = e.currentTarget.checked; void save(person); }} /></label>
			</div>
		</section>
	{/each}

	{/if}
</div>

<div class="hidden md:block">
<div class="mb-4"><PersonFilter people={data.people.map((person) => ({ id: person.id, name: person.name, first_name: person.first_name, photo_url: person.photo_url }))} bind:selected={who} viewer={data.people.find((person) => person.is_self)?.id} all={false} /></div>
<div class="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
	<div class="grid min-w-0 content-start gap-4">
	{#each data.people.filter((person) => person.id === who) as person (person.id)}
		{@render editor(person)}
	{/each}
		{#if data.stats?.available}{@const mine = data.stats.people.find((candidate) => candidate.id === who)}{#if mine}<StatsShare person={mine} />{/if}{/if}
	</div>
	<div class="grid min-w-0 gap-4">{#if data.overview}<ProfileOverview overview={data.overview} people={data.people} />{/if}</div>
</div>
{#if data.follows}<div class="mt-4"><FollowTeams initial={data.follows} /></div>{/if}
</div>
