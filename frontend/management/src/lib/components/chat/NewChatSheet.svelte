<script lang="ts">
	import { Megaphone, MessageSquare, User, Users } from '@lucide/svelte';
	import { chatApi, errorText, type Conversation, type ConversationKind, type ConversationMode, type NewConversation, type Person, type TeamOption } from '#lib/chat.ts';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import PeoplePicker from './PeoplePicker.svelte';

	/**
	 * The "New chat" dialog: a team chat (a team plus who joins), a group chat (a title and people) or a direct chat (one person). People who
	 * may announce (`canAnnounce`) also pick the mode, discussion or announcements.
	 */
	let { open = $bindable(false), canAnnounce = false, kinds = ['team', 'group', 'direct'], oncreated }: {
		open: boolean;
		canAnnounce?: boolean;
		kinds?: ConversationKind[];
		oncreated: (c: Conversation) => void;
	} = $props();

	type Choice = ConversationKind | 'club';
	let kind = $state<Choice>('team'); // reset to the first kind on offer every time the dialog opens (below)
	let clubTeams = $state<string[]>([]);
	let mode = $state<ConversationMode>('discussion');
	let title = $state('');
	let teamId = $state('');
	let include = $state({ staff: true, players: true, parents: false });
	let people = $state<Person[]>([]);
	let teams = $state<TeamOption[] | null>(null);
	let groups = $state<TeamOption[]>([]);
	let error = $state('');
	let busy = $state(false);

	/** The kinds on offer; people who may announce also get "Whole club" (a group chat for everybody, kept in step with the club). */
	const choices = $derived<Choice[]>(canAnnounce && kinds.includes('group') ? [...kinds, 'club'] : kinds);

	// On every opening: start clean on the first kind on offer; the teams load the first time they are needed.
	$effect(() => {
		if (!open) return;
		kind = kinds.includes('team') ? 'team' : kinds[0];
		mode = 'discussion';
		title = '';
		teamId = '';
		include = { staff: true, players: true, parents: false };
		clubTeams = [];
		people = [];
		error = '';
	});
	$effect(() => {
		if (open && (kind === 'team' || kind === 'club') && teams === null) {
			chatApi.teams(kind === 'club').then((res) => { teams = res.ok ? (res.data?.results ?? []) : []; groups = res.ok ? (res.data?.groups ?? []) : []; });
		}
	});

	const icons = { team: Users, group: MessageSquare, direct: User, club: Megaphone };
	const problem = $derived(
		kind === 'club' ? (!title.trim() ? t('chat.new.need_title') : !include.staff && !include.players && !include.parents ? t('chat.new.need_include') : '')
		: kind === 'team' ? (!teamId ? t('chat.new.need_team') : !include.staff && !include.players && !include.parents ? t('chat.new.need_include') : '')
		: kind === 'group' ? (!title.trim() ? t('chat.new.need_title') : !people.length ? t('chat.new.need_people') : '')
		: !people.length ? t('chat.new.need_people') : ''
	);

	async function create(e: SubmitEvent) {
		e.preventDefault();
		if (problem || busy) {
			error = problem;
			return;
		}
		const body: NewConversation = { kind: kind === 'club' ? 'group' : kind };
		if (canAnnounce && kind !== 'direct') body.mode = mode;
		if (kind === 'club') {
			body.audience = 'club';
			body.title = title.trim();
			body.include = { ...include };
			if (clubTeams.length) body.team_ids = [...clubTeams];
		} else if (kind === 'team') {
			if (teamId.startsWith('g:')) body.group_id = teamId.slice(2);
			else body.team_id = teamId;
			body.include = { ...include };
			if (title.trim()) body.title = title.trim();
		} else {
			body.member_ids = people.map((p) => p.member_id);
			if (kind === 'group') body.title = title.trim();
		}
		busy = true;
		error = '';
		const res = await chatApi.create(body);
		busy = false;
		if (!res.ok || !res.data) {
			error = errorText(res, t('chat.err.generic'));
			return;
		}
		open = false;
		oncreated(res.data);
	}
</script>

<Modal bind:open title={t('chat.new')} closable>
	<form class="grid gap-4" onsubmit={create}>
		{#if choices.length > 1}
			<div class="grid grid-flow-col auto-cols-fr gap-1.5" role="radiogroup" aria-label={t('chat.new.kind')}>
				{#each choices as k (k)}
					{@const Icon = icons[k]}
					<button type="button" role="radio" aria-checked={kind === k} class="portal-person compact flex-col justify-center gap-1.5 py-3! {kind === k ? 'border-club! bg-club! text-club-content!' : ''}" onclick={() => { kind = k; error = ''; if (k === 'club') { mode = 'announcement'; include = { staff: true, players: true, parents: true }; } else mode = 'discussion'; }}>
						<Icon size={17} aria-hidden="true" />{t(`chat.new.${k}`)}
					</button>
				{/each}
			</div>
		{/if}

		{#if kind === 'team'}
			<label class="grid gap-1 text-sm font-semibold text-ink">{t('chat.new.team_pick')}
				<select class="select w-full font-normal" bind:value={teamId} disabled={teams === null}>
					<option value="">{teams === null ? t('chat.loading') : t('chat.new.team_none')}</option>
					{#if groups.length}<optgroup label={t('chat.new.pick_teams')}>{#each teams ?? [] as team (team.id)}<option value={team.id}>{team.name}</option>{/each}</optgroup>
					<optgroup label={t('chat.new.pick_groups')}>{#each groups as g (g.id)}<option value={`g:${g.id}`}>{g.name}</option>{/each}</optgroup>
					{:else}{#each teams ?? [] as team (team.id)}<option value={team.id}>{team.name}</option>{/each}{/if}
				</select>
			</label>
			<fieldset class="grid gap-2">
				<legend class="mb-1 text-sm font-semibold text-ink">{t('chat.new.include')}</legend>
				{#each [['staff', 'chat.new.staff'], ['players', 'chat.new.players'], ['parents', 'chat.new.parents']] as const as [key, label] (key)}
					<label class="flex items-center justify-between gap-3 text-sm text-ink">{t(label)}<input type="checkbox" class="toggle" bind:checked={include[key]} /></label>
				{/each}
			</fieldset>
			<label class="grid gap-1 text-sm font-semibold text-ink">{t('chat.new.title')}
				<input class="input w-full font-normal" bind:value={title} maxlength="120" />
				<span class="text-xs font-normal text-muted">{t('chat.new.title_team_help')}</span>
			</label>
		{:else if kind === 'club'}
			<label class="grid gap-1 text-sm font-semibold text-ink">{t('chat.new.title')}
				<input class="input w-full font-normal" bind:value={title} maxlength="120" />
			</label>
			<p class="text-xs text-muted">{t('chat.new.club_help')}</p>
			<fieldset class="grid gap-2">
				<legend class="mb-1 text-sm font-semibold text-ink">{t('chat.new.include')}</legend>
				{#each [['staff', 'chat.new.staff'], ['players', 'chat.new.players'], ['parents', 'chat.new.parents']] as const as [key, label] (key)}
					<label class="flex items-center justify-between gap-3 text-sm text-ink">{t(label)}<input type="checkbox" class="toggle" bind:checked={include[key]} /></label>
				{/each}
			</fieldset>
			<fieldset class="grid gap-1.5">
				<legend class="mb-1 text-sm font-semibold text-ink">{t('chat.new.club_teams')}</legend>
				<p class="text-xs text-muted">{t('chat.new.club_teams_help')}</p>
				{#if teams === null}
					<p class="text-sm text-muted" role="status">{t('chat.loading')}</p>
				{:else}
					<div class="grid max-h-40 gap-1 overflow-y-auto">
						{#each teams as team (team.id)}
							<label class="flex items-center gap-2 text-sm text-ink"><input type="checkbox" value={team.id} bind:group={clubTeams} />{team.name}</label>
						{/each}
					</div>
				{/if}
			</fieldset>
		{:else if kind === 'group'}
			<label class="grid gap-1 text-sm font-semibold text-ink">{t('chat.new.title')}
				<input class="input w-full font-normal" bind:value={title} maxlength="120" />
			</label>
			<div class="grid gap-1"><span class="text-sm font-semibold text-ink">{t('chat.new.people')}</span><PeoplePicker bind:selected={people} /></div>
		{:else}
			<div class="grid gap-1"><span class="text-sm font-semibold text-ink">{t('chat.new.person')}</span><PeoplePicker bind:selected={people} single /></div>
		{/if}

		{#if canAnnounce && kind !== 'direct'}
			<fieldset class="grid gap-1.5">
				<legend class="mb-1 text-sm font-semibold text-ink">{t('chat.new.mode')}</legend>
				{#each ['discussion', 'announcement'] as const as m (m)}
					<label class="flex items-start gap-2.5 rounded-lg border border-line p-2.5 text-sm {mode === m ? 'border-club bg-club/5' : ''}">
						<input type="radio" class="mt-1" name="chat-mode" value={m} bind:group={mode} />
						<span class="min-w-0"><b class="flex items-center gap-1.5 text-ink">{#if m === 'announcement'}<Megaphone size={14} aria-hidden="true" />{/if}{t(`chat.mode.${m}`)}</b><span class="block text-xs text-muted">{t(`chat.mode.${m}_help`)}</span></span>
					</label>
				{/each}
			</fieldset>
		{/if}

		{#if error}<p class="text-sm text-danger" role="alert">{error}</p>{/if}
		<div class="modal-action mt-0"><button class="btn btn-primary" type="submit" disabled={busy}>{t('chat.new.create')}</button></div>
	</form>
</Modal>
