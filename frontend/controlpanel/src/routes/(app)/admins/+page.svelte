<script lang="ts">
	import { invalidate } from '$app/navigation';
	import type { PlatformAdmin } from '#lib/admins.ts';
	import Alert from '#lib/components/Alert.svelte';
	import AdminList from '#lib/components/admins/AdminList.svelte';
	import AuditLog from '#lib/components/admins/AuditLog.svelte';
	import GrantDialog from '#lib/components/admins/GrantDialog.svelte';
	import LevelDialog from '#lib/components/admins/LevelDialog.svelte';
	import RevokeDialog from '#lib/components/admins/RevokeDialog.svelte';

	let { data } = $props();

	let grantOpen = $state(false);
	let changing = $state<PlatformAdmin | null>(null);
	let revoking = $state<PlatformAdmin | null>(null);
	let notice = $state('');

	function done(message: string) {
		notice = message;
		invalidate('app:admins');
	}
</script>

<svelte:head><title>Admins · RosterChief</title></svelte:head>

<div>
	<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Admins</h1>
	<p class="mt-1.5 font-mono text-[11px] text-muted">Who can use this control panel, and what they did</p>
</div>

{#if notice}<Alert kind="success">{notice}</Alert>{/if}

<AdminList admins={data.admins} meId={data.me.id} error={data.adminsError} ongrant={() => (grantOpen = true)} onchange={(a) => (changing = a)} onrevoke={(a) => (revoking = a)} />

<AuditLog entries={data.entries} total={data.total} error={data.auditError} query={data.query} pageSize={data.pageSize} admins={data.admins} clubs={data.clubs} />

<GrantDialog bind:open={grantOpen} ondone={done} />
<LevelDialog bind:admin={changing} ondone={done} />
<RevokeDialog bind:admin={revoking} ondone={done} />
