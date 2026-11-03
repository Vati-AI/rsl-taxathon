<script setup lang="ts">
import { computed } from 'vue';
import { formatAmount } from '../domain/money';

const props = defineProps<{ value: string | null; currency: string }>();
const text = computed(() => formatAmount(props.value, props.currency));
</script>

<template>
  <span v-if="text" class="whitespace-nowrap tabular-nums">{{ text }}</span>
  <!-- Missing is not zero: say so explicitly. -->
  <span v-else class="italic text-ink-gray-5">Not provided</span>
</template>
