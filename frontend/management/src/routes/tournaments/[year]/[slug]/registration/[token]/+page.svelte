<script lang="ts">
	import { KeyRound } from '@lucide/svelte';
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import TeamPortal from '#lib/components/tournaments/public/TeamPortal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { storeAccess } from '#lib/team-access.ts';

	let { data } = $props();
	let password = $state('');
	let busy = $state(false);
	let error = $state('');

	async function unlock(e: SubmitEvent) {
		e.preventDefault();
		if (busy || !password.trim()) return;
		busy = true;
		error = '';
		const res = await api<{ access: string }>('POST', `/public/tournaments/${encodeURIComponent(page.params.slug ?? '')}/registrations/${encodeURIComponent(data.token)}/access`, { body: { password } });
		busy = false;
		if (res.ok && res.data) {
			storeAccess(data.token, res.data.access);
			await invalidateAll();
			return;
		}
		error = res.status === 429 ? t('reg.err.rate_limited') : res.fields.password || t('ts.team.pw_wrong');
	}
</script>

<svelte:head>
	<title>{t('ts.team.pw_title')}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

{#if data.locked}
	<section class="mx-auto w-full max-w-md overflow-hidden rounded-2xl border border-line bg-white shadow-sm">
		<div class="flex items-center gap-3 border-b border-line bg-subhead px-5 py-4">
			<span class="flex size-10 items-center justify-center rounded-full bg-club/10 text-club"><KeyRound size={20} aria-hidden="true" /></span>
			<div>
				<h2 class="font-display text-lg font-extrabold text-ink uppercase">{t('ts.team.pw_title')}</h2>
				<p class="text-sm text-muted">{t('ts.team.pw_help')}</p>
			</div>
		</div>
		<form class="grid gap-3 p-5" onsubmit={unlock}>
			<label class="grid gap-1.5 text-sm font-semibold text-ink" for="team-pw">{t('ts.team.pw_label')}
				<input id="team-pw" class="input input-bordered w-full font-mono tracking-wider" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" bind:value={password} placeholder="xxxxx-xxxxx" required />
			</label>
			{#if error}<p class="text-sm text-danger" role="alert">{error}</p>{/if}
			<button class="btn btn-primary" type="submit" disabled={busy || !password.trim()}>{t('ts.team.pw_open')}</button>
			<p class="text-xs text-muted">{t('ts.team.pw_lost')}</p>
		</form>
	</section>
{:else}
	<TeamPortal {data} />
{/if}
