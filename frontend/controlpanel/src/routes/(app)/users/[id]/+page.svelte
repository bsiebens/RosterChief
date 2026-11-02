<script lang="ts">
	import { invalidate } from '$app/navigation';
	import { ArrowLeft } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import AccountCard from '#lib/components/users/AccountCard.svelte';
	import ActionDialogs, { type Action } from '#lib/components/users/ActionDialogs.svelte';
	import ActionsCard from '#lib/components/users/ActionsCard.svelte';
	import ActivityCard from '#lib/components/users/ActivityCard.svelte';
	import SecurityCard from '#lib/components/users/SecurityCard.svelte';
	import SessionsCard from '#lib/components/users/SessionsCard.svelte';
	import UserClubsCard from '#lib/components/users/UserClubsCard.svelte';
	import { withZone } from '#lib/time.ts';
	import { userName } from '#lib/users.ts';

	let { data } = $props();

	const user = $derived(data.user);
	const acc = $derived(user.account);
	const isSelf = $derived(String(acc.id) === data.meId);
	const name = $derived(userName(acc));

	let action = $state<Action | null>(null);
	let notice = $state('');

	function done(message: string) {
		notice = message;
		invalidate('app:user');
	}
</script>

<svelte:head><title>{acc.email} · Users · RosterChief</title></svelte:head>

<div>
	<a class="mb-3 inline-flex items-center gap-1.5 font-mono text-[11px] text-muted hover:text-ink" href="/users"><ArrowLeft size={12} aria-hidden="true" /> All users</a>
	<div class="flex flex-wrap items-center gap-2">
		<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] break-all text-ink uppercase">{acc.email}</h1>
		<span class="badge {user.blocked ? 'badge-error' : 'badge-success'}">{user.blocked ? 'blocked' : 'active'}</span>
		{#if acc.platform_level}<span class="badge {acc.platform_level === 'superuser' ? 'badge-neutral' : 'badge-info'}">{acc.platform_level}</span>{/if}
		{#if isSelf}<span class="badge badge-ghost">you</span>{/if}
	</div>
	{#if name}<p class="mt-1.5 text-sm text-muted">{name}</p>{/if}
</div>

{#if user.blocked}
	<Alert kind="error" title="Blocked">
		Blocked {user.blocked.blocked_at ? withZone(user.blocked.blocked_at) : 'at an unknown time'}{user.blocked.blocked_by_email ? ` by ${user.blocked.blocked_by_email}` : ''}{user.blocked.reason ? `: ${user.blocked.reason}` : '.'}
	</Alert>
{/if}
{#if notice}<Alert kind="success">{notice}</Alert>{/if}

<div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
	<div class="flex min-w-0 flex-col gap-4">
		<AccountCard {user} />
		<SecurityCard {user} {isSelf} onreset={() => (action = 'mfa')} />
		<UserClubsCard clubs={user.clubs} />
	</div>
	<div class="flex min-w-0 flex-col gap-4">
		<ActionsCard {user} {isSelf} onaction={(a) => (action = a)} />
		<SessionsCard sessions={user.sessions} />
	</div>
</div>

<ActivityCard entries={user.recent_activity} userId={acc.id} />

<ActionDialogs {user} bind:action ondone={done} />
