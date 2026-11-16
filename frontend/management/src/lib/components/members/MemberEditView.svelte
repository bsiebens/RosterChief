<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import MemberForm from '#lib/components/members/form/MemberForm.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';

	import type { ActionData, PageData } from '../../../routes/(app)/members/[id]/edit/$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	let confirmOpen = $state(false);
	let busy = $state(false);

	usePageHeader(() => ({ title: t('members.form.title_edit', { name: data.name }), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-outline btn-error gap-2" type="button" onclick={() => (confirmOpen = true)}><Trash2 size={16} /> {t('members.form.delete')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('members.form.title_edit', { name: data.name }))}</title></svelte:head>

{#if data.initial}
<MemberForm action="?/save" initial={form?.values ?? data.initial} fields={form?.fields} message={form?.message} code={form?.code} cancelHref="/members/{data.id}" profilePhotoUrl={data.profilePhotoUrl} playerPhotoUrl={data.playerPhotoUrl} />
{/if}

<Modal bind:open={confirmOpen} title={t('members.form.delete_title')}>
	<form
		method="POST"
		action="?/delete"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update();
				busy = false;
			};
		}}
	>
		{#if form?.deleteError}<div class="mb-3"><Alert kind="error" title={t('members.form.delete_failed')}>{form.deleteError}</Alert></div>{/if}
		<p class="text-sm">{t('members.form.delete_body', { name: data.name })}</p>
		{#if data.hasLogin}
			<label class="label mt-3 cursor-pointer items-start justify-start gap-3">
				<input type="checkbox" name="detach_login" class="checkbox mt-0.5" />
				<span>
					<span class="label-text block">{t('members.form.detach_login')}</span>
					<span class="label-text-alt block">{t('members.form.detach_login_help')}</span>
				</span>
			</label>
		{/if}
		<div class="modal-action mt-4 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (confirmOpen = false)}>{t('members.form.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('members.form.delete')}</button>
		</div>
	</form>
</Modal>
