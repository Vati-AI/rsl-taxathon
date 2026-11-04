<script setup lang="ts">
import { Skeleton } from 'frappe-ui';
import { listFindings } from '../api/review';
import { useLoader } from '../composables/useLoader';
import FindingCard from './FindingCard.vue';
import StatePanel from './StatePanel.vue';
import { errorPanel } from './errorPanel';

/** Read-only findings for a case (listFindings). Decisions are made on the review page. */
const props = defineProps<{ caseId: string }>();
const { state, reload } = useLoader(
  (signal) => listFindings(props.caseId, fetch, signal),
  () => props.caseId,
);
</script>

<template>
  <div
    v-if="state.status === 'loading'"
    role="status"
    aria-label="Loading findings"
    class="space-y-3"
  >
    <Skeleton class="h-24 w-full rounded-surface" />
  </div>
  <StatePanel
    v-else-if="state.status === 'error'"
    v-bind="errorPanel(state.error)"
    @retry="reload"
  />
  <StatePanel
    v-else-if="!state.data.length"
    kind="empty"
    title="No findings yet"
    message="Findings appear here when a risk rule or the investigation agent records them."
  />
  <ul v-else class="space-y-3">
    <li
      v-for="finding in state.data"
      :key="finding.finding_id"
      class="rounded-surface border border-outline-gray-1 bg-surface-base px-4 py-4"
    >
      <FindingCard :finding="finding" />
    </li>
  </ul>
</template>
