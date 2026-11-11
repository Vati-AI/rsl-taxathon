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
          'surface-subtle': 'var(--vati-surface-subtle)',
          'accent-hover': 'var(--vati-accent-hover)',
          'accent-subtle': 'var(--vati-accent-subtle)',
          'info-bg': 'var(--vati-info-bg)',
          'info-border': 'var(--vati-info-border)',
          'info-text': 'var(--vati-info-text)',
          'success-bg': 'var(--vati-success-bg)',
          'success-border': 'var(--vati-success-border)',
          'success-text': 'var(--vati-success-text)',
          'warning-bg': 'var(--vati-warning-bg)',
          'warning-border': 'var(--vati-warning-border)',
          'warning-text': 'var(--vati-warning-text)',
          'danger-bg': 'var(--vati-danger-bg)',
          'danger-border': 'var(--vati-danger-border)',
          'danger-text': 'var(--vati-danger-text)',
          'neutral-bg': 'var(--vati-neutral-bg)',
          'neutral-border': 'var(--vati-neutral-border)',
          'neutral-text': 'var(--vati-neutral-text)',
          'nav-bg': 'var(--vati-nav-bg)',
          'nav-text': 'var(--vati-nav-text)',
          'nav-muted': 'var(--vati-nav-text-muted)',
          'nav-active': 'var(--vati-nav-active-bg)',
        },
      },
      borderRadius: {
        control: 'var(--vati-radius-control)',
        surface: 'var(--vati-radius-surface)',
      },
      boxShadow: {
        overlay: 'var(--vati-shadow-overlay)',
      },
      height: {
        'vati-header': 'var(--vati-header-height)',
        'vati-row': 'var(--vati-table-row)',
      },
      width: {
        'vati-sidebar': 'var(--vati-sidebar-width)',
        'vati-rail': 'var(--vati-rail-width)',
      },
      maxWidth: {
        'vati-content': 'var(--vati-content-max)',
      },
    },
  },
};

export default preset;
