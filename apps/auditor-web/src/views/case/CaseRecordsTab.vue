<script setup lang="ts">
/**
 * Case workspace · Relationships or Findings (TAX-148): the TAX-041 record views, with
 * their own loading and error states while the case records load.
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { Skeleton } from 'frappe-ui';
import type { CaseRecords } from '../../api/caseRecords';
import CaseFindingsTab from '../../components/CaseFindingsTab.vue';
import CaseRelationshipsTab from '../../components/CaseRelationshipsTab.vue';
import StatePanel from '../../components/StatePanel.vue';
import { errorPanel } from '../../components/errorPanel';
import type { LoadState } from '../../composables/useLoader';

defineOptions({ inheritAttrs: false });
defineProps<{ caseId: string; recordState: LoadState<CaseRecords> }>();
const emit = defineEmits<{ 'reload-records': [] }>();
const route = useRoute();
const kind = computed(() => route.meta.records ?? 'findings');
const focusFinding = computed(() =>
  typeof route.query.finding === 'string' ? route.query.finding : undefined,
);
</script>

<template>
  <div
    v-if="recordState.status === 'loading'"
    role="status"
    :aria-label="`Loading ${kind}`"
    class="space-y-3"
  >
    <Skeleton class="h-6 w-48 rounded-control" />
    <Skeleton class="h-32 w-full rounded-surface" />
  </div>
  <StatePanel
    v-else-if="recordState.status === 'error'"
    v-bind="errorPanel(recordState.error)"
    @retry="emit('reload-records')"
  />
  <CaseRelationshipsTab
    v-else-if="kind === 'relationships'"
    :case-id="caseId"
    :records="recordState.data"
  />
  <CaseFindingsTab v-else :case-id="caseId" :records="recordState.data" :focus-id="focusFinding" />
</template>
