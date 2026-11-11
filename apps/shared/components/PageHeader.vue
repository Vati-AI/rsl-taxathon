<script setup lang="ts">
/**
 * Page header (TAX-145, DESIGN_STANDARD.md v2 layout rule 2): breadcrumb, one title, one
 * plain sentence saying what the page is for, a status chip, at most one primary action
 * and up to two secondary actions. Nothing else sits above the fold except notices.
 *
 * Slots: `breadcrumbs`, `status` (a StatusChip), `actions` (secondary, then primary last).
 */
withDefaults(
  defineProps<{
    title: string;
    description?: string;
    /** id of the heading, for aria-labelledby on the page section. */
    headingId?: string;
    /** 1 on a page; a lower level only where the header is an example inside a page. */
    level?: 1 | 2 | 3;
  }>(),
  { description: undefined, headingId: 'page-title', level: 1 },
);
</script>

<template>
  <header
    data-kit="page-header"
    class="mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-3"
  >
    <div class="min-w-0 flex-1 basis-80">
      <slot name="breadcrumbs" />
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
        <component
          :is="`h${level}`"
          :id="headingId"
          class="break-words text-2xl font-semibold leading-tight text-vati-text"
        >
          {{ title }}
        </component>
        <slot name="status" />
      </div>
      <p v-if="description" class="mt-1 max-w-3xl text-base text-vati-muted">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="flex min-w-0 flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </header>
</template>
