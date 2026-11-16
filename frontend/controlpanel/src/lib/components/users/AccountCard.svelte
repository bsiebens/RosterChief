<script lang="ts">
	import { Check, UserRound } from '@lucide/svelte';
	import { withZone } from '#lib/time.ts';
	import type { UserDetail } from '#lib/users.ts';

	let { user }: { user: UserDetail } = $props();
	const a = $derived(user.account);
	const date = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title"><UserRound size={16} aria-hidden="true" /> Account</div>
		<dl class="grid grid-cols-[9rem_1fr] gap-x-4 gap-y-2.5 text-sm">
			<dt class="text-muted">Emails</dt>
			<dd class="flex flex-col gap-1">
				{#each a.emails as e (e.email)}
					<span class="flex flex-wrap items-center gap-1.5 font-mono text-xs">
						{e.email}
						{#if e.primary}<span class="badge badge-info badge-sm">primary</span>{/if}
						{#if e.verified}<span class="badge badge-success badge-sm"><Check size={10} aria-hidden="true" /> verified</span>{:else}<span class="badge badge-warning badge-sm">unverified</span>{/if}
					</span>
				{:else}
					<span class="font-mono text-xs">{a.email} <span class="badge badge-warning badge-sm">no login email</span></span>
				{/each}
			</dd>
			<dt class="text-muted">Member since</dt>
			<dd>{date(a.date_joined)}</dd>
			<dt class="text-muted">Last sign-in</dt>
			<dd class="font-mono text-xs">{a.last_login ? withZone(a.last_login) : 'never'}</dd>
			<dt class="text-muted">Password</dt>
			<dd>{#if a.has_usable_password}Set{:else}<span class="text-warn-text">No password set: signs in via reset only</span>{/if}</dd>
			<dt class="text-muted">Platform level</dt>
			<dd>{a.platform_level ?? 'none'}</dd>
		</dl>
	</div>
</div>
