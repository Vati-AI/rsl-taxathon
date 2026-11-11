<script setup lang="ts">
/**
 * Usage line above a table (TAX-145): "Showing 25 of 143 cases · 3 overdue". Counts are
 * real values passed in by the page, never invented.
 */
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    shown: number;
    total: number;
    /** Plural noun: "cases", "requests". */
    noun: string;
    /** Extra facts after the count: ["3 overdue"]. */
    extras?: string[];
  }>(),
  { extras: () => [] },
);
const text = computed(() =>
  [`Showing ${props.shown} of ${props.total} ${props.noun}`, ...props.extras].join(' · '),
);
</script>

<template>
  <p data-kit="usage-line" class="vati-tabular text-sm text-vati-muted" aria-live="polite">
    {{ text }}
  </p>
</template>
