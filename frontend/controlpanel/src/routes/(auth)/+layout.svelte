<script lang="ts">

    let {children} = $props();
</script>

<!-- Same entrance shell as the old app's _auth_base.html: one centred card on a dark page. -->
<div class="flex min-h-screen flex-col items-center gap-10 bg-ink px-4 py-14 font-sans text-slate">
    <div class="flex w-full max-w-md items-center justify-between gap-4 bg-ink py-1">
        <a class="flex min-w-0 shrink-0 items-center gap-2.5" href="/">
            <img class="h-8 w-8 shrink-0" src="/images/rosterchief-white.svg" alt="" width="32" height="32"/>
            <span class="font-display text-xl font-extrabold tracking-[.1em] text-white uppercase">RosterChief</span>
            <span class="font-mono text-[11px] text-on-dark-faint">controlpanel</span>
        </a>
    </div>

    <main class="flex w-full max-w-md flex-1 flex-col justify-center gap-4">
        {@render children()}
    </main>

    <p class="font-mono text-[11px] text-on-dark-faint">&copy; {new Date().getFullYear()} RosterChief</p>
</div>
