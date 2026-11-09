<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ClipboardList, Copy, Lock, Pencil, Plus, Send, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import Field from '#lib/components/Field.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { formatDay } from '#lib/events/types.ts';
	import { formHref, sendFormHref, type FormListItem } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; active: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.active !== 'all') sp.set('active', f.active);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const navigate = (to: string) => goto(to, { reset: false });
	const filtered = $derived(!!data.filters.q || data.filters.active !== 'all');
	const tabList = $derived([
		{ key: 'all', label: t('forms.filter.all') },
		{ key: 'active', label: t('forms.filter.active') },
		{ key: 'inactive', label: t('forms.filter.inactive') }
	]);

	let busy = $state(false);
	let target = $state<FormListItem | null>(null);
	let dupTitle = $state('');
	let dupOpen = $state(false);
	let delOpen = $state(false);
	function askDuplicate(f: FormListItem) {
		target = f;
		dupTitle = t('forms.duplicate.default_title', { title: f.title });
		dupOpen = true;
	}
	function askDelete(f: FormListItem) {
		target = f;
		delOpen = true;
	}
	const deleted = $derived(page.url.searchParams.get('deleted') === '1');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const hasResponses = $derived(!!form && 'code' in form && form.code === 'has_responses');
	const failure = $derived(form && 'op' in form && !hasResponses && !('done' in form) ? String(form.message || t('common.error.generic')) : '');

	usePageHeader(() => ({ title: t('forms.title'), context: data.count === 1 ? t('forms.count_one') : t('forms.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-primary gap-2" href="/forms/new"><Plus size={16} /> {t('forms.add')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('forms.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar
		value={data.filters.q}
		searchLabel={t('forms.search')}
		searchPlaceholder={t('forms.search_placeholder')}
		onsearch={(q) => goto(href({ q }), { replace: true, reset: false })}
		showClear={filtered}
		onclear={() => navigate('?')}>
		{#snippet tabs()}
			<FilterTabs tabs={tabList} active={data.filters.active} label={t('forms.filter.label')} hrefFor={(key) => href({ active: key })} onnavigate={navigate} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-7 py-5">
		{#if deleted}<Alert kind="success">{t('forms.deleted')}</Alert>{/if}
		{#if done === 'delete'}<Alert kind="success">{t('forms.deleted')}</Alert>{/if}
		{#if done === 'deactivate'}<Alert kind="success">{t('forms.deactivated')}</Alert>{/if}
		{#if failure}<Alert kind="error" title={t('forms.action_failed')}>{failure}</Alert>{/if}
		{#if hasResponses && form && 'id' in form}
			{@const f = data.rows.find((r) => r.id === form.id)}
			<Alert kind="warning" title={t('forms.delete.blocked_title')}>
				<p>{t('forms.delete.blocked', { title: f?.title ?? '' })}</p>
				<form method="POST" action="?/deactivate" class="mt-2" use:enhance>
					<input type="hidden" name="id" value={form.id} />
					<button class="btn btn-outline btn-sm" type="submit">{t('forms.delete.deactivate')}</button>
				</form>
			</Alert>
		{/if}

		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<ClipboardList size={28} />
				<p>{filtered ? t('forms.empty_filtered') : t('forms.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th>{t('forms.col.form')}</th><th class="text-right">{t('forms.col.fields')}</th><th class="text-right">{t('forms.col.sends')}</th><th class="text-right">{t('forms.col.responses')}</th><th>{t('forms.col.created')}</th><th><span class="sr-only">{t('forms.col.actions')}</span></th></tr>
					</thead>
					<tbody>
						{#each data.rows as f (f.id)}
							<tr class="hover:bg-surface">
								<td>
									<a class="link font-semibold" href={formHref(f.id)}>{f.title}</a>
									<div class="mt-0.5 flex flex-wrap items-center gap-2">
										{#if !f.is_active}<span class="badge badge-sm badge-warning">{t('forms.inactive')}</span>{/if}
										{#if f.login_required}<span class="badge badge-sm gap-1"><Lock size={10} aria-hidden="true" /> {t('forms.login_required')}</span>{/if}
									</div>
								</td>
								<td class="text-right font-mono text-sm">{f.field_count}</td>
								<td class="text-right font-mono text-sm">{f.send_count}</td>
								<td class="text-right font-mono text-sm">{f.response_count}</td>
								<td class="font-mono text-xs whitespace-nowrap text-muted">{formatDay(f.created)}</td>
								<td class="text-right whitespace-nowrap">
									<div class="flex justify-end gap-1">
										<a class="btn btn-outline btn-xs gap-1" href={formHref(f.id)} aria-label={t('forms.edit_named', { title: f.title })}><Pencil size={13} /> {t('common.edit')}</a>
										{#if f.is_active}<a class="btn btn-outline btn-xs gap-1" href={sendFormHref(f.id)} aria-label={t('forms.send_named', { title: f.title })}><Send size={13} /> {t('forms.send')}</a>{/if}
										<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('forms.duplicate_named', { title: f.title })} onclick={() => askDuplicate(f)}><Copy size={13} /></button>
										<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('forms.delete_named', { title: f.title })} onclick={() => askDelete(f)}><Trash2 size={13} /></button>
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>

<Modal bind:open={dupOpen} title={t('forms.duplicate.title')}>
	<form
		method="POST"
		action="?/duplicate"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update, result }) => {
				await update({ reset: false });
				busy = false;
				if (result.type !== 'redirect') dupOpen = false;
			};
		}}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<p class="text-sm text-muted">{t('forms.duplicate.body')}</p>
		<Field label={t('forms.f.title')} name="title" bind:value={dupTitle} maxlength={255} />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (dupOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Copy size={16} /> {t('forms.duplicate')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delOpen} title={t('forms.delete.title')}>
	<form
		method="POST"
		action="?/delete"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
				delOpen = false;
			};
		}}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<p>{t('forms.delete.body', { title: target?.title ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
