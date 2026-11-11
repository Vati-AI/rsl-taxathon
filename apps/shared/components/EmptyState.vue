<script setup lang="ts">
/**
 * Empty state (TAX-145): one icon slot, one heading, one sentence and the two or three
 * actions that make sense here. No illustrations borrowed from other products.
 */
withDefaults(
  defineProps<{
    title: string;
    message?: string;
    /** An outline icon class, e.g. lucide-inbox. */
    icon?: string;
    /** Heading level inside the page outline. */
    headingLevel?: 2 | 3;
  }>(),
  { icon: 'lucide-inbox', message: undefined, headingLevel: 2 },
);
</script>

<template>
  <div
    data-kit="empty-state"
    class="flex flex-col items-center gap-2 rounded-surface border border-dashed border-vati-border-strong bg-vati-surface px-6 py-10 text-center"
  >
    <span aria-hidden="true" :class="[icon, 'size-6 text-vati-muted']" />
    <component :is="`h${headingLevel}`" class="text-base font-semibold text-vati-text">
      {{ title }}
    </component>
    <p v-if="message" class="max-w-prose text-vati-muted">{{ message }}</p>
    <div v-if="$slots.actions" class="mt-2 flex flex-wrap justify-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
