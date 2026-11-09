<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ChevronDown, ChevronRight, Lock, Pencil, Plus, ShieldCheck, Trash2, UserMinus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { roleHref, type Role, type RoleDetail } from '#lib/roles.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const tabs = $derived([{ key: 'roles', label: t('roles.tab.roles') }, { key: 'people', label: t('roles.tab.people') }]);
	const navigate = (to: string) => goto(to, { reset: false });

	// Every role starts closed; opening one shows who holds it. `?open=<role id>` opens one straight away.
	let opened = $state<Record<string, boolean>>({});
	$effect(() => {
		if (data.open) opened[data.open] = true;
	});

	let addOpen = $state(false);
	let deleteOpen = $state(false);
	let deleting = $state<Role | null>(null);
	let target = $state<RoleDetail | null>(null);
	let picked = $state('');
	let busy = $state(false);
	function askAdd(r: RoleDetail) {
		target = r;
		picked = '';
		addOpen = true;
	}
	const failure = $derived(form && 'message' in form && form.op && !('done' in form) ? String(form.message || t('common.error.generic')) : '');
	const submit = () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') addOpen = false;
		};
	};

	usePageHeader(() => ({ title: t('roles.title'), context: t('roles.count', { n: data.roles.length }), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-primary gap-2" href="/settings/roles/new"><Plus size={16} /> {t('roles.add')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('roles.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-7 py-2.5">
		<div class="flex-1"></div>
		<FilterTabs {tabs} active={data.tab} label={t('roles.tab.label')} hrefFor={(key) => (key === 'roles' ? '?' : `?tab=${key}`)} onnavigate={navigate} />
		<InfoTip label={t('roles.info_label')} text={data.tab === 'people' ? t('roles.people_intro') : t('roles.intro')} align="right" />
	</div>

	<div class="grid gap-4 px-7 py-5">
	{#if page.url.searchParams.get('deleted') === '1' || (form && 'done' in form && form.done === 'delete')}<Alert kind="success">{t('roles.deleted')}</Alert>{/if}
	{#if failure}<Alert kind="error" title={t('roles.action_failed')}>{failure}</Alert>{/if}

	{#if data.error}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if data.tab === 'roles'}
		<div class="card overflow-x-auto">
			<table class="table">
				<thead><tr><th>{t('roles.col.role')}</th><th class="text-right">{t('roles.col.permissions')}</th><th class="text-right">{t('roles.col.people')}</th><th><span class="sr-only">{t('roles.col.actions')}</span></th></tr></thead>
				<tbody>
					{#each data.roles as r (r.id)}
						<tr class="hover:bg-surface">
							<td>
								<a class="link font-semibold" href={roleHref(r.id)}>{r.name}</a>
								{#if r.is_system}<span class="badge badge-sm ml-2 gap-1"><Lock size={10} aria-hidden="true" /> {t('roles.system')}</span>{/if}
								{#if r.description}<p class="text-sm text-muted">{r.description}</p>{/if}
							</td>
							<td class="text-right font-mono text-sm">{r.is_owner ? t('roles.all') : r.permissions.length}</td>
							<td class="text-right font-mono text-sm">{r.member_count}</td>
							<td class="text-right whitespace-nowrap">
								<div class="flex justify-end gap-1">
									<a class="btn btn-outline btn-xs gap-1" href={roleHref(r.id)} aria-label={t('roles.edit_named', { name: r.name })}><Pencil size={13} /> {t('common.edit')}</a>
									{#if !r.is_system}<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('roles.delete_named', { name: r.name })} onclick={() => ((deleting = r), (deleteOpen = true))}><Trash2 size={13} /></button>{/if}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if !data.roles.length}<div class="card card-body items-center py-10 text-center text-muted"><ShieldCheck size={28} /><p>{t('roles.empty')}</p></div>{/if}
	{:else}
		<div class="card overflow-x-auto">
			<table class="table w-full table-fixed">
				<colgroup><col /><col class="w-64" /><col class="w-40" /></colgroup>
				{#each data.people as r (r.id)}
					<tbody>
						<tr>
							<th colspan="3" class="bg-subhead p-0 text-left">
								<div class="flex items-center">
									<button type="button" class="flex min-w-0 flex-1 items-center gap-2 px-4 py-2 text-left hover:bg-paper" aria-expanded={!!opened[r.id]} onclick={() => (opened[r.id] = !opened[r.id])}>
										{#if opened[r.id]}<ChevronDown size={16} aria-hidden="true" />{:else}<ChevronRight size={16} aria-hidden="true" />{/if}
										<span class="truncate font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{r.name}</span>
										<span class="rounded-full border border-muted bg-white px-2 py-0.5 font-mono text-xs font-semibold text-slate">{r.members.length}</span>
										{#if r.is_system}<Lock size={12} class="text-muted" aria-label={t('roles.system')} />{/if}
									</button>
									<div class="flex shrink-0 items-center gap-1 pr-3">
										<a class="btn btn-outline btn-xs btn-square" href={roleHref(r.id)} aria-label={t('roles.edit_named', { name: r.name })}><Pencil size={13} /></a>
										<button class="btn btn-outline btn-xs gap-1" type="button" onclick={() => askAdd(r)} aria-label={t('roles.add_to_named', { name: r.name })}><Plus size={13} /> {t('roles.add_person_short')}</button>
									</div>
								</div>
							</th>
						</tr>
						{#if opened[r.id]}
							{#each r.members as m (m.id)}
								<tr class="hover:bg-surface">
									<td class="px-4 pl-10"><a class="link font-semibold" href="/members/{m.id}">{m.name}</a></td>
									<td class="truncate text-sm text-muted">{m.email}</td>
									<td class="text-right">
										<form method="POST" action="?/removeMember" use:enhance>
											<input type="hidden" name="role_id" value={r.id} />
											<input type="hidden" name="member_id" value={m.id} />
											<button class="btn btn-outline btn-error btn-xs gap-1" type="submit" aria-label={t('roles.remove_named', { name: m.name })}><UserMinus size={13} /> {t('roles.remove')}</button>
										</form>
									</td>
								</tr>
							{:else}
								<tr><td colspan="3" class="px-4 pl-10 text-sm text-muted">{t('roles.no_people')}</td></tr>
							{/each}
						{/if}
					</tbody>
				{/each}
			</table>
		</div>
	{/if}
</div>
</div>

<Modal bind:open={addOpen} title={target ? t('roles.add_to_title', { name: target.name }) : ''}>
	<form method="POST" action="?/addMember" class="grid gap-4" use:enhance={submit}>
		<input type="hidden" name="role_id" value={target?.id ?? ''} />
		<EntityPicker kind="member" name="member_id" label={t('roles.add_person')} bind:value={picked} exclude={target?.members.map((m) => m.id) ?? []} />
		<p class="text-xs text-muted">{t('roles.card.people_desc')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (addOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy || !picked}><Plus size={16} /> {t('roles.give')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('roles.delete.title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={() => { busy = true; return async ({ update }) => { await update({ reset: false }); busy = false; deleteOpen = false; }; }}>
		<input type="hidden" name="role_id" value={deleting?.id ?? ''} />
		<p>{t('roles.delete.body', { name: deleting?.name ?? '', n: deleting?.member_count ?? 0 })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
