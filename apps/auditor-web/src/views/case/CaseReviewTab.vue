<script setup lang="ts">
/** Case workspace · Review (TAX-148): decisions, evidence requests and referrals (TAX-042). */
import type { Case } from '../../api/vati';
import DecisionPanel from '../../components/DecisionPanel.vue';

defineOptions({ inheritAttrs: false });
defineProps<{ caseId: string; item: Case }>();
const emit = defineEmits<{ changed: [] }>();
</script>

<template>
  <DecisionPanel :item="item" @changed="emit('changed')" />
</template>
