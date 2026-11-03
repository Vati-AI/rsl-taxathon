<script setup lang="ts">
import { computed } from 'vue';
import { Badge } from 'frappe-ui';
import { reviewLane } from '../domain/cases';

const props = defineProps<{ lane: string }>();
const lane = computed(() => reviewLane(props.lane));
</script>

<template>
  <!-- Text plus colour: the label alone carries the meaning. A lane is a review signal, not a verdict. -->
  <Badge :theme="lane.theme" :label="lane.label" class="whitespace-nowrap" />
</template>
