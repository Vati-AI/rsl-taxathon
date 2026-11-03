<script setup lang="ts">
import { computed } from 'vue';
import type { Case } from '../api/vati';
import { processingState } from '../domain/cases';

const props = defineProps<{ item: Case }>();
const state = computed(() => processingState(props.item));
</script>

<template>
  <span v-if="state.kind === 'failed'" class="inline-flex items-start gap-1 text-ink-red-7">
    <span class="lucide-circle-alert mt-0.5 size-4 shrink-0" aria-hidden="true" />
    <span>{{ state.text }}</span>
  </span>
  <span v-else-if="state.kind === 'pending'" class="inline-flex items-start gap-1 text-ink-gray-5">
    <span class="lucide-loader mt-0.5 size-4 shrink-0" aria-hidden="true" />
    <span>{{ state.text }}</span>
  </span>
  <span v-else class="inline-flex items-center gap-1 text-ink-gray-5">
    <span class="lucide-check size-4 shrink-0" aria-hidden="true" />
    <span>Up to date</span>
  </span>
</template>
