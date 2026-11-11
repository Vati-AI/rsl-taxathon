<script setup lang="ts">
/**
 * Key-facts strip (TAX-145): the four to six facts a reader needs first, in one row that
 * wraps on small screens. An empty value shows a muted dash, read as "Not available".
 */
import type { KeyFact } from './types';

defineProps<{ facts: KeyFact[]; label?: string }>();
</script>

<template>
  <dl
    data-kit="key-facts"
    :aria-label="label"
    class="grid grid-cols-2 gap-px overflow-hidden rounded-surface border border-vati-border bg-vati-border sm:grid-cols-3 lg:grid-flow-col lg:auto-cols-fr lg:grid-cols-none"
  >
    <div v-for="fact in facts" :key="fact.label" class="min-w-0 bg-vati-surface px-4 py-3">
      <dt class="text-sm text-vati-muted">{{ fact.label }}</dt>
      <dd
        :class="['mt-0.5 break-words font-semibold text-vati-text', fact.numeric && 'vati-tabular']"
      >
        <template v-if="fact.value !== null && fact.value !== undefined && fact.value !== ''">
          {{ fact.value }}
        </template>
        <template v-else>
          <span aria-hidden="true" class="font-normal text-vati-muted">—</span>
          <span class="vati-sr-only">Not available</span>
        </template>
      </dd>
    </div>
  </dl>
</template>
