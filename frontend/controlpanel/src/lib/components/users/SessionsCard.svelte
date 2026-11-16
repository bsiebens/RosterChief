<script lang="ts">
	import { MonitorSmartphone } from '@lucide/svelte';
	import { withZone } from '#lib/time.ts';
	import type { UserDetail } from '#lib/users.ts';

	let { sessions }: { sessions: UserDetail['sessions'] } = $props();
</script>

<div class="card">
	<div class="card-body gap-2 text-sm">
		<div class="card-title"><MonitorSmartphone size={16} aria-hidden="true" /> Sessions</div>
		<div>Active sessions: <strong class="text-ink">{sessions.truncated ? `at least ${sessions.active_count}` : sessions.active_count}</strong></div>
		<div>Last active ≈ <span class="font-mono text-xs">{sessions.last_seen ? withZone(sessions.last_seen) : 'unknown'}</span></div>
		<p class="text-xs text-muted">The last-active time is an estimate.</p>
	</div>
</div>
