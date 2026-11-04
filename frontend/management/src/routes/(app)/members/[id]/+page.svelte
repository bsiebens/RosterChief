<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { History, KeyRound, Pencil } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FamiliesCard from '#lib/components/members/detail/FamiliesCard.svelte';
	import GrantResult from '#lib/components/members/detail/GrantResult.svelte';
	import GrantLoginModal from '#lib/components/members/detail/GrantLoginModal.svelte';
	import GroupsCard from '#lib/components/members/detail/GroupsCard.svelte';
	import MembershipCard from '#lib/components/members/detail/MembershipCard.svelte';
	import PicturesCard from '#lib/components/members/detail/PicturesCard.svelte';
	import PersonalCard from '#lib/components/members/detail/PersonalCard.svelte';
	import EligibilityCard from '#lib/components/members/detail/EligibilityCard.svelte';
	import TeamsCard from '#lib/components/members/detail/TeamsCard.svelte';
	import AttendanceCard from '#lib/components/members/detail/AttendanceCard.svelte';
	import DocumentsCard from '#lib/components/members/detail/DocumentsCard.svelte';
	import EvaluationsCard from '#lib/components/members/detail/EvaluationsCard.svelte';
	import ProfileHero from '#lib/components/members/detail/ProfileHero.svelte';
	import { page } from '$app/state';
	import { canExecuteEvaluations } from '#lib/permissions.ts';
	import { attendanceSummary, documentsDone, mockDocuments, mockSessions } from '#lib/members/mock.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { currentMembership, fullName, statusBadge, statusLabel } from '#lib/members/detail-helpers.ts';

	let { data, form } = $props();
	const member = $derived(data.member);
	// While navigating away (back to the list) `data` briefly belongs to the next page: `member` is undefined then.
	const current = $derived(member ? currentMembership(member.club_memberships, data.seasons) : null);
	let grantOpen = $state(false);

	const tabs = $derived([
		{ key: 'overview', label: t('profile.tab.overview') },
		...(canExecuteEvaluations(data.me) ? [{ key: 'evaluations', label: t('profile.tab.evaluations') }] : []),
		{ key: 'documents', label: t('profile.tab.documents') },
		{ key: 'attendance', label: t('profile.tab.attendance') }
	]);
	const tab = $derived(tabs.find((x) => x.key === page.url.searchParams.get('tab'))?.key ?? 'overview');
	// Placeholder figures for the hero until the real evaluations, documents and attendance exist.
	const docs = mockDocuments();
	const attendance = attendanceSummary(mockSessions());

	usePageHeader(() => ({
		title: (member && fullName(member)) || t('members.detail.title.fallback'),
		context: member?.email ?? '',
		badge: current ? statusPill : null,
		actions: member && data.isAdmin ? actions : null
	}));
</script>

{#snippet statusPill()}
	{#if current}<span class="badge {statusBadge(current.status)} self-center">{statusLabel(current.status)}</span>{/if}
{/snippet}

{#snippet actions()}
	{#if member}
	{#if !member.has_login}
		<button class="btn btn-outline gap-2" type="button" onclick={() => (grantOpen = true)}><KeyRound size={16} /> {t('members.detail.grant_login')}</button>
	{/if}
	<a class="btn btn-outline gap-2" href="/members/{member.id}/history"><History size={16} /> {t('members.detail.history')}</a>
	<a class="btn btn-outline gap-2" href="/members/{member.id}/edit"><Pencil size={16} /> {t('members.detail.edit')}</a>
	{/if}
{/snippet}

<svelte:head><title>{pageTitle((member && fullName(member)) || t('members.detail.title.fallback'))}</title></svelte:head>

{#if member}
{#if form?.granted}<GrantResult granted={form.granted} />{/if}

<div class="grid gap-4">
	<ProfileHero {member} {current} attendance={attendance.rate} docsDone={documentsDone(docs)} docsTotal={docs.length} />

	<nav class="card flex flex-wrap items-center justify-between gap-x-3 px-3" aria-label={t('profile.tabs')}>
		<div class="flex flex-wrap">
			{#each tabs as item (item.key)}
				<a
					href="?tab={item.key}"
					data-sveltekit-noscroll
					data-sveltekit-keepfocus
					aria-current={tab === item.key ? 'page' : undefined}
					class="-mb-px border-b-2 px-4 py-3.5 font-display text-sm font-bold tracking-[.08em] whitespace-nowrap uppercase {tab === item.key ? 'border-club text-ink' : 'border-transparent text-muted hover:text-ink'}">{item.label}</a>
			{/each}
		</div>
		{#if tab !== 'overview'}<span class="badge badge-outline badge-sm shrink-0" title={t('profile.sample_hint')}>{t('profile.sample')}</span>{/if}
	</nav>

	{#if tab === 'evaluations'}
		<EvaluationsCard />
	{:else if tab === 'documents'}
		<DocumentsCard canManage={data.isAdmin} />
	{:else if tab === 'attendance'}
		<AttendanceCard />
	{:else}
		<div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_34rem]">
			<div class="grid gap-4">
				<PersonalCard {member} guardians={data.guardians} />
				<MembershipCard {member} seasons={data.seasons} isAdmin={data.isAdmin} {form} />
				{#if data.hasOfficiating}
					<div class="grid gap-4 md:grid-cols-2">
						<EligibilityCard kind="referee" profile={data.officiating.referee} levels={data.officiatingOptions.referee_levels} teams={data.officiatingOptions.teams} {form} canEdit={data.isAdmin} />
						<EligibilityCard kind="official" profile={data.officiating.official} levels={data.officiatingOptions.official_levels} teams={data.officiatingOptions.teams} {form} canEdit={data.isAdmin} />
					</div>
				{/if}
			</div>
			<div class="grid gap-4">
				{#if data.isAdmin}<PicturesCard {member} />{/if}
				<FamiliesCard {member} families={data.families} isAdmin={data.isAdmin} {form} />
				<TeamsCard {member} seasons={data.seasons} />
				{#if data.isAdmin}<GroupsCard groups={member.groups} />{/if}
			</div>
		</div>
	{/if}
</div>

{#if data.isAdmin}<GrantLoginModal bind:open={grantOpen} email={member.email} name={fullName(member)} error={form?.grantError} fields={form?.grantFields ?? {}} />{/if}
{/if}
