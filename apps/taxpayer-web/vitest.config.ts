import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vitest/config';

// Tests use only the Vue plugin; the Frappe build/proxy plugins are not needed in jsdom.
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    // frappe-ui ships TypeScript/Vue source with extensionless imports; let Vite transform it.
    server: { deps: { inline: [/frappe-ui/] } },
  },
});
