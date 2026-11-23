<script lang="ts">
	import { Megaphone, MessageSquare, Send } from '@lucide/svelte';
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { dateTimeLabel } from '#lib/tournaments-public.ts';
	import type { TeamMessage, Thread } from './+page.server.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const doc = $derived(data.threads);
	const base = $derived(`/tournaments/${tn.id}`);
	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.messages') }));

	// the open conversation: a team (registration id) or 'all' for the broadcasts
	let current = $state<string>('all');
	let messages = $state<TeamMessage[]>([]);
	let text = $state('');
	let busy = $state(false);
	let error = $state('');
	const thread = $derived(doc?.threads.find((x) => x.registration_id === current) ?? null);

	async function open(id: string) {
		current = id;
		error = '';
		if (id === 'all') {
			messages = doc?.broadcasts ?? [];
			return;
		}
		const res = await api<{ messages: TeamMessage[] }>('GET', `${base}/messages/${id}`);
		messages = res.data?.messages ?? [];
		await invalidateAll(); // the unread counts
	}
	$effect(() => {
		if (current === 'all') messages = doc?.broadcasts ?? [];
	});
	// a notification opens the page on the conversation it is about: ?team=<application id>
	onMount(() => {
		const wanted = page.url.searchParams.get('team');
		if (wanted && doc?.threads.some((x) => x.registration_id === wanted)) open(wanted);
	});

	async function send(e: SubmitEvent) {
		e.preventDefault();
		const body = text.trim();
		if (!body || busy) return;
		busy = true;
		error = '';
		const res = await api('POST', current === 'all' ? `${base}/message-broadcast` : `${base}/messages/${current}`, { body: { body } });
		busy = false;
		if (!res.ok) {
			error = res.message || t('common.error.generic');
			return;
		}
		text = '';
		if (current === 'all') await invalidateAll();
		else await open(current);
	}
	const preview = (th: Thread) => (th.last_message ? `${th.last_message.sender === 'organiser' ? t('tn.msg.you') : th.contact}: ${th.last_message.body}` : t('tn.msg.none'));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.messages'))}</title></svelte:head>

{#if !doc}
	<Alert kind="error">{t('common.error.generic')}</Alert>
{:else}
	<div class="grid min-h-[32rem] items-stretch gap-4 lg:grid-cols-[20rem_1fr]">
		<aside class="card overflow-hidden">
			<button type="button" class="flex w-full items-center gap-3 border-b border-line px-4 py-3 text-left hover:bg-paper {current === 'all' ? 'bg-paper' : ''}" onclick={() => open('all')}>
				<span class="flex size-9 shrink-0 items-center justify-center rounded-full bg-club/10 text-club"><Megaphone size={17} aria-hidden="true" /></span>
				<span class="min-w-0"><b class="block text-ink">{t('tn.msg.all_teams')}</b><span class="block truncate text-xs text-muted">{t('tn.msg.all_teams_help')}</span></span>
			</button>
			<ul class="max-h-[34rem] divide-y divide-rule overflow-y-auto">
				{#each doc.threads as th (th.registration_id)}
					<li>
						<button type="button" class="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-paper {th.unread ? 'bg-amber-50 shadow-[inset_4px_0_0_var(--color-warning)]' : ''} {current === th.registration_id ? 'bg-paper' : ''}" onclick={() => open(th.registration_id)}>
							<span class="flex size-9 shrink-0 items-center justify-center rounded-full {th.unread ? 'bg-warning text-white' : 'bg-ink/5 text-ink'}"><MessageSquare size={16} aria-hidden="true" /></span>
							<span class="min-w-0 flex-1"><b class="block truncate text-ink {th.unread ? 'font-extrabold' : ''}">{th.team}</b><span class="block truncate text-xs {th.unread ? 'font-semibold text-ink' : 'text-muted'}">{preview(th)}</span></span>
							{#if th.unread}<span class="badge badge-warning badge-sm font-bold">{th.unread}</span>{/if}
						</button>
					</li>
				{/each}
			</ul>
		</aside>

		<section class="card flex min-w-0 flex-col overflow-hidden">
			<header class="border-b border-line px-5 py-3">
				<h2 class="font-display text-base font-extrabold text-ink uppercase">{thread ? thread.team : t('tn.msg.all_teams')}</h2>
				<p class="text-xs text-muted">{thread ? t('tn.msg.thread_help', { contact: thread.contact }) : t('tn.msg.broadcast_help')}</p>
			</header>
			<div class="flex flex-1 flex-col gap-3 overflow-y-auto bg-paper/50 p-5">
				{#each messages as m (m.id)}
					<div class="flex {m.sender === 'organiser' ? 'justify-end' : 'justify-start'}">
						<div class="max-w-[80%] rounded-2xl px-4 py-2.5 text-sm shadow-sm {m.sender === 'organiser' ? 'rounded-br-sm bg-ink text-white' : 'rounded-bl-sm border border-line bg-white text-ink'}">
							<p class="font-mono text-[10px] opacity-70">{m.author} · {dateTimeLabel(m.created)}{#if m.broadcast && current !== 'all'} · {t('tn.msg.broadcast')}{/if}</p>
							<p class="mt-0.5 whitespace-pre-wrap">{m.body}</p>
						</div>
					</div>
				{:else}
					<p class="m-auto text-sm text-muted">{t('tn.msg.empty')}</p>
				{/each}
			</div>
			<form class="grid gap-2 border-t border-line p-4" onsubmit={send}>
				{#if error}<p class="text-sm text-danger" role="alert">{error}</p>{/if}
				<textarea class="textarea min-h-20 w-full" bind:value={text} maxlength="4000" placeholder={t('tn.msg.placeholder')} aria-label={t('tn.msg.placeholder')}></textarea>
				<div class="flex items-center justify-between gap-3">
					<span class="text-xs text-muted">{t('tn.msg.email_note')}</span>
					<button class="btn btn-primary gap-2" type="submit" disabled={busy || !text.trim()}><Send size={15} aria-hidden="true" /> {current === 'all' ? t('tn.msg.send_all') : t('tn.msg.send')}</button>
				</div>
			</form>
		</section>
	</div>
{/if}
