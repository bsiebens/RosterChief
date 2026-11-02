<script lang="ts">
	import { ShieldCheck, ShieldOff, UserCog, UserMinus, UserPlus } from '@lucide/svelte';
	import { adminName, type PlatformAdmin } from '#lib/admins.ts';
	import Kpi from '../Kpi.svelte';
	import Alert from '../Alert.svelte';

	let {
		admins,
		meId,
		error,
		ongrant,
		onchange,
		onrevoke
	}: {
		admins: PlatformAdmin[];
		meId: string;
		error: string;
		ongrant: () => void;
		onchange: (a: PlatformAdmin) => void;
		onrevoke: (a: PlatformAdmin) => void;
	} = $props();

	const supers = $derived(admins.filter((a) => a.is_superuser).length);
	const noMfa = $derived(admins.filter((a) => !a.mfa_enabled).length);
</script>

<section class="flex flex-col gap-3" aria-labelledby="admins-h">
	<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
		<Kpi label="Platform admins" value={admins.length} />
		<Kpi label="Superusers" value={supers} sub="can manage admins and jobs" />
		<Kpi label="Staff" value={admins.length - supers} sub="read and club support" />
		<Kpi label="Without MFA" value={noMfa} tone={noMfa ? 'warn' : 'none'} sub={noMfa ? 'needs two-step sign-in' : 'everyone is protected'} />
	</div>

	{#if error}<Alert kind="error">{error}</Alert>{/if}

	<div class="card">
		<div class="flex items-center gap-3 border-b border-line px-4 py-3">
			<h2 id="admins-h" class="card-title m-0"><UserCog size={16} aria-hidden="true" /> Platform admins</h2>
			<button class="btn btn-primary btn-sm ml-auto" onclick={ongrant}><UserPlus size={14} aria-hidden="true" /> Grant access</button>
		</div>
		<div class="overflow-x-auto">
			<table class="table">
				<thead><tr><th>Name</th><th>Email</th><th>Level</th><th>MFA</th><th class="text-right">Actions</th></tr></thead>
				<tbody>
					{#each admins as admin (admin.id)}
						{@const me = admin.id === meId}
						<tr class={admin.mfa_enabled ? '' : 'bg-row-warn'}>
							<td class="font-semibold text-ink">
								{adminName(admin)}
								{#if me}<span class="badge badge-ghost badge-sm ml-1">you</span>{/if}
							</td>
							<td class="font-mono text-xs text-muted">{admin.email}</td>
							<td><span class="badge badge-sm {admin.is_superuser ? 'badge-info' : 'badge-ghost'}">{admin.is_superuser ? 'superuser' : 'staff'}</span></td>
							<td>
								{#if admin.mfa_enabled}
									<span class="badge badge-success badge-sm"><ShieldCheck size={11} aria-hidden="true" /> enabled</span>
								{:else}
									<span class="badge badge-warning badge-sm"><ShieldOff size={11} aria-hidden="true" /> NOT enabled</span>
								{/if}
							</td>
							<td class="text-right whitespace-nowrap">
								<button class="btn btn-outline btn-xs" disabled={me} title={me ? 'You cannot change your own level' : undefined} onclick={() => onchange(admin)} aria-label="Change level of {admin.email}">Change level</button>
								<button class="btn btn-outline btn-error btn-xs" disabled={me} title={me ? 'You cannot revoke your own access' : undefined} onclick={() => onrevoke(admin)} aria-label="Revoke access of {admin.email}"><UserMinus size={12} aria-hidden="true" /> Revoke</button>
							</td>
						</tr>
					{:else}
						<tr><td colspan="5" class="py-10 text-center text-sm text-muted">No platform admins found.</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if noMfa}
			<div class="border-t border-line px-4 py-2.5 text-xs text-warn-text">Platform staff should sign in with two-step verification. Admins marked “NOT enabled” can still be asked to set it up at their next sign-in.</div>
		{/if}
	</div>
</section>
