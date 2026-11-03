import { fileURLToPath } from 'node:url';
import vue from '@vitejs/plugin-vue';
import autoprefixer from 'autoprefixer';
import frappeui from 'frappe-ui/vite';
import tailwindcss from 'tailwindcss';
import { defineConfig } from 'vitest/config';

// Same layout as apps/auditor-web (ADR-004), but this app is served by the
// vati_tax Frappe app: build into its public/ folder and write the guarded
// www/taxpayer page, whose Jinja boot data supplies the CSRF token.
const here = fileURLToPath(new URL('.', import.meta.url));
const frappeApp = fileURLToPath(new URL('../../backend/vati_tax/vati_tax/', import.meta.url));
// Local Frappe developer site from docs/FRAPPE_DEV.md (loopback only).
const frappeDev = 'http://127.0.0.1:58000';

export default defineConfig({
  root: here,
  base: '/taxpayer/',
  plugins: [
    frappeui({
      frontendRoute: '/taxpayer',
      // The repo is not laid out as apps/<app>/frontend, so set proxy and paths explicitly.
      frappeProxy: false,
      buildConfig: {
        outDir: frappeApp + 'public/taxpayer',
        indexHtmlPath: frappeApp + 'www/taxpayer.html',
        baseUrl: '/assets/vati_tax/taxpayer/',
      },
    }),
    vue(),
  ],
  css: {
    postcss: {
      plugins: [tailwindcss({ config: here + 'tailwind.config.ts' }), autoprefixer()],
    },
  },
  server: {
    host: '127.0.0.1',
    port: 58082,
    strictPort: true,
    proxy: {
      '^/(api|login|logout|assets|files|private|desk)': { target: frappeDev, changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    // frappe-ui ships source with extensionless relative imports; let Vite transform it.
    server: { deps: { inline: ['frappe-ui'] } },
  },
});
