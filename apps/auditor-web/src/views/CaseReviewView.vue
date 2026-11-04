<script setup lang="ts">
import { computed } from 'vue';
import { Button, Skeleton } from 'frappe-ui';
import { getCase } from '../api/vati';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import DecisionPanel from '../components/DecisionPanel.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { laneLabel } from '../domain/cases';

/** Officer review of one case: decisions, evidence requests and referrals (TAX-042). */
const props = defineProps<{ caseId: string }>();
const { state, reload, refresh } = useLoader(
  (signal) => getCase(props.caseId, fetch, signal),
  () => props.caseId,
);
const item = computed(() => (state.value.status === 'ready' ? state.value.data : null));
// Two items only: at narrow widths frappe-ui collapses a middle item into a menu
// button without an accessible name. The "Back to case" action links to the case.
const breadcrumbs = computed(() => [
  { label: 'My case queue', route: { name: 'queue' } },
  { label: item.value ? `${item.value.display_id} · Review and decide` : 'Review and decide' },
]);
</script>

<template>
  <PageLayout
    :title="item ? `Review and decide · ${item.display_id}` : 'Review and decide'"
    :breadcrumbs="breadcrumbs"
  >
    <template v-if="item" #actions>
      <Button
        label="Back to case"
        icon-left="lucide-arrow-left"
        :route="{ name: 'case', params: { caseId } }"
      />
    </template>
    <div
      v-if="state.status === 'loading'"
      role="status"
      aria-label="Loading case"
      class="space-y-3"
    >
      <Skeleton class="h-6 w-48 rounded-control" />
      <Skeleton class="h-40 w-full rounded-surface" />
    </div>
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error)"
      @retry="reload"
    >
      <Button label="Back to my case queue" :route="{ name: 'queue' }" />
    </StatePanel>
    <template v-else-if="item">
      <div class="mb-4 flex flex-wrap items-center gap-2">
        <CaseStatusBadge :status="item.status" />
        <span class="break-words text-ink-gray-7">{{ item.taxpayer.display_name }}</span>
        <span class="text-ink-gray-5">· Refund lane: {{ laneLabel(item.refund_lane) }}</span>
      </div>
      <DecisionPanel :item="item" @changed="refresh" />
    </template>
  </PageLayout>
</template>
