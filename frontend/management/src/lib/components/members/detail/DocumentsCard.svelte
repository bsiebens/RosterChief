<script lang="ts">
	import { CircleAlert, CircleCheck, Clock, Eye, FileText, FileX, Send } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import { documentsDone, mockDocuments, type DocStatus } from '#lib/members/mock.ts';

	/** Sample content: the onboarding paperwork checklist. Everyone sees the status; opening a document or sending a reminder needs members.manage_members. */
	let { canManage = false }: { canManage?: boolean } = $props();
	const docs = mockDocuments();
	const done = documentsDone(docs);
	const look: Record<DocStatus, { badge: string; tint: string; icon: typeof CircleCheck }> = {
		signed: { badge: 'badge-success', tint: 'text-ok-text', icon: CircleCheck },
		pending: { badge: 'badge-warning', tint: 'text-warn-text', icon: Clock },
		expired: { badge: 'badge-error', tint: 'text-danger', icon: CircleAlert },
		missing: { badge: 'badge-ghost', tint: 'text-muted', icon: FileX }
	};
	const detail = (d: (typeof docs)[number]) =>
		d.status === 'signed' ? t('profile.docs.signed_on', { date: formatDate(d.date) }) : d.status === 'expired' ? t('profile.docs.expired_on', { date: formatDate(d.due) }) : d.status === 'pending' ? t('profile.docs.due', { date: formatDate(d.due) }) : t('profile.docs.not_received');
</script>

<div class="card card-body sample-stripes">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div>
			<h2 class="card-title"><FileText size={18} /> {t('profile.docs.title')}</h2>
			<p class="text-sm text-muted">{t('profile.docs.subtitle')} · {t('profile.docs.progress', { done, total: docs.length })}</p>
		</div>
		<div class="h-2 w-48 overflow-hidden rounded-full bg-rule"><div class="h-full rounded-full bg-club" style="width: {(done / docs.length) * 100}%"></div></div>
	</div>
	<ul class="mt-3 grid gap-3 md:grid-cols-2">
		{#each docs as d (d.key)}
			{@const l = look[d.status]}
			<li class="flex items-center gap-3 rounded-lg border border-line p-3.5">
				<span class="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-surface {l.tint}"><l.icon size={20} /></span>
				<div class="min-w-0 flex-1">
					<p class="truncate font-semibold text-ink">{t(`profile.docs.${d.key}`)}</p>
					<p class="truncate text-xs text-muted">{detail(d)}</p>
				</div>
				<div class="flex shrink-0 items-center gap-2">
					<span class="badge badge-sm {l.badge}">{t(`profile.docs.status.${d.status}`)}</span>
					{#if canManage}
						<button class="btn btn-outline btn-xs gap-1" type="button">
							{#if d.status === 'signed'}<Eye size={13} /> {t('profile.docs.view')}{:else}<Send size={13} /> {t('profile.docs.remind')}{/if}
						</button>
					{/if}
				</div>
			</li>
		{/each}
	</ul>
</div>
