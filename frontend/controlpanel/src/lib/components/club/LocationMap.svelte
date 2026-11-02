<script lang="ts">
	import 'maplibre-gl/dist/maplibre-gl.css';
	import { onDestroy, onMount } from 'svelte';
	import type { HomeLocation } from '#lib/clubs.ts';

	let { location }: { location: HomeLocation } = $props();

	// OpenFreeMap: free vector tiles, no API key. Coordinates come from OSM Nominatim (best effort, cached per address).
	const STYLE = 'https://tiles.openfreemap.org/styles/bright';

	let el: HTMLDivElement;
	let ml: any;
	let map: any;
	let marker: any;
	let state = $state<'loading' | 'ready' | 'notfound'>('loading');

	const query = $derived([location.latitude, location.longitude].join(',') + '|' + [location.address, `${location.zip_code} ${location.city}`.trim(), location.country_name].filter(Boolean).join(', '));

	async function geocode(q: string): Promise<[number, number] | null> {
		const key = `rc_geo:${q}`;
		try {
			const hit = sessionStorage.getItem(key);
			if (hit) return JSON.parse(hit);
		} catch {}
		try {
			const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`, { headers: { Accept: 'application/json' } });
			const rows = res.ok ? await res.json() : [];
			if (!rows.length) return null;
			const point: [number, number] = [Number(rows[0].lon), Number(rows[0].lat)];
			try {
				sessionStorage.setItem(key, JSON.stringify(point));
			} catch {}
			return point;
		} catch {
			return null;
		}
	}

	async function show(q: string) {
		state = 'loading';
		const exact: [number, number] | null = location.latitude != null && location.longitude != null ? [location.longitude, location.latitude] : null;
		const point = exact ?? (await geocode(q.split('|')[1]));
		if (!point || q !== query || !map) return void (q === query && (state = 'notfound'));
		marker?.remove();
		marker = new ml.Marker({ color: '#c8102e' }).setLngLat(point).addTo(map);
		map.jumpTo({ center: point, zoom: 15 });
		state = 'ready';
	}

	onMount(async () => {
		ml = await import('maplibre-gl');
		ml.setWorkerUrl('/vendor/maplibre-gl-worker.mjs');
		map = new ml.Map({ container: el, style: STYLE, center: [4.5, 51], zoom: 3, scrollZoom: false, attributionControl: { compact: true } });
		map.addControl(new ml.NavigationControl({ showCompass: false }), 'top-right');
		map.on('click', () => map.scrollZoom.enable());
		map.on('mouseout', () => map.scrollZoom.disable());
		show(query);
	});

	// Re-centre when the address is edited.
	$effect(() => {
		const q = query;
		if (map) show(q);
	});

	onDestroy(() => map?.remove());
</script>

<div class="relative h-full min-h-48 w-full overflow-hidden rounded border border-line bg-subhead" role="img" aria-label="Map of {location.name}">
	<div class="absolute inset-0"><div bind:this={el} style="width:100%;height:100%"></div></div>
	{#if state !== 'ready'}
		<div class="pointer-events-none absolute inset-0 flex items-center justify-center bg-subhead/80 font-mono text-[11px] text-muted">
			{state === 'loading' ? 'Loading map…' : 'Address not found on the map'}
		</div>
	{/if}
</div>
