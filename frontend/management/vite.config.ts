import adapter from '@sveltejs/adapter-node';
import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';

// The app version is the project's: one number in pyproject.toml.
const version = /^version\s*=\s*"([^"]+)"/m.exec(readFileSync(new URL('../../pyproject.toml', import.meta.url), 'utf8'))?.[1] ?? '';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			adapter: adapter()
		})
	],
	define: { __APP_VERSION__: JSON.stringify(version) },
	server: { port: 5174 }
});
