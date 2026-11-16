<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { actionLabel, diffRows, formatDateTime } from '#lib/members/detail-helpers.ts';
	import type { HistoryEntry } from '#lib/members/detail-types.ts';

	let { items }: { items: HistoryEntry[] } = $props();
</script>

{#if items.length}
	<div class="overflow-x-auto">
		<table class="table">
			<thead>
				<tr>
					<th>{t('members.history.when')}</th>
					<th>{t('members.history.actor')}</th>
					<th>{t('members.history.action')}</th>
					<th>{t('members.history.target')}</th>
					<th>{t('members.history.changes')}</th>
				</tr>
			</thead>
			<tbody>
				{#each items as e (e.id)}
					<tr>
						<td class="font-mono text-xs whitespace-nowrap">{formatDateTime(e.created)}</td>
						<td class="text-sm">{e.actor_email ?? t('members.history.system')}</td>
						<td class="text-sm font-semibold" title={e.action}>{actionLabel(e.action)}</td>
						<td class="text-sm">{e.target_label}</td>
						<td class="text-xs">
							{#each diffRows(e.changes) as r (r.field)}
								<div>
									<span class="font-mono text-xs font-semibold">{r.field}</span>:
									{#if r.plain !== undefined}
										<span>{r.plain}</span>
									{:else}
										<span class="text-muted line-through">{r.from}</span> → <span class="font-semibold text-ink">{r.to}</span>
									{/if}
								</div>
							{/each}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{:else}
	<p class="p-4 text-sm text-muted">{t('members.history.empty')}</p>
{/if}
