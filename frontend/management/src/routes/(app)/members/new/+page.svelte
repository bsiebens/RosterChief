<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import MemberForm from '#lib/components/members/form/MemberForm.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';

	let { data, form } = $props();
	usePageHeader(() => ({ title: t('members.form.title_new') }));
</script>

<svelte:head><title>{pageTitle(t('members.form.title_new'))}</title></svelte:head>

<MemberForm initial={form?.values ?? data.initial} fields={form?.fields} message={form?.message} code={form?.code} cancelHref="/members" />
