<script lang="ts">
	import { Archive, ArchiveRestore, BellOff, UserMinus, UserPlus } from '@lucide/svelte';
	import { chatApi, errorText, groupParticipants, showRoleLabel, type Conversation, type ConversationMode, type Participant, type Person } from '#lib/chat.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import PeoplePicker from './PeoplePicker.svelte';
	import PersonAvatar from './PersonAvatar.svelte';

	/**
	 * Who is in the conversation, grouped by role, with the mute switch for everyone. Chat admins can also add and remove people, rename the
	 * chat, switch discussion/announcements and archive it. Every change is sent to the API and the answer goes up through `onchanged`.
	 */
	let { conversation, onchanged }: { conversation: Conversation; onchanged: (c: Conversation) => void } = $props();

	let participants = $state<Participant[] | null>(null);
	let error = $state('');
	let busy = $state(false);
	let adding = $state(false);
	let picked = $state<Person[]>([]);
	let name = $state('');
	let loadedFor = '';

	async function load() {
		const res = await chatApi.detail(conversation.id);
		if (res.ok && res.data) {
			participants = res.data.participants;
			error = '';
		} else if (participants === null) error = errorText(res, t('chat.err.load'));
	}

	// Load when the panel opens and again when the head count changes (somebody joined or was removed).
	$effect(() => {
		const key = `${conversation.id}:${conversation.participant_count}`;
		if (key === loadedFor) return;
		loadedFor = key;
		void load();
	});
	const title = $derived(conversation.title);
	$effect(() => {
		name = title;
	});

	const groups = $derived(groupParticipants(participants ?? []));

	async function patch(change: Parameters<typeof chatApi.patch>[1]) {
		busy = true;
		error = '';
		const res = await chatApi.patch(conversation.id, change);
		busy = false;
		if (res.ok && res.data) onchanged(res.data);
		else error = errorText(res, t('chat.err.generic'));
	}

	async function add() {
		if (!picked.length) return;
		busy = true;
		error = '';
		const res = await chatApi.addParticipants(conversation.id, picked.map((p) => p.member_id));
		busy = false;
		if (!res.ok) {
			error = errorText(res, t('chat.err.generic'));
			return;
		}
		picked = [];
		adding = false;
		await load();
		onchanged({ ...conversation, participant_count: participants?.length ?? conversation.participant_count });
	}

	async function remove(p: Participant) {
		if (!confirm(t('chat.people.remove_confirm', { name: p.person.name }))) return;
		busy = true;
		const res = await chatApi.removeParticipant(conversation.id, p.person.member_id);
		busy = false;
		if (!res.ok) {
			error = errorText(res, t('chat.err.generic'));
			return;
		}
		await load();
		onchanged({ ...conversation, participant_count: participants?.length ?? conversation.participant_count });
	}
</script>

<div class="grid gap-5 p-4">
	{#if error}<p class="text-sm text-danger" role="alert">{error}</p>{/if}

	<label class="flex items-center justify-between gap-3 rounded-lg border border-line bg-white p-3">
		<span class="min-w-0"><b class="flex items-center gap-1.5 text-sm text-ink"><BellOff size={14} aria-hidden="true" />{t('chat.people.mute')}</b><span class="block text-xs text-muted">{t('chat.people.mute_help')}</span></span>
		<input type="checkbox" class="toggle" checked={conversation.muted} disabled={busy} onchange={(e) => patch({ muted: e.currentTarget.checked })} />
	</label>

	<section aria-live="polite">
		<div class="mb-2 flex items-center justify-between gap-2">
			<h3 class="font-display text-sm font-extrabold tracking-wide text-ink uppercase">{t('chat.people.title')} <span class="font-mono text-xs text-muted">{conversation.participant_count}</span></h3>
			{#if conversation.is_admin && !conversation.archived}
				<button type="button" class="btn btn-outline btn-sm gap-1.5" aria-expanded={adding} onclick={() => (adding = !adding)}><UserPlus size={14} aria-hidden="true" />{t('chat.people.add')}</button>
			{/if}
		</div>
		{#if adding}
			<div class="mb-3 grid gap-2 rounded-lg border border-line bg-white p-3">
				<PeoplePicker bind:selected={picked} exclude={(participants ?? []).map((p) => p.person.member_id)} />
				<button type="button" class="btn btn-primary btn-sm justify-self-end" disabled={busy || !picked.length} onclick={add}>{t('chat.people.add_do')}</button>
			</div>
		{/if}
		{#if participants === null && !error}
			<p class="text-sm text-muted" role="status">{t('chat.people.loading')}</p>
		{/if}
		{#each groups as group (group.role)}
			<h4 class="mt-3 mb-1 text-[11px] font-bold tracking-wider text-muted uppercase">{t(`chat.people.role.${group.role}`)} · {group.people.length}</h4>
			<ul class="divide-y divide-rule rounded-lg border border-line bg-white">
				{#each group.people as p (p.person.member_id)}
					<li class="flex items-center gap-2.5 px-3 py-2">
						<PersonAvatar person={p.person} />
						<span class="min-w-0 flex-1 truncate text-sm text-ink">{p.person.name}{#if showRoleLabel(p.person)} <span class="text-xs text-muted">· {p.person.role_label}</span>{/if}{#if p.is_admin} <span class="badge badge-ghost badge-sm">{t('chat.people.chat_admin')}</span>{/if}</span>
						{#if conversation.is_admin && !conversation.archived && conversation.kind !== 'direct'}
							<button type="button" class="grid size-7 shrink-0 place-items-center rounded-full text-muted hover:bg-paper hover:text-danger" aria-label={t('chat.people.remove', { name: p.person.name })} disabled={busy} onclick={() => remove(p)}><UserMinus size={15} aria-hidden="true" /></button>
						{/if}
					</li>
				{/each}
			</ul>
		{/each}
	</section>

	{#if conversation.is_admin && conversation.kind !== 'direct'}
		<section class="grid gap-3 rounded-lg border border-line bg-white p-3">
			<h3 class="font-display text-sm font-extrabold tracking-wide text-ink uppercase">{t('chat.people.settings')}</h3>
			<form class="grid gap-1" onsubmit={(e) => { e.preventDefault(); if (name.trim() && name.trim() !== conversation.title) void patch({ title: name.trim() }); }}>
				<label class="text-sm font-semibold text-ink" for="chat-rename">{t('chat.people.rename')}</label>
				<div class="flex gap-2"><input id="chat-rename" class="input min-w-0 flex-1" bind:value={name} maxlength="120" /><button class="btn btn-outline" type="submit" disabled={busy || !name.trim() || name.trim() === conversation.title}>{t('chat.people.save')}</button></div>
			</form>
			<div class="grid gap-1.5" role="radiogroup" aria-label={t('chat.new.mode')}>
				{#each ['discussion', 'announcement'] as const as m (m)}
					<label class="flex items-start gap-2.5 rounded-lg border p-2.5 text-sm {conversation.mode === m ? 'border-club bg-club/5' : 'border-line'}">
						<input type="radio" class="mt-1" name="chat-mode-{conversation.id}" checked={conversation.mode === m} disabled={busy} onchange={() => patch({ mode: m as ConversationMode })} />
						<span class="min-w-0"><b class="block text-ink">{t(`chat.mode.${m}`)}</b><span class="block text-xs text-muted">{t(`chat.mode.${m}_help`)}</span></span>
					</label>
				{/each}
			</div>
			<button type="button" class="btn btn-outline btn-error gap-1.5 justify-self-start" disabled={busy} onclick={() => { if (conversation.archived || confirm(t('chat.people.archive_confirm'))) void patch({ archived: !conversation.archived }); }}>
				{#if conversation.archived}<ArchiveRestore size={15} aria-hidden="true" />{t('chat.people.unarchive')}{:else}<Archive size={15} aria-hidden="true" />{t('chat.people.archive')}{/if}
			</button>
		</section>
	{/if}
</div>
