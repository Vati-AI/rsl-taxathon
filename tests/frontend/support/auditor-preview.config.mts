import { mergeConfig } from 'vite';
import auditor from '../../../apps/auditor-web/vite.config.mts';
import { AUDITOR_PORT, FIXTURE_API_PORT } from './env';

// Serve the built auditor app (`npm run build:auditor`) with Vite's preview
// server and send /api to the synthetic fixture server (scripts/dev.py) on the
// harness port. The fixture server still answers every API request with its
// own principal and tenant rules; Vite only serves the built static files.
export default mergeConfig(auditor, {
  preview: {
    host: '127.0.0.1',
    port: AUDITOR_PORT,
    strictPort: true,
    proxy: { '/api': `http://127.0.0.1:${FIXTURE_API_PORT}` },
  },
});
