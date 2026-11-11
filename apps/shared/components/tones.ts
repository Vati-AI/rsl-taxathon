/**
 * Status tones shared by the Vati Workspace kit (docs/DESIGN_STANDARD.md v2, TAX-145).
 * Every tone pairs a background, border and text token from tokens.css whose contrast
 * is measured in contrast-pairs.json. Status is always shown with text, never colour
 * alone; risk is a review signal, never a verdict.
 */
export type Tone = 'info' | 'success' | 'warning' | 'danger' | 'neutral';

export const TONES: readonly Tone[] = ['info', 'success', 'warning', 'danger', 'neutral'];

/** Tailwind classes for a tone (classes are spelled out so Tailwind keeps them). */
export const TONE_CLASSES: Record<Tone, { surface: string; text: string; dot: string }> = {
  info: {
    surface: 'bg-vati-info-bg border-vati-info-border',
    text: 'text-vati-info-text',
    dot: 'bg-vati-info-text',
  },
  success: {
    surface: 'bg-vati-success-bg border-vati-success-border',
    text: 'text-vati-success-text',
    dot: 'bg-vati-success-text',
  },
  warning: {
    surface: 'bg-vati-warning-bg border-vati-warning-border',
    text: 'text-vati-warning-text',
    dot: 'bg-vati-warning-text',
  },
  danger: {
    surface: 'bg-vati-danger-bg border-vati-danger-border',
    text: 'text-vati-danger-text',
    dot: 'bg-vati-danger-text',
  },
  neutral: {
    surface: 'bg-vati-neutral-bg border-vati-neutral-border',
    text: 'text-vati-neutral-text',
    dot: 'bg-vati-neutral-text',
  },
};

/** The icon that accompanies a tone in notices and toasts. */
export const TONE_ICONS: Record<Tone, string> = {
  info: 'lucide-info',
  success: 'lucide-circle-check',
  warning: 'lucide-triangle-alert',
  danger: 'lucide-circle-alert',
  neutral: 'lucide-circle-minus',
};
