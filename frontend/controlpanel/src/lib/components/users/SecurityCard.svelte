<script lang="ts">
	import { ShieldCheck, ShieldOff } from '@lucide/svelte';
	import { withZone } from '#lib/time.ts';
	import { AUTHENTICATOR_LABEL, type UserDetail } from '#lib/users.ts';

	let { user, isSelf, onreset }: { user: UserDetail; isSelf: boolean; onreset: () => void } = $props();
	const list = $derived(user.mfa.authenticators);
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title">
			<ShieldCheck size={16} aria-hidden="true" /> Security
			{#if user.mfa.enabled}<span class="badge badge-success badge-sm ml-1">2FA on</span>{:else}<span class="badge badge-sm ml-1 {user.account.platform_level ? 'badge-warning' : 'badge-ghost'}">2FA off</span>{/if}
			<button
				class="btn btn-outline btn-error btn-xs ml-auto"
				disabled={isSelf || !list.length}
				title={isSelf ? 'You cannot reset your own two-factor here; use your account security page.' : !list.length ? 'Nothing to reset: no two-factor authentication.' : undefined}
				onclick={onreset}><ShieldOff size={12} aria-hidden="true" /> Reset two-factor</button
			>
		</div>
		{#if list.length}
			<ul class="divide-y divide-rule">
				{#each list as m, i (i)}
					<li class="flex flex-wrap items-baseline justify-between gap-x-4 py-2 text-sm">
						<span class="font-semibold text-ink">
							{AUTHENTICATOR_LABEL[m.type]}
							{#if m.type === 'recovery_codes' && m.unused_codes != null}<span class="font-normal text-muted"> · {m.unused_codes} unused</span>{/if}
							{#if m.name}<span class="font-normal text-muted"> · {m.name}</span>{/if}
						</span>
						<span class="font-mono text-[11px] text-muted">added {withZone(m.created_at)} · last used {m.last_used_at ? withZone(m.last_used_at) : 'never'}</span>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="text-sm text-muted">No two-factor authentication set up.{user.account.platform_level ? ' Platform staff need it before they can use the control panel.' : ''}</p>
		{/if}
	</div>
</div>
