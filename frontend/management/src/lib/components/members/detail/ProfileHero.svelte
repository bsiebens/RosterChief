<script lang="ts">
	import { page } from '$app/state';
	import { Mail, Phone } from '@lucide/svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import { formatRate, rateTone, TONE, type MemberAttendance } from '#lib/attendance.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { fullName, statusBadge, statusLabel, telHref } from '#lib/members/detail-helpers.ts';
	import type { ClubMembership, MemberDetail } from '#lib/members/detail-types.ts';

	/** Profile header: club-coloured banner, big avatar, name, roles, contact chips and a strip of key figures. */
	let { member, current, attendance, docsDone, docsTotal, docsExpired = 0 }: { member: MemberDetail; current?: ClubMembership | null; attendance: MemberAttendance | null; docsDone: number; docsTotal: number; docsExpired?: number } = $props();
	const clubName = $derived((page.data as { branding?: { name?: string } }).branding?.name ?? '');
	const rate = $derived(attendance?.rate ?? null);
	const teams = $derived(new Set([...member.team_memberships.map((x) => x.team_id), ...member.staff_assignments.map((x) => x.team_id)]).size);
</script>

<section class="card overflow-hidden">
	<div class="flex h-24 items-center justify-end bg-primary px-8" style="background-image: linear-gradient(120deg, color-mix(in srgb, var(--color-club) 55%, var(--color-primary)) 0%, var(--color-primary) 62%);">
		{#if clubName}<span class="max-w-[75%] truncate font-display text-5xl leading-none font-extrabold tracking-[.1em] text-primary-content uppercase drop-shadow-sm sm:text-6xl lg:text-7xl">{clubName}</span>{/if}
	</div>
	<div class="px-6 pb-5">
		<div class="-mt-12 flex flex-wrap items-end gap-x-5 gap-y-3">
			<div class="flex rounded-full bg-white ring-4 ring-white">
				<Avatar first_name={member.first_name} last_name={member.last_name} photoUrl={member.photo_url} size="h-24 w-24" text="text-4xl" />
			</div>
			<div class="min-w-0 flex-1 pt-16 pb-1">
				<h2 class="truncate font-display text-2xl font-extrabold text-ink">{fullName(member)}</h2>
				<div class="mt-1.5 flex flex-wrap items-center gap-2">
					{#each member.roles as role (role)}<span class="badge badge-outline">{t(`members.detail.role.${role}`)}</span>{/each}
					{#if member.email}
						<a class="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink" href="mailto:{member.email}"><Mail size={14} /> {member.email}</a>
					{/if}
					{#if member.phone}
						<a class="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink" href={telHref(member.phone)}><Phone size={14} /> {member.phone_display ?? member.phone}</a>
					{/if}
				</div>
			</div>
		</div>

		<dl class="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line lg:grid-cols-4">
			<div class="bg-white p-4">
				<dt class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('profile.stat.membership')}</dt>
				<dd class="mt-1.5 flex items-center gap-2">
					{#if current}<span class="badge {statusBadge(current.status)}">{statusLabel(current.status)}</span>{:else}<span class="text-sm text-muted">{t('profile.stat.none')}</span>{/if}
				</dd>
				<p class="mt-1 text-xs text-muted">{t('profile.stat.this_season')}</p>
			</div>
			<div class="bg-white p-4">
				<dt class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('profile.stat.teams')}</dt>
				<dd class="mt-1 font-display text-2xl font-extrabold text-ink">{teams}</dd>
				<p class="mt-1 truncate text-xs text-muted">{[...member.team_memberships.map((x) => x.team_name), ...member.staff_assignments.map((x) => x.team_name)].filter((n, i, a) => a.indexOf(n) === i).join(', ') || '—'}</p>
			</div>
			<div class="bg-white p-4">
				<dt class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('profile.stat.attendance')}</dt>
				<dd class="mt-1 font-display text-2xl font-extrabold"><a class="hover:underline {rate === null ? 'text-muted' : TONE[rateTone(rate)].text}" href="?tab=attendance" data-sveltekit-noscroll data-sveltekit-keepfocus>{formatRate(rate)}</a></dd>
				{#if rate !== null}
					<div class="mt-1.5 h-1.5 overflow-hidden rounded-full bg-rule"><div class="h-full rounded-full {TONE[rateTone(rate)].bar}" style="width: {rate}%"></div></div>
				{:else}
					<p class="mt-1 text-xs text-muted">{attendance ? t('attbox.no_data') : t('attbox.failed')}</p>
				{/if}
			</div>
			<div class="bg-white p-4">
				<dt class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('profile.stat.documents')}</dt>
				<dd class="mt-1 font-display text-2xl font-extrabold {docsTotal ? (docsExpired ? 'text-danger' : 'text-ink') : 'text-muted'}">
					<a class="hover:underline" href="?tab=documents" data-sveltekit-noscroll data-sveltekit-keepfocus>{docsTotal ? t('profile.stat.documents_value', { done: docsDone, total: docsTotal }) : '–'}</a>
				</dd>
				{#if docsTotal}
					<div class="mt-1.5 h-1.5 overflow-hidden rounded-full bg-rule"><div class="h-full rounded-full {docsExpired ? 'bg-danger' : 'bg-club'}" style="width: {(docsDone / docsTotal) * 100}%"></div></div>
					{#if docsExpired}<p class="mt-1 text-xs text-danger">{t('doc.expired_n', { n: docsExpired })}</p>{/if}
				{:else}
					<p class="mt-1 text-xs text-muted">{t('profile.stat.documents_none')}</p>
				{/if}
			</div>
		</dl>
	</div>
</section>
