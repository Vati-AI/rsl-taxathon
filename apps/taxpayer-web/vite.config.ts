import { fileURLToPath, URL } from 'node:url';
import vue from '@vitejs/plugin-vue';
import frappeui from 'frappe-ui/vite';
import { defineConfig } from 'vite';

// Built assets live in the Frappe app so the guarded www/taxpayer page serves them.
const frappeApp = fileURLToPath(new URL('../../backend/vati_tax/vati_tax', import.meta.url));
// Local Frappe developer site from docs/FRAPPE_DEV.md (loopback only).
const frappeDev = 'http://127.0.0.1:58000';

export default defineConfig({
  base: '/taxpayer/',
  plugins: [
    frappeui({
      frontendRoute: '/taxpayer',
      // The repo layout is not apps/<app>/frontend, so configure paths and proxy explicitly.
      frappeProxy: false,
      buildConfig: {
        outDir: `${frappeApp}/public/taxpayer`,
        indexHtmlPath: `${frappeApp}/www/taxpayer.html`,
        baseUrl: '/assets/vati_tax/taxpayer/',
      },
    }),
    vue(),
  ],
  server: {
    host: '127.0.0.1',
    port: 58081,
    strictPort: true,
    proxy: {
      '^/(api|login|logout|assets|files|private|desk)': { target: frappeDev, changeOrigin: false },
    },
  },
});
