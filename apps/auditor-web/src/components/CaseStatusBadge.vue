<script setup lang="ts">
import { computed } from 'vue';
import { Badge } from 'frappe-ui';
import type { CaseStatus } from '../api/vati';
import { statusInfo } from '../domain/cases';

const props = defineProps<{ status: CaseStatus }>();
const info = computed(() => statusInfo(props.status));
</script>

<template>
  <!-- Text plus colour: the label carries the meaning. Status is a processing stage, never a verdict. -->
  <Badge :theme="info.theme" :label="info.label" class="whitespace-nowrap" />
</template>
