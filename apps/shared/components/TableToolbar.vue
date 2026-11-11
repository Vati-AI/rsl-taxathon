<script setup lang="ts">
/**
 * Table toolbar (TAX-145): one row with search, then Filters, Display options and Export
 * (slots), and one primary action on the right. Wraps on small screens.
 */
import { TextInput } from 'frappe-ui';

withDefaults(
  defineProps<{
    /** Accessible name and placeholder of the search box. */
    searchLabel?: string;
    placeholder?: string;
  }>(),
  { searchLabel: 'Search', placeholder: 'Search' },
);
const query = defineModel<string>('query', { default: '' });
</script>

<template>
  <div
    data-kit="table-toolbar"
    role="toolbar"
    :aria-label="`${searchLabel} and table actions`"
    class="mb-3 flex flex-wrap items-center gap-2"
  >
    <div class="min-w-0 flex-1 basis-56 sm:max-w-sm">
      <TextInput
        v-model="query"
        type="search"
        :placeholder="placeholder"
        :aria-label="searchLabel"
        class="w-full"
      >
        <template #prefix>
          <span aria-hidden="true" class="lucide-search size-4 text-vati-muted" />
        </template>
      </TextInput>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <slot name="filters" />
      <slot name="display" />
      <slot name="export" />
    </div>
    <div v-if="$slots.primary" class="ml-auto flex items-center">
      <slot name="primary" />
    </div>
  </div>
</template>
