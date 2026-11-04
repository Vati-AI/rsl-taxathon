<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Alert, Button, Skeleton, TabList, TabPanel, TabTrigger, Tabs } from 'frappe-ui';
import { getCaseRecords } from '../api/caseRecords';
import { getCase } from '../api/vati';
import AmountText from '../components/AmountText.vue';
import CaseEvidenceTab from '../components/CaseEvidenceTab.vue';
import CaseFindingsTab from '../components/CaseFindingsTab.vue';
import CaseRelationshipsTab from '../components/CaseRelationshipsTab.vue';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import PageLayout from '../components/PageLayout.vue';
import SelectionReasons from '../components/SelectionReasons.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { laneLabel, processingState, serviceLabel, taxpayerTypeLabel } from '../domain/cases';
import { formatTimestamp } from '../domain/dates';

const props = defineProps<{ caseId: string }>();
const { state, reload } = useLoader(
  (signal) => getCase(props.caseId, fetch, signal),
  () => props.caseId,
);
const item = computed(() => (state.value.status === 'ready' ? state.value.data : null));
const processing = computed(() => (item.value ? processingState(item.value) : null));

// Evidence, relationships and findings (TAX-041) load together from listEvidence,
// listEntities and listFindings, so citations can be checked against stored evidence.
const { state: recordState, reload: reloadRecords } = useLoader(
  (signal) => getCaseRecords(props.caseId, fetch, signal),
  () => props.caseId,
);
const recordTabs = [
  { value: 'evidence', label: 'Evidence' },
  { value: 'relationships', label: 'Relationships' },
  { value: 'findings', label: 'Findings' },
] as const;

// The open tab lives in the URL so a link back from a cited source lands on it.
const TABS: string[] = ['overview', ...recordTabs.map((entry) => entry.value)];
const route = useRoute();
const router = useRouter();
const fromQuery = () =>
  typeof route.query.tab === 'string' && TABS.includes(route.query.tab)
    ? route.query.tab
    : 'overview';
const tab = ref(fromQuery());
const focusFinding = computed(() =>
  typeof route.query.finding === 'string' ? route.query.finding : undefined,
);
watch(
  () => route.query.tab,
  () => (tab.value = fromQuery()),
);
watch(tab, (value) => {
  if (value === fromQuery()) return;
  const { finding, ...rest } = route.query;
  const query = { ...rest, tab: value === 'overview' ? undefined : value };
  void router.replace({ query: value === 'findings' ? { ...query, finding } : query });
});
const SYNC_LABELS = { synced: 'Up to date', pending: 'Waiting', failed: 'Failed' } as const;
</script>

<template>
  <PageLayout
    :title="item ? `${item.display_id} · ${item.taxpayer.display_name}` : 'Case'"
    :breadcrumbs="[
      { label: 'My case queue', route: { name: 'queue' } },
      { label: item ? item.display_id : 'Case' },
    ]"
  >
    <template v-if="item" #actions>
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
      <Skeleton class="h-6 w-48 rounded-control" />
      <Skeleton class="h-32 w-full rounded-surface" />
    </div>
    <div v-else-if="state.status === 'error'">
      <StatePanel v-bind="errorPanel(state.error)" @retry="reload">
        <Button label="Back to my case queue" :route="{ name: 'queue' }" />
      </StatePanel>
    </div>
    <template v-else-if="item && processing">
      <div class="mb-4 flex flex-wrap items-center gap-2">
        <CaseStatusBadge :status="item.status" />
        <span class="text-ink-gray-5">Refund lane: {{ laneLabel(item.refund_lane) }}</span>
      </div>
      <Alert
        v-if="processing.kind === 'failed'"
        theme="red"
        class="mb-4"
        title="Processing failed"
        :description="`${processing.text}. The service retries automatically; this case has no findings until processing succeeds.`"
      />
      <Tabs v-model="tab">
        <TabList>
          <TabTrigger value="overview" label="Overview" />
          <TabTrigger
            v-for="entry in recordTabs"
            :key="entry.value"
            :value="entry.value"
            :label="entry.label"
          />
        </TabList>
        <TabPanel value="overview" class="pt-4">
          <!-- minmax(0, …) lets wide children (the service table) scroll instead of widening the column. -->
          <div
            class="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]"
          >
            <section
              aria-labelledby="reasons-heading"
              class="rounded-surface border border-outline-gray-1 bg-surface-base"
            >
              <h2
                id="reasons-heading"
                class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
              >
                Why this case was selected
              </h2>
              <div class="px-4 py-4">
                <SelectionReasons :reasons="item.selection_reasons" />
                <p class="mt-3 text-sm text-ink-gray-5">
                  Selection signals route work for review. They are not findings, assessments or
                  decisions.
                </p>
              </div>
            </section>
            <section
              aria-labelledby="taxpayer-heading"
              class="rounded-surface border border-outline-gray-1 bg-surface-base"
            >
              <h2
                id="taxpayer-heading"
                class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
              >
                Taxpayer
              </h2>
              <div class="space-y-2 px-4 py-4">
                <p class="break-words text-ink-gray-8">{{ item.taxpayer.display_name }}</p>
                <p class="text-sm text-ink-gray-5">
                  {{ taxpayerTypeLabel(item.taxpayer.taxpayer_type) }} · synthetic record
                </p>
                <RouterLink
                  :to="{ name: 'taxpayer', params: { taxpayerId: item.taxpayer_id } }"
                  class="inline-flex items-center gap-1 text-ink-blue-link underline-offset-2 hover:underline"
                >
                  View taxpayer profile
                  <span class="lucide-arrow-right size-4" aria-hidden="true" />
                </RouterLink>
              </div>
            </section>
            <section
              aria-labelledby="summary-heading"
              class="rounded-surface border border-outline-gray-1 bg-surface-base lg:col-span-2"
            >
              <h2
                id="summary-heading"
                class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
              >
                Case summary
              </h2>
              <dl class="grid gap-x-6 gap-y-3 px-4 py-4 sm:grid-cols-2 lg:grid-cols-3">
                <div>
                  <dt class="text-sm text-ink-gray-5">Case reference</dt>
                  <dd class="text-ink-gray-8">{{ item.display_id }}</dd>
                </div>
                <div>
                  <dt class="text-sm text-ink-gray-5">Refund claimed</dt>
                  <dd class="text-ink-gray-8">
                    <AmountText
                      :value="item.refund_claim?.amount ?? null"
                      :currency="item.refund_claim?.currency ?? ''"
                    />
                  </dd>
                </div>
                <div>
                  <dt class="text-sm text-ink-gray-5">Assigned officer</dt>
                  <dd class="text-ink-gray-8">
                    {{ item.assigned_officer_id ? 'Assigned' : 'Unassigned' }}
                  </dd>
                </div>
                <div>
                  <dt class="text-sm text-ink-gray-5">Opened</dt>
                  <dd class="text-ink-gray-8">{{ formatTimestamp(item.created_at) }}</dd>
                </div>
                <div>
                  <dt class="text-sm text-ink-gray-5">Last updated</dt>
                  <dd class="text-ink-gray-8">{{ formatTimestamp(item.updated_at) }}</dd>
                </div>
                <div>
                  <dt class="text-sm text-ink-gray-5">Case version</dt>
                  <dd class="text-ink-gray-8">{{ item.version }}</dd>
                </div>
              </dl>
            </section>
            <section aria-labelledby="processing-heading" class="lg:col-span-2">
              <h2 id="processing-heading" class="mb-2 text-lg font-semibold text-ink-gray-9">
                Processing by service
              </h2>
              <div
                role="region"
                aria-label="Service processing table"
                tabindex="0"
                class="overflow-x-auto rounded-surface border border-outline-gray-1 bg-surface-base"
              >
                <table class="w-full min-w-[560px] border-collapse text-left">
                  <thead
                    class="border-b border-outline-gray-1 bg-surface-gray-1 text-sm text-ink-gray-6"
                  >
                    <tr>
                      <th scope="col" class="px-3 py-2 font-medium">Service</th>
                      <th scope="col" class="px-3 py-2 font-medium">Record</th>
                      <th scope="col" class="px-3 py-2 font-medium">State</th>
                      <th scope="col" class="px-3 py-2 font-medium">Last attempt</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="link in item.service_links"
                      :key="link.service + link.external_type"
                      class="border-b border-outline-gray-1 last:border-b-0"
                    >
                      <td class="px-3 py-2 text-ink-gray-8">{{ serviceLabel(link.service) }}</td>
                      <td class="px-3 py-2 text-ink-gray-6">
                        {{ link.external_id ?? 'Not created yet' }}
                      </td>
                      <td
                        :class="[
                          'px-3 py-2',
                          link.sync.state === 'failed' ? 'text-ink-red-7' : 'text-ink-gray-7',
                        ]"
                      >
                        {{ SYNC_LABELS[link.sync.state] }}
                        <span v-if="link.sync.error_code" class="text-sm">
                          ({{ link.sync.error_code }})
                        </span>
                      </td>
                      <td class="px-3 py-2 text-ink-gray-6">
                        {{ formatTimestamp(link.sync.last_attempt_at) ?? 'Not attempted' }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </TabPanel>
        <TabPanel v-for="entry in recordTabs" :key="entry.value" :value="entry.value" class="pt-4">
          <div
            v-if="recordState.status === 'loading'"
            role="status"
            :aria-label="`Loading ${entry.label.toLowerCase()}`"
            class="space-y-3"
          >
            <Skeleton class="h-6 w-48 rounded-control" />
            <Skeleton class="h-32 w-full rounded-surface" />
          </div>
          <StatePanel
            v-else-if="recordState.status === 'error'"
            v-bind="errorPanel(recordState.error)"
            @retry="reloadRecords"
          />
          <template v-else>
            <CaseEvidenceTab
              v-if="entry.value === 'evidence'"
              :case-id="caseId"
              :records="recordState.data"
            />
            <CaseRelationshipsTab
              v-else-if="entry.value === 'relationships'"
              :case-id="caseId"
              :records="recordState.data"
            />
            <CaseFindingsTab
              v-else
              :case-id="caseId"
              :records="recordState.data"
              :focus-id="focusFinding"
            />
          </template>
        </TabPanel>
      </Tabs>
    </template>
  </PageLayout>
</template>
