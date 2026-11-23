<script lang="ts">
	import { Send } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { accessHeaders } from '#lib/team-access.ts';
	import { dateTimeLabel } from '#lib/tournaments-public.ts';

	/** A team's conversation with the organisers, on its private page. Opening it marks the organisers' messages as read. */
	let { base, token, onread }: { base: string; token: string; onread?: () => void } = $props();
	type Msg = { id: string; sender: 'organiser' | 'team'; author: string; body: string; created: string; broadcast: boolean };
	let messages = $state<Msg[]>([]);
	let text = $state('');
	let busy = $state(false);
	let error = $state('');

	async function load() {
		const res = await api<{ messages: Msg[] }>('GET', `${base}/messages`, { headers: accessHeaders(token) });
		messages = res.data?.messages ?? [];
		onread?.();
	}
	onMount(load);

	async function send(e: SubmitEvent) {
		e.preventDefault();
		const body = text.trim();
		if (!body || busy) return;
		busy = true;
		error = '';
		const res = await api('POST', `${base}/messages`, { body: { body }, headers: accessHeaders(token) });
		busy = false;
		if (!res.ok) {
			error = t('ts.team.msg_failed');
			return;
		}
		text = '';
		await load();
	}
</script>

<section class="overflow-hidden rounded-2xl border border-line bg-white shadow-sm">
	<p class="border-b border-line bg-subhead px-5 py-3 text-sm text-muted">{t('ts.team.msg_intro')}</p>
	<div class="flex max-h-[32rem] min-h-56 flex-col gap-3 overflow-y-auto bg-paper/50 p-5">
		{#each messages as m (m.id)}
			<div class="flex {m.sender === 'team' ? 'justify-end' : 'justify-start'}">
				<div class="max-w-[85%] rounded-2xl px-4 py-2.5 text-sm shadow-sm {m.sender === 'team' ? 'rounded-br-sm bg-ink text-white' : 'rounded-bl-sm border border-line bg-white text-ink'}">
					<p class="font-mono text-[10px] opacity-70">{m.sender === 'team' ? t('ts.team.msg_you') : t('ts.team.msg_organisers')} · {dateTimeLabel(m.created)}{#if m.broadcast} · {t('ts.team.msg_announcement')}{/if}</p>
					<p class="mt-0.5 whitespace-pre-wrap">{m.body}</p>
				</div>
			</div>
		{:else}
			<p class="m-auto text-sm text-muted">{t('ts.team.msg_empty')}</p>
		{/each}
	</div>
	<form class="grid gap-2 border-t border-line p-4" onsubmit={send}>
		{#if error}<p class="text-sm text-danger" role="alert">{error}</p>{/if}
		<textarea class="textarea min-h-20 w-full" bind:value={text} maxlength="4000" placeholder={t('ts.team.msg_placeholder')} aria-label={t('ts.team.msg_placeholder')}></textarea>
		<div class="flex justify-end"><button class="btn btn-primary gap-2" type="submit" disabled={busy || !text.trim()}><Send size={15} aria-hidden="true" /> {t('ts.team.msg_send')}</button></div>
	</form>
</section>
