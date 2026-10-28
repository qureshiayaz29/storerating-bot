// @ts-check
import { defineConfig, passthroughImageService } from 'astro/config';
import vercel from '@astrojs/vercel';

export default defineConfig({
  output: 'server',
  adapter: vercel({ maxDuration: 30 }),
  image: { service: passthroughImageService() }, // no image optimisation needed; keeps sharp out of the function
  security: { checkOrigin: false }, // Telegram posts JSON from its own servers
  vite: {
    // Bundle server dependencies into the function. The adapter's file copier
    // otherwise creates a self-referencing symlink while copying es5-ext
    // (a dependency of google-play-scraper) and the build fails with ELOOP.
    // Build only: the dev server needs CommonJS dependencies left external.
    ssr: { noExternal: process.argv.includes('build') ? true : undefined },
  },
});
