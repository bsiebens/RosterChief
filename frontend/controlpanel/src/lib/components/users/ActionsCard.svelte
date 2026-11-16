<script lang="ts">
	import { Ban, KeyRound, ShieldOff, ShieldCheck } from '@lucide/svelte';
	import type { UserDetail } from '#lib/users.ts';
	import type { Action } from './ActionDialogs.svelte';

	let { user, isSelf, onaction }: { user: UserDetail; isSelf: boolean; onaction: (a: Action) => void } = $props();

	const blocked = $derived(!!user.blocked);
	const SELF = 'You cannot do this to your own account here.';
	const pwTitle = $derived(isSelf ? SELF : blocked ? 'This account is blocked. Unblock it first.' : undefined);
</script>

<div class="card">
	<div class="card-body gap-0 divide-y divide-rule">
		<div class="card-title pb-3"><ShieldCheck size={16} aria-hidden="true" /> Actions</div>

		<div class="flex flex-col gap-2 py-4 text-sm">
			<div class="font-semibold text-ink">Send password reset email</div>
			<p class="text-muted">Sends the standard reset email to the user. You never see or set a password.</p>
			<div><button class="btn btn-outline btn-sm" disabled={isSelf || blocked} title={pwTitle} onclick={() => onaction('password')}><KeyRound size={13} aria-hidden="true" /> Send reset email</button></div>
		</div>

		<div class="flex flex-col gap-2 py-4 text-sm">
			<div class="font-semibold text-ink">Reset two-factor</div>
			<p class="text-muted">Removes all of their two-factor methods, for when they lost their device and recovery codes.</p>
			<div>
				<button
					class="btn btn-outline btn-error btn-sm"
					disabled={isSelf || !user.mfa.authenticators.length}
					title={isSelf ? SELF : !user.mfa.authenticators.length ? 'Nothing to reset: no two-factor authentication.' : undefined}
					onclick={() => onaction('mfa')}><ShieldOff size={13} aria-hidden="true" /> Reset two-factor</button
				>
			</div>
		</div>

		<div class="mt-1 flex flex-col gap-2 rounded-md border border-danger-border bg-danger-bg/40 p-4 text-sm">
			<div class="font-semibold text-club-dark">{blocked ? 'Account is blocked' : 'Danger area'}</div>
			{#if blocked}
				<p class="text-muted">They cannot sign in. Unblocking lets them sign in again with their current credentials.</p>
				<div><button class="btn btn-outline btn-success btn-sm" onclick={() => onaction('unblock')}>Unblock account</button></div>
			{:else}
				<p class="text-muted">Signs the user out everywhere immediately and stops them signing in until you unblock the account.</p>
				<div><button class="btn btn-error btn-sm" disabled={isSelf} title={isSelf ? SELF : undefined} onclick={() => onaction('block')}><Ban size={13} aria-hidden="true" /> Block account</button></div>
			{/if}
		</div>
	</div>
</div>
