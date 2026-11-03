import { fileURLToPath } from 'node:url';
import vue from '@vitejs/plugin-vue';
import autoprefixer from 'autoprefixer';
import frappeui from 'frappe-ui/vite';
import tailwindcss from 'tailwindcss';
import { defineConfig } from 'vitest/config';

const here = fileURLToPath(new URL('.', import.meta.url));
const fixtureApi = 'http://127.0.0.1:58080';

export default defineConfig({
  root: here,
  base: '/auditor/',
  plugins: [
    // No Frappe bench here: disable its /api proxy, bench build paths and Jinja boot data
    // so requests reach the explicit fixture server instead.
    frappeui({ frappeProxy: false, buildConfig: false, jinjaBootData: false }),
    vue(),
  ],
  css: {
    postcss: {
      plugins: [tailwindcss({ config: here + 'tailwind.config.ts' }), autoprefixer()],
    },
  },
  server: {
    host: '127.0.0.1',
    port: 58081,
    strictPort: true,
    proxy: { '/api': fixtureApi },
  },
  build: { outDir: 'dist', emptyOutDir: true },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
});
