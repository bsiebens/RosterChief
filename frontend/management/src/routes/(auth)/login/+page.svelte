<script lang="ts">
    import {goto} from '$app/navigation';
    import {page} from '$app/state';
    import {KeyRound} from '@lucide/svelte';
    import {auth, getCredential, isPending, splitErrors, webauthnMessage, webauthnSupported} from '#lib/api.ts';
    import {safeNext} from '#lib/next.ts';
    import {t} from '#lib/i18n.svelte.ts';
    import {afterSignIn} from '#lib/session.ts';
    import AuthCard from '#lib/components/AuthCard.svelte';
    import Alert from '#lib/components/Alert.svelte';
    import Field from '#lib/components/Field.svelte';

    let {data} = $props();

    let email = $state('');
    let password = $state('');
    let busy = $state(false);
    let formErrors = $state<string[]>([]);
    let fieldErrors = $state<Record<string, string>>({});
    let notice = $state('');

    const next = $derived(safeNext(page.url.searchParams.get('next')));
    const passkeys = $derived(data.passkeyLogin && webauthnSupported());

    async function finish(r: Awaited<ReturnType<typeof auth>>) {
        if (r.ok) return afterSignIn(next, data.mode);
        if (isPending(r, 'mfa_authenticate')) {
            return goto(`/login/mfa${next !== '/' ? `?next=${encodeURIComponent(next)}` : ''}`, {refreshAll: true});
        }
        if (isPending(r, 'verify_email')) {
            notice = t('auth.login.verify_first');
            return;
        }
        const e = splitErrors(r);
        formErrors = e.form;
        fieldErrors = e.fields;
    }

    /**
     * Allauth answers 409 when this browser already has a signed-in session (e.g. a session without access to this club
     * left the user on /login). Sign that session out (the proxy clears the cookie) and try once more.
     */
    async function freshLogin(call: () => Promise<Awaited<ReturnType<typeof auth>>>) {
        const first = await call();
        if (first.status !== 409) return first;
        await auth('DELETE', '/auth/session');
        return call();
    }

    async function submit(event: SubmitEvent) {
        event.preventDefault();
        busy = true;
        formErrors = [];
        fieldErrors = {};
        notice = '';
        await finish(await freshLogin(() => auth('POST', '/auth/login', {email, password})));
        busy = false;
    }

    async function passkeyLogin() {
        busy = true;
        formErrors = [];
        try {
            const options = await auth('GET', '/auth/webauthn/login');
            if (!options.data?.request_options) return (formErrors = [t('auth.login.passkey_unavailable')]);
            const credential = await getCredential(options.data.request_options);
            if (!credential) return;
            await finish(await freshLogin(() => auth('POST', '/auth/webauthn/login', {credential})));
        } catch (e) {
            formErrors = [webauthnMessage(e)];
        } finally {
            busy = false;
        }
    }
</script>

<AuthCard title={t('auth.login.title')} lead={data.mode === 'club' && data.branding ? t('auth.login.lead_club', {club: data.branding.name}) : t('auth.login.lead_platform')}>
    {#if page.url.searchParams.get('reset') === 'done'}
        <Alert kind="success" title={t('auth.login.reset_done_title')}>{t('auth.login.reset_done_body')}</Alert>
    {/if}
    {#if notice}
        <Alert kind="warning">{notice}</Alert>
    {/if}
    {#each formErrors as message (message)}
        <Alert kind="error">{message}</Alert>
    {/each}

    <form class="flex flex-col gap-4" onsubmit={submit}>
        <Field label={t('auth.email')} type="email" name="email" autocomplete="username webauthn" required bind:value={email} error={fieldErrors.email}/>
        <Field label={t('auth.password')} type="password" name="password" autocomplete="current-password" required bind:value={password} error={fieldErrors.password}/>
        <button class="btn btn-primary w-full" type="submit" disabled={busy}>{t('auth.login.submit')}</button>

        {#if passkeys}
            <div class="flex items-center gap-3 text-xs text-muted" aria-hidden="true">
                <span class="h-px flex-1 bg-line"></span><span>{t('auth.or')}</span><span class="h-px flex-1 bg-line"></span>
            </div>
            <button class="btn btn-outline w-full" type="button" disabled={busy} onclick={passkeyLogin}>
                <KeyRound size={16} aria-hidden="true"/>
                {t('auth.login.passkey')}
            </button>
        {/if}
    </form>

    {#snippet footer()}<a href="/account/password/reset">{t('auth.login.forgot')}</a>{/snippet}
</AuthCard>
