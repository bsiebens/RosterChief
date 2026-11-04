<script lang="ts">
	import Alert from '#lib/components/Alert.svelte';
	import { t } from '#lib/i18n.svelte.ts';

	let { granted }: { granted: { email: string; created: boolean; emailSent: boolean } } = $props();
</script>

<div class="mb-4 grid gap-3">
	<Alert kind="success" title={t('members.detail.grant.done', { email: granted.email })}>
		{#if granted.emailSent}
			{t('members.detail.grant.emailed', { email: granted.email })}
		{:else if !granted.created}
			{t('members.detail.grant.existing', { email: granted.email })}
		{/if}
	</Alert>
	{#if !granted.emailSent}
		<Alert kind="warning" title={t('members.detail.grant.not_sent_title')}>{t('members.detail.grant.not_sent', { email: granted.email })}</Alert>
	{/if}
</div>
