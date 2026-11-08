<script lang="ts">
	import { Building2, FilePenLine, Shield } from '@lucide/svelte';
	import Field from '#lib/components/Field.svelte';
	import RichTextEditor from '#lib/components/news/RichTextEditor.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { NEWS_VISIBILITIES, type NewsIn, type TeamOption } from '#lib/news.ts';

	/** The editable fields of a news item (content in both languages, visibility, teams), used by the add and edit pages. */
	let { v = $bindable(), teams, fields = {}, tried = false, disabled = false }: { v: NewsIn; teams: TeamOption[]; fields?: Record<string, string>; tried?: boolean; disabled?: boolean } = $props();
	const uid = $props.id();

	const err = $derived({
		title: fields.title || (tried && !v.title.trim() ? t('news.err.title_required') : ''),
		body: fields.body || (tried && !v.body.trim() ? t('news.err.body_required') : '')
	});
	const toggle = (id: string) => (v.team_ids = v.team_ids.includes(id) ? v.team_ids.filter((x) => x !== id) : [...v.team_ids, id]);
</script>

<fieldset class="grid gap-5" {disabled}>
	<section class="card card-body" aria-labelledby="{uid}-content">
		<header class="mb-3 grid gap-0.5">
			<h2 id="{uid}-content" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><FilePenLine size={16} aria-hidden="true" /> {t('news.card.content')}</h2>
			<p class="text-xs text-muted">{t('news.card.content_desc')}</p>
		</header>
		<div class="grid gap-4 md:grid-cols-2">
			<Field label={t('news.f.title')} bind:value={v.title} error={err.title} required maxlength={255} />
			<Field label={t('news.f.title_en')} bind:value={v.title_en} error={fields.title_en} help={t('news.f.title_en_help')} maxlength={255} />
			<div class="w-full">
				<RichTextEditor bind:value={v.body} label={t('news.f.body')} lang="nl" invalid={!!err.body} required describedby="{uid}-body-note" {disabled} />
				{#if err.body}<span id="{uid}-body-note" class="mt-1 block text-xs text-error">{err.body}</span>{:else}<span id="{uid}-body-note" class="label-text-alt mt-1 block">{t('news.f.markdown_help')}</span>{/if}
			</div>
			<div class="w-full">
				<RichTextEditor bind:value={v.body_en} label={t('news.f.body_en')} lang="en" invalid={!!fields.body_en} describedby="{uid}-body-en-note" {disabled} />
				{#if fields.body_en}<span id="{uid}-body-en-note" class="mt-1 block text-xs text-error">{fields.body_en}</span>{:else}<span id="{uid}-body-en-note" class="label-text-alt mt-1 block">{t('news.f.body_en_help')}</span>{/if}
			</div>
		</div>
	</section>

	<section class="card card-body" aria-labelledby="{uid}-audience">
		<header class="mb-3 grid gap-0.5">
			<h2 id="{uid}-audience" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Shield size={16} aria-hidden="true" /> {t('news.card.audience')}</h2>
			<p class="text-xs text-muted">{t('news.card.audience_desc')}</p>
		</header>
		<div class="grid gap-5">
			<fieldset class="grid gap-2">
				<legend class="mb-1 font-display text-sm font-bold tracking-wide text-ink uppercase">{t('news.visibility')}</legend>
				<div class="flex flex-wrap gap-2">
					{#each NEWS_VISIBILITIES as o (o)}
						<label class="flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-club {v.visibility === o ? 'border-primary bg-subhead font-semibold' : 'border-line'}">
							<input class="sr-only" type="radio" name="{uid}-visibility" value={o} bind:group={v.visibility} />
							{t(`news.visibility.${o}`)}
						</label>
					{/each}
				</div>
				<span class="label-text-alt">{fields.visibility || t('news.visibility_help')}</span>
			</fieldset>

			<fieldset class="grid gap-2">
				<legend class="mb-1 flex items-center gap-2 font-display text-sm font-bold tracking-wide text-ink uppercase">
					{t('news.f.teams')}
					{#if v.team_ids.length}<span class="font-mono text-xs font-normal tracking-normal text-muted normal-case">· {v.team_ids.length}</span>{/if}
				</legend>
				{#if !v.team_ids.length}
					<div class="flex items-center gap-2 text-sm text-muted"><Building2 size={15} aria-hidden="true" /> {t('news.f.teams_none')}</div>
				{/if}
				<div class="@container max-h-64 overflow-y-auto rounded-lg border p-2 {fields.team_ids ? 'border-danger' : 'border-line'}">
					<div class="grid grid-cols-1 gap-1 @[18rem]:grid-cols-2 @[32rem]:grid-cols-3">
						{#each teams as team (team.id)}
							<label class="flex min-w-0 cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-surface" title={team.name}>
								<input type="checkbox" class="checkbox" checked={v.team_ids.includes(team.id)} onchange={() => toggle(team.id)} />
								<span class="truncate">{team.name}</span>
							</label>
						{/each}
					</div>
				</div>
				<span class="label-text-alt {fields.team_ids ? 'text-error' : ''}">{fields.team_ids || t('news.f.teams_help')}</span>
			</fieldset>
		</div>
	</section>
</fieldset>
