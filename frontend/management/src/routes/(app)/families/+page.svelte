<script lang="ts">
	import { untrack } from 'svelte';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { House, ChevronRight, Plus, Search, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { familyRoleLabel } from '#lib/members/detail-helpers.ts';
	import { FAMILY_ROLES } from '#lib/families/roles.ts';

	let { data, form } = $props();
	let addOpen = $state(false);
	let busy = $state(false);
	// svelte-ignore state_referenced_locally
	let q = $state(data.filters.q);
	$effect(() => {
		const external = data.filters.q;
		// react to the URL changing (clear, back), never to what is being typed
		untrack(() => {
			if (external !== q.trim()) q = external;
		});
	});
	let memberId = $state('');

	const href = (patch: Partial<{ q: string; role: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.role) sp.set('role', f.role);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	let timer: ReturnType<typeof setTimeout> | undefined;
	function onType() {
		clearTimeout(timer);
		timer = setTimeout(() => q.trim() !== data.filters.q && goto(href({ q: q.trim() }), { replace: true, reset: false }), 400);
	}
	const filtered = $derived(!!data.filters.q || !!data.filters.role);

	usePageHeader(() => ({ title: t('families.title'), context: data.count === 1 ? t('families.count_one') : t('families.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => (addOpen = true)}><Plus size={16} /> {t('families.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('families.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-6 py-2.5" role="search">
		<div class="relative">
			<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
			<input type="search" aria-label={t('families.search')} placeholder={t('families.search_placeholder')} class="input w-64 pl-9" bind:value={q} oninput={onType} />
		</div>
		<select class="select w-auto" aria-label={t('families.role')} value={data.filters.role} onchange={(e) => goto(href({ role: e.currentTarget.value }))}>
			<option value="">{t('families.role_any')}</option>
			{#each FAMILY_ROLES as r (r)}<option value={r}>{t('families.role_has', { role: familyRoleLabel(r) })}</option>{/each}
		</select>
		{#if filtered}
			<a class="btn btn-outline gap-2" href="?"><X size={14} /> {t('common.clear_filters')}</a>
		{/if}
	</div>

	<div class="px-6 py-4">
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<House size={28} />
				<p>{filtered ? t('families.empty_filtered') : t('families.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th>{t('families.col.family')}</th><th>{t('families.col.people')}</th><th class="text-right">{t('families.col.count')}</th><th></th></tr>
					</thead>
					<tbody>
						{#each data.rows as f (f.id)}
							<tr class="hover:bg-paper">
								<td class="font-semibold"><a class="link" href="/families/{f.id}">{f.display_name}</a></td>
								<td>
									<div class="flex flex-wrap gap-1.5">
										{#each f.members as m (m.member_id)}
											<a class="badge badge-outline gap-1" href="/members/{m.member_id}">{m.first_name} {m.last_name} <span class="text-muted">· {familyRoleLabel(m.role)}</span></a>
										{/each}
									</div>
								</td>
								<td class="text-right font-mono text-xs">{f.members.length}</td>
								<td class="text-right"><a class="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-rule hover:text-ink" href="/families/{f.id}" aria-label={t('families.edit_named', { name: f.display_name })} title={t('common.edit')}><ChevronRight size={18} /></a></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(offset) => href({ offset })} />
		{/if}
	</div>
</div>

<Modal bind:open={addOpen} title={t('families.add')}>
	<form
		method="POST"
		action="?/create"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
			};
		}}
	>
		{#if form?.familyAction === 'create' && form.familyError && !Object.keys(form.familyFields ?? {}).length}<Alert kind="error" title={t('families.create_failed')}>{form.familyError}</Alert>{/if}
		<Field label={t('families.name')} name="name" help={t('families.name_help')} error={form?.familyFields?.name} autocomplete="off" />
		<EntityPicker kind="member" name="member_id" label={t('families.first_person')} bind:value={memberId} error={form?.familyFields?.member_id} />
		{#if memberId}
			<div class="form-control w-full">
				<label class="label" for="fam-role"><span class="label-text">{t('families.role_in_family')}</span></label>
				<select id="fam-role" name="role" class="select select-bordered w-full">
					{#each FAMILY_ROLES as r (r)}<option value={r}>{familyRoleLabel(r)}</option>{/each}
				</select>
			</div>
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (addOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Plus size={16} /> {t('families.create')}</button>
		</div>
	</form>
</Modal>
