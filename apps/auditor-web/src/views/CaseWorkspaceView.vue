<script setup lang="ts">
/**
 * The case workspace (TAX-148, DESIGN_STANDARD.md v2 layout rules 2 and 4). One page
 * header (case title, status chip, one primary action), a key-facts strip and routed
 * tabs: Summary, Evidence, Relationships, Findings and Review, each with its own URL.
 * The case and its records (TAX-041) load once here and are handed to the open tab, so
 * switching tabs does not reload them.
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { Alert, Button, Skeleton } from 'frappe-ui';
import { KeyFacts, RoutedTabs, type KeyFact, type RoutedTab } from '../../../shared/components';
import { getCaseRecords } from '../api/caseRecords';
import { getCase } from '../api/vati';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { laneLabel, processingState, taxpayerTypeLabel } from '../domain/cases';
import { formatTimestamp } from '../domain/dates';
import { formatAmount } from '../domain/money';

const props = defineProps<{ caseId: string }>();
const route = useRoute();

const {
  state,
  reload,
  refresh: refreshCase,
} = useLoader(
  (signal) => getCase(props.caseId, fetch, signal),
  () => props.caseId,
);
const item = computed(() => (state.value.status === 'ready' ? state.value.data : null));
const processing = computed(() => (item.value ? processingState(item.value) : null));

// Evidence, relationships and findings load together, so citations can be checked
// against stored evidence on every tab.
const { state: recordState, reload: reloadRecords } = useLoader(
  (signal) => getCaseRecords(props.caseId, fetch, signal),
  () => props.caseId,
);
const records = computed(() =>
  recordState.value.status === 'ready' ? recordState.value.data : null,
);

const current = computed(() => (typeof route.name === 'string' ? route.name : null));
const onReview = computed(() => current.value === 'case-review');
const params = computed(() => ({ caseId: props.caseId }));
const tabs = computed<RoutedTab[]>(() => [
  { label: 'Summary', to: { name: 'case', params: params.value }, match: 'case' },
  {
    label: 'Evidence',
    to: { name: 'case-evidence', params: params.value },
    match: ['case-evidence', 'evidence'],
    count: records.value?.evidence.length,
  },
  {
    label: 'Relationships',
    to: { name: 'case-relationships', params: params.value },
    match: 'case-relationships',
    count: records.value?.entities.length,
  },
  {
    label: 'Findings',
    to: { name: 'case-findings', params: params.value },
    match: 'case-findings',
    count: records.value?.findings.length,
  },
  { label: 'Review', to: { name: 'case-review', params: params.value }, match: 'case-review' },
]);

const currentTab = computed(
  () =>
    tabs.value.find((t) =>
      (Array.isArray(t.match) ? t.match : [t.match]).includes(current.value ?? ''),
    )?.label ?? 'Case',
);
const title = computed(() => {
  if (!item.value) return onReview.value ? 'Review and decide' : 'Case';
  return onReview.value
    ? `Review and decide · ${item.value.display_id}`
    : `${item.value.display_id} · ${item.value.taxpayer.display_name}`;
});
// Two items only: at narrow widths frappe-ui collapses a middle item into a menu button
// without an accessible name.
const breadcrumbs = computed(() => [
  { label: 'My case queue', route: { name: 'queue' } },
  { label: item.value ? item.value.display_id : 'Case' },
]);
const facts = computed<KeyFact[]>(() => {
  const c = item.value;
  if (!c) return [];
  return [
    { label: 'Taxpayer', value: `${c.taxpayer.display_name} (${taxpayerTypeLabel(c.taxpayer.taxpayer_type).toLowerCase()})` },
    { label: 'Refund claimed', value: formatAmount(c.refund_claim?.amount ?? null, c.refund_claim?.currency ?? ''), numeric: true },
    { label: 'Refund lane', value: laneLabel(c.refund_lane) },
    { label: 'Opened', value: formatTimestamp(c.created_at), numeric: true },
    { label: 'Last updated', value: formatTimestamp(c.updated_at), numeric: true },
    { label: 'Case version', value: String(c.version), numeric: true },
  ];
}); // prettier-ignore
</script>

<template>
  <PageLayout
    :title="title"
    :description="
      onReview
        ? 'Propose, approve or request evidence. Every action is confirmed before it is recorded.'
        : undefined
    "
    :breadcrumbs="breadcrumbs"
  >
    <template v-if="item" #status><CaseStatusBadge :status="item.status" /></template>
    <template v-if="item && !onReview" #actions>
      <Button
        variant="solid"
        theme="blue"
        label="Review and decide"
        icon-left="lucide-gavel"
        :route="{ name: 'case-review', params: { caseId } }"
      />
    </template>

    <div
      v-if="state.status === 'loading'"
      role="status"
      aria-label="Loading case"
      class="space-y-3"
    >
      <Skeleton class="h-16 w-full rounded-surface" />
      <Skeleton class="h-10 w-80 rounded-control" />
      <Skeleton class="h-32 w-full rounded-surface" />
    </div>
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error)"
      @retry="reload"
    >
      <Button label="Back to my case queue" :route="{ name: 'queue' }" />
    </StatePanel>
    <template v-else-if="item && processing">
      <KeyFacts :facts="facts" label="Key facts" class="mb-4" />
      <Alert
        v-if="processing.kind === 'failed'"
        theme="red"
        class="mb-4"
        title="Processing failed"
        :description="`${processing.text}. The service retries automatically; this case has no findings until processing succeeds.`"
      />
      <RoutedTabs :tabs="tabs" label="Case sections" :current="current" />
      <RouterView v-slot="{ Component }">
        <!-- The open tab's content, named after the tab, like a tab panel. -->
        <section :aria-label="currentTab" data-kit="case-tab-panel">
          <component
            :is="Component"
            :case-id="caseId"
            :item="item"
            :records="records"
            :record-state="recordState"
            @reload-records="reloadRecords"
            @changed="refreshCase"
          />
        </section>
      </RouterView>
    </template>
  </PageLayout>
</template>
