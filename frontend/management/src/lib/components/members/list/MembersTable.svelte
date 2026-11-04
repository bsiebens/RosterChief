<script lang="ts">
	import { ArrowDown, ArrowUp } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { hasActiveFilters, pageHref, sortState, toggleOrdering, type ListFilters, type MemberRow, type OrderField } from '#lib/members/list.ts';
	import MemberRowItem from './MemberRowItem.svelte';

	let { rows, filters, canManage, showFees, onnavigate }: { rows: MemberRow[]; filters: ListFilters; canManage: boolean; showFees: boolean; onnavigate: (href: string) => void } = $props();

	// Status is shown to everyone who can see the list; the dues column is admin only. People without a membership
	// in the season (guardians, staff) get a dash.
	const showStatus = true;
	const feeColumn = $derived(showFees);
	const cols = $derived(5 + 1 + (feeColumn ? 1 : 0) + (canManage ? 1 : 0) + 1);
</script>

{#snippet sortable(field: OrderField, label: string)}
	{@const state = sortState(filters.ordering, field)}
	{@const href = pageHref(filters, { ordering: toggleOrdering(filters.ordering, field) })}
	<th aria-sort={state}>
		<a
			{href}
			class="inline-flex items-center gap-1 hover:text-ink"
			title={t('members.list.sort_by', { column: label })}
			onclick={(e) => {
				e.preventDefault();
				onnavigate(href);
			}}>{label}{#if state === 'ascending'}<ArrowUp size={12} />{:else if state === 'descending'}<ArrowDown size={12} />{/if}</a>
	</th>
{/snippet}

<div class="card overflow-x-auto">
	<table class="table">
		<thead>
			<tr>
				{@render sortable('last_name', t('members.list.col.member'))}
				{#if canManage}<th class="text-center">{t('members.list.col.login')}</th>{/if}
				<th>{t('members.list.col.type')}</th>
				{@render sortable('email', t('members.list.col.email'))}
				{@render sortable('license_number', t('members.list.col.licence'))}
				<th>{t('members.list.col.family')}</th>
				{#if showStatus}<th>{t('members.list.col.status')}</th>{/if}
				{#if feeColumn}<th>{t('members.list.col.fee')}</th>{/if}
				<th class="text-right"><span class="sr-only">{t('members.list.col.actions')}</span></th>
			</tr>
		</thead>
		<tbody>
			{#each rows as member (member.id)}
				<MemberRowItem {member} {canManage} showFees={feeColumn} {showStatus} />
			{:else}
				<tr><td colspan={cols} class="py-6 text-center text-muted">{t(hasActiveFilters(filters) ? 'members.list.empty_filtered' : 'members.list.empty')}</td></tr>
			{/each}
		</tbody>
	</table>
</div>
