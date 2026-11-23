<script lang="ts">
	import Avatar from '#lib/components/Avatar.svelte';
	import { splitName, type Person } from '#lib/chat.ts';

	let { person, size = 'size-8', text = 'text-xs' }: { person: Pick<Person, 'name' | 'photo_url'>; size?: string; text?: string } = $props();
	const name = $derived(splitName(person.name));
</script>

<Avatar first_name={name.first} last_name={name.last} photoUrl={person.photo_url} {size} {text} />
