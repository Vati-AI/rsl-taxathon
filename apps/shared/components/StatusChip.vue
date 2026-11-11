<script setup lang="ts">
/**
 * Status chip (TAX-145): a short status in words with a tone. Colour never carries the
 * meaning alone; the label is always visible text.
 */
import { computed } from 'vue';
import { TONE_CLASSES, type Tone } from './tones';

const props = withDefaults(
  defineProps<{
    label: string;
    tone?: Tone;
    /** md for tables and lists, lg for page headers. */
    size?: 'md' | 'lg';
  }>(),
  { tone: 'neutral', size: 'md' },
);
const classes = computed(() => TONE_CLASSES[props.tone]);
</script>

<template>
  <span
    data-kit="status-chip"
    :data-tone="tone"
    :class="[
      'inline-flex max-w-full items-center gap-1.5 rounded-full border font-medium',
      size === 'lg' ? 'px-2.5 py-1 text-sm' : 'px-2 py-0.5 text-xs',
      classes.surface,
      classes.text,
    ]"
  >
    <span aria-hidden="true" :class="['size-1.5 shrink-0 rounded-full', classes.dot]" />
    <span class="truncate">{{ label }}</span>
  </span>
</template>
