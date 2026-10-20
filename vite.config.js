import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// Builds a static site into /build for GitHub Pages.
			adapter: adapter(),

			// GitHub Pages serves project sites from /<repo-name>; the deploy workflow sets BASE_PATH.
			paths: {
				base: process.env.BASE_PATH ?? ''
			}
		})
	]
});
