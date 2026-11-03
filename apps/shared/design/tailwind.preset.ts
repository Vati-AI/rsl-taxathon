import frappePreset from 'frappe-ui/tailwind';
import type { Config } from 'tailwindcss';

/**
 * Shared Tailwind preset for Vati custom frontends: frappe-ui 1.1.0 preset plus
 * the --vati-* tokens from tokens.css. Apps add their own `content` globs
 * (Tailwind 3 ignores `content` in presets).
 */
const preset: Partial<Config> = {
  presets: [frappePreset as Partial<Config>],
  theme: {
    extend: {
      colors: {
        vati: {
          canvas: 'var(--vati-canvas)',
          surface: 'var(--vati-surface)',
          border: 'var(--vati-border)',
          'border-strong': 'var(--vati-border-strong)',
          text: 'var(--vati-text)',
          muted: 'var(--vati-text-muted)',
          accent: 'var(--vati-accent)',
          'fixture-bg': 'var(--vati-fixture-bg)',
          'fixture-text': 'var(--vati-fixture-text)',
        },
      },
      borderRadius: {
        control: 'var(--vati-radius-control)',
        surface: 'var(--vati-radius-surface)',
      },
    },
  },
};

export default preset;
