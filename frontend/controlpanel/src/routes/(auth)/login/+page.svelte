<script lang="ts">
    import {goto} from '$app/navigation';
    import {page} from '$app/state';
    import {KeyRound} from '@lucide/svelte';
    import {auth, getCredential, isPending, splitErrors, webauthnMessage, webauthnSupported} from '#lib/api.ts';
    import {safeNext} from '#lib/next.ts';
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
        if (r.ok) return goto(next, {refreshAll: true});
        if (isPending(r, 'mfa_authenticate')) {
            return goto(`/login/mfa${next !== '/' ? `?next=${encodeURIComponent(next)}` : ''}`, {refreshAll: true});
        }
        if (isPending(r, 'verify_email')) {
            notice = 'Confirm your email address first. We sent you a link when your account was created.';
            return;
        }
        const e = splitErrors(r);
        formErrors = e.form;
        fieldErrors = e.fields;
    }

    async function submit(event: SubmitEvent) {
        event.preventDefault();
        busy = true;
        formErrors = [];
        fieldErrors = {};
        notice = '';
        await finish(await auth('POST', '/auth/login', {email, password}));
        busy = false;
    }

    async function passkeyLogin() {
        busy = true;
        formErrors = [];
        try {
            const options = await auth('GET', '/auth/webauthn/login');
            if (!options.data?.request_options) return (formErrors = ['Passkey sign-in is not available right now.']);
            const credential = await getCredential(options.data.request_options);
            if (!credential) return;
            await finish(await auth('POST', '/auth/webauthn/login', {credential}));
        } catch (e) {
            formErrors = [webauthnMessage(e)];
        } finally {
            busy = false;
        }
    }
</script>

<AuthCard title="Sign in" lead="Platform admins & staff only.">
    {#if page.url.searchParams.get('reset') === 'done'}
        <Alert kind="success" title="Password updated">Sign in with your new password.</Alert>
    {/if}
    {#if notice}
        <Alert kind="warning">{notice}</Alert>
    {/if}
    {#each formErrors as message (message)}
        <Alert kind="error">{message}</Alert>
    {/each}

    <form class="flex flex-col gap-4" onsubmit={submit}>
        <Field label="Email" type="email" name="email" autocomplete="username webauthn" required bind:value={email} error={fieldErrors.email}/>
        <Field label="Password" type="password" name="password" autocomplete="current-password" required bind:value={password} error={fieldErrors.password}/>
        <button class="btn btn-primary w-full" type="submit" disabled={busy}>Sign in</button>

        {#if passkeys}
            <div class="flex items-center gap-3 text-xs text-muted" aria-hidden="true">
                <span class="h-px flex-1 bg-line"></span><span>or</span><span class="h-px flex-1 bg-line"></span>
            </div>
            <button class="btn btn-outline w-full" type="button" disabled={busy} onclick={passkeyLogin}>
                <KeyRound size={16} aria-hidden="true"/>
                Sign in with a passkey
            </button>
        {/if}
    </form>

    {#snippet footer()}<a href="/account/password/reset">Forgot your password?</a>{/snippet}
</AuthCard>
