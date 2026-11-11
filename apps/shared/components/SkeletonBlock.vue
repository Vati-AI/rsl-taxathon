<script setup lang="ts">
/**
 * Skeleton loading (TAX-145): grey placeholder lines in the shape of what is coming,
 * announced once to assistive technology as "Loading …". Motion stops under
 * prefers-reduced-motion (tokens.css).
 */
withDefaults(
  defineProps<{
    /** What is loading, read by screen readers: "Loading cases". */
    label: string;
    lines?: number;
    /** rows: table-like full-width rows; text: paragraph lines of varying width. */
    variant?: 'text' | 'rows';
  }>(),
  { lines: 3, variant: 'text' },
);
const widths = ['w-11/12', 'w-9/12', 'w-10/12', 'w-7/12', 'w-8/12'];
</script>

<template>
  <div data-kit="skeleton" role="status" aria-live="polite" class="w-full">
    <span class="vati-sr-only">{{ label }}…</span>
    <div aria-hidden="true" class="flex flex-col gap-2">
      <div
        v-for="n in lines"
        :key="n"
        :class="[
          'animate-pulse rounded-control bg-vati-surface-subtle',
          variant === 'rows' ? 'h-vati-row w-full' : ['h-3.5', widths[(n - 1) % widths.length]],
        ]"
      />
    </div>
  </div>
</template>
