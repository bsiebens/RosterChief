<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { Search, UserCheck, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import { kindIcon } from '#lib/officiating-icons.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';

	let { data } = $props();

	const KINDS = ['all', 'referee', 'official'] as const;
	const f = $derived(data.filters);
	// svelte-ignore state_referenced_locally
	let q = $state(data.filters.q);
	$effect(() => {
		const external = f.q;
		// react to the URL changing (clear, back), never to what is being typed
		untrack(() => {
			if (external !== q.trim()) q = external;
		});
	});

	const levels = $derived(f.kind === 'referee' ? data.refereeLevels : f.kind === 'official' ? data.officialLevels : [...data.refereeLevels, ...data.officialLevels]);

	function href(change: Partial<typeof f>) {
		const next = { ...f, ...change };
		const sp = new URLSearchParams();
		if (next.kind !== 'all') sp.set('kind', next.kind);
		if (next.q) sp.set('q', next.q);
		if (next.level) sp.set('level', next.level);
		if (next.valid !== 'all') sp.set('valid', next.valid);
		const s = sp.toString();
		return s ? `?${s}` : '?';
	}
	const navigate = (to: string, replace = false) => goto(to, { reset: false, replace });
	let timer: ReturnType<typeof setTimeout> | undefined;
	function onType() {
		clearTimeout(timer);
		timer = setTimeout(() => {
			if (q.trim() !== f.q) navigate(href({ q: q.trim() }), true);
		}, 300);
	}
	const pick = (key: 'level' | 'valid') => (e: Event) => navigate(href({ [key]: (e.currentTarget as HTMLSelectElement).value }));
	const hasFilters = $derived(!!(f.q || f.level || f.valid !== 'all' || f.kind !== 'all'));

	usePageHeader(() => ({ title: t('officials.title'), context: data.people.length === 1 ? t('officials.count_one') : t('officials.count', { n: data.people.length }) }));
</script>

<svelte:head><title>{pageTitle(t('officials.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-6 py-2.5">
		<div class="flex flex-wrap items-center gap-2" role="search">
			<div class="relative">
				<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
				<input type="search" aria-label={t('officials.search')} placeholder={t('officials.search_placeholder')} class="input w-56 pl-9" bind:value={q} oninput={onType} />
			</div>
			<select class="select w-auto" aria-label={t('officials.level')} value={f.level} onchange={pick('level')}>
				<option value="">{t('officials.level_any')}</option>
				{#each levels as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
			</select>
			<select class="select w-auto" aria-label={t('officials.valid')} value={f.valid} onchange={pick('valid')}>
				<option value="all">{t('officials.valid_any')}</option>
				<option value="valid">{t('officials.valid_valid')}</option>
				<option value="expired">{t('officials.valid_expired')}</option>
			</select>
			{#if hasFilters}
				<a class="btn btn-outline gap-2" href="?" onclick={(e) => { e.preventDefault(); navigate('?'); }}><X size={14} /> {t('common.clear_filters')}</a>
			{/if}
		</div>
		<div class="flex-1"></div>
		<div class="flex items-center gap-1 " role="group" aria-label={t('officials.kind_label')}>
			{#each KINDS as kind (kind)}
				<a
					href={href({ kind, level: '' })}
					aria-current={f.kind === kind ? 'true' : undefined}
					onclick={(e) => { e.preventDefault(); navigate(href({ kind, level: '' })); }}
					class="rounded-md px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {f.kind === kind ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}">{t(`officials.kind.${kind}`)}</a>
			{/each}
		</div>
		<InfoTip label={t('officials.info_label')} text={t('officials.info')} align="right" />
	</div>

	<div class="grid gap-4 px-6 py-4">
		<p class="flex flex-wrap items-center gap-2 text-xs text-muted"><span class="badge badge-sm badge-outline">{t('officials.legend_own')}</span><span class="badge badge-sm border-dashed border-club bg-club/10 text-club-dark">{t('officials.legend_inherited')}</span></p>
		{#if data.error}
			<Alert kind="error">{t('officials.load_failed')}</Alert>
		{:else if !data.people.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<UserCheck size={28} />
				<p>{t('officials.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th class="whitespace-nowrap">{t('officials.col.name')}</th><th class="hidden md:table-cell">{t('officials.col.kind')}</th><th class="whitespace-nowrap">{t('officials.col.level')}</th><th class="whitespace-nowrap">{t('officials.col.valid_until')}</th><th class="hidden lg:table-cell">{t('officials.col.teams')}</th></tr>
					</thead>
					<tbody>
						{#each data.people as p (p.member_id + p.kind)}
						{@const KindIcon = kindIcon[p.kind]}
							<tr class="hover:bg-paper">
								<td class="whitespace-nowrap">
									<a class="group flex items-center gap-3 font-semibold" href="/members/{p.member_id}">
										<Avatar first_name={p.first_name} last_name={p.last_name} photoUrl={p.photo_url} />
										<span class="group-hover:underline">{p.first_name} {p.last_name}</span>
									</a>
								</td>
								<td class="hidden md:table-cell"><span class="badge badge-sm badge-ghost gap-1"><KindIcon size={11} aria-hidden="true" /> {t(`officials.kind_pill.${p.kind}`)}</span></td>
								<td class="whitespace-nowrap">{p.level_name ?? t('common.none')}</td>
								<td class="whitespace-nowrap">
									<div class="flex items-center gap-2">
										<span class="font-mono text-xs text-muted">{p.valid_until ?? t('common.none')}</span>
										<span class="badge badge-sm {p.is_valid ? 'badge-success' : 'badge-error'}">{p.is_valid ? t('officials.status.valid') : t('officials.status.expired')}</span>
									</div>
								</td>
								<td class="hidden lg:table-cell">
									{#if p.eligible_team_names.length || !(p.kind === 'referee' && p.excluded_team_names.length)}
										<div class="flex flex-wrap items-center gap-1">
											{#each p.eligible_team_names as n (n)}
												{#if p.inherited_team_names?.includes(n)}<span class="badge badge-sm border-dashed border-club bg-club/10 text-club-dark" title={t('officials.inherited')}>{n}</span>{:else}<span class="badge badge-sm badge-outline">{n}</span>{/if}
											{/each}
											{#if !p.eligible_team_names.length}<span class="text-sm text-muted">—</span>{/if}
										</div>
									{/if}
									{#if p.kind === 'referee' && p.excluded_team_names.length}
										<div class="flex flex-wrap items-center gap-1 {p.eligible_team_names.length ? 'mt-1.5' : ''}">
											<span class="text-xs text-muted">{t('officials.excluded')}</span>
											{#each p.excluded_team_names as n (n)}<span class="badge badge-sm badge-outline border-error text-error">{n}</span>{/each}
										</div>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>
