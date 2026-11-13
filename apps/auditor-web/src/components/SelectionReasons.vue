<script setup lang="ts">
import type { SelectionReason } from '../api/vati';
import { reasonSourceLabel } from '../domain/cases';

// compact: first reason plus a count, for table cells. Full: every reason with its source.
defineProps<{ reasons: SelectionReason[]; compact?: boolean }>();
</script>

<template>
  <span v-if="!reasons.length" class="italic text-ink-gray-5">No reasons recorded yet</span>
  <span v-else-if="compact" class="block">
    <span class="line-clamp-2">{{ reasons[0].summary }}</span>
    <span v-if="reasons.length > 1" class="text-sm text-ink-gray-5">
      +{{ reasons.length - 1 }} more {{ reasons.length - 1 === 1 ? 'reason' : 'reasons' }}
    </span>
  </span>
  <ul v-else class="space-y-2">
    <li v-for="reason in reasons" :key="reason.code" class="flex gap-2">
      <span class="lucide-flag mt-1 size-4 shrink-0 text-ink-gray-5" aria-hidden="true" />
      <span>
        <span class="block text-ink-gray-8">{{ reason.summary }}</span>
        <span class="text-sm text-ink-gray-5">{{ reasonSourceLabel(reason.source) }}</span>
      </span>
    </li>
  </ul>
</template>
