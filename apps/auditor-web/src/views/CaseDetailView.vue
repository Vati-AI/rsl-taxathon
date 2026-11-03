<script setup lang="ts">
import { ref } from 'vue';
import { Button, Skeleton, TabList, TabPanel, TabTrigger, Tabs } from 'frappe-ui';
import { getCase } from '../api/fixtures';
import AmountText from '../components/AmountText.vue';
import PageLayout from '../components/PageLayout.vue';
import ReviewLaneBadge from '../components/ReviewLaneBadge.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { scenarioLabel } from '../domain/cases';

const props = defineProps<{ id: string }>();
const { state, reload } = useLoader(
  (signal) => getCase(props.id, fetch, signal),
  () => props.id,
);
const tab = ref('overview');

// These sections map to operations in integration/api/openapi.json (TAX-018),
// which the read-only fixture server does not serve. Say so rather than invent data.
const pending = [
  { value: 'evidence', label: 'Evidence', endpoint: '/api/v1/cases/{case_id}/evidence' },
  { value: 'entities', label: 'Entities', endpoint: '/api/v1/cases/{case_id}/entities' },
  { value: 'findings', label: 'Findings', endpoint: '/api/v1/cases/{case_id}/findings' },
];
</script>

<template>
  <PageLayout
    :title="state.status === 'ready' ? `${state.data.id} · ${state.data.name}` : `Case ${id}`"
    :breadcrumbs="[{ label: 'Case queue', route: { name: 'queue' } }, { label: id }]"
  >
    <template v-if="state.status === 'ready'" #actions>
      <span id="request-evidence-reason" class="hidden text-sm text-ink-gray-5 md:inline">
        Not available in fixture mode
      </span>
      <Button
        variant="solid"
        theme="blue"
        label="Request evidence"
        icon-left="lucide-file-question"
        disabled
        aria-describedby="request-evidence-reason"
        tooltip="Evidence requests need the Frappe portal and officer approval flow (later tasks). Fixture mode is read-only."
      />
    </template>

    <p v-if="state.status === 'ready'" class="mb-3 text-sm text-ink-gray-5 md:hidden">
      Request evidence is not available in fixture mode.
    </p>
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
        <Button label="Back to case queue" :route="{ name: 'queue' }" />
      </StatePanel>
    </div>
    <template v-else>
      <div class="mb-4 flex flex-wrap items-center gap-2">
        <ReviewLaneBadge :lane="state.data.expected" />
        <span class="text-ink-gray-5">{{ scenarioLabel(state.data.scenario) }}</span>
      </div>
      <Tabs v-model="tab">
        <TabList>
          <TabTrigger value="overview" label="Overview" />
          <TabTrigger
            v-for="item in pending"
            :key="item.value"
            :value="item.value"
            :label="item.label"
          />
        </TabList>
        <TabPanel value="overview" class="pt-4">
          <section
            aria-labelledby="summary-heading"
            class="rounded-surface border border-outline-gray-1 bg-surface-base"
          >
            <h2
              id="summary-heading"
              class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
            >
              Case summary
            </h2>
            <dl class="grid gap-x-6 gap-y-3 px-4 py-4 sm:grid-cols-2">
              <div>
                <dt class="text-sm text-ink-gray-5">Case reference</dt>
                <dd class="text-ink-gray-8">{{ state.data.id }}</dd>
              </div>
              <div>
                <dt class="text-sm text-ink-gray-5">Taxpayer (synthetic)</dt>
                <dd class="break-words text-ink-gray-8">{{ state.data.name }}</dd>
              </div>
              <div>
                <dt class="text-sm text-ink-gray-5">Scenario</dt>
                <dd class="text-ink-gray-8">{{ scenarioLabel(state.data.scenario) }}</dd>
              </div>
              <div>
                <dt class="text-sm text-ink-gray-5">Currency</dt>
                <dd class="text-ink-gray-8">{{ state.data.currency }}</dd>
              </div>
              <div>
                <dt class="text-sm text-ink-gray-5">Refund claimed</dt>
                <dd class="text-ink-gray-8">
                  <AmountText :value="state.data.refund_claim" :currency="state.data.currency" />
                </dd>
              </div>
              <div>
                <dt class="text-sm text-ink-gray-5">Supported refund (fixture)</dt>
                <dd class="text-ink-gray-8">
                  <AmountText
                    :value="state.data.supported_refund"
                    :currency="state.data.currency"
                  />
                </dd>
              </div>
              <div class="sm:col-span-2">
                <dt class="text-sm text-ink-gray-5">Proposed review lane</dt>
                <dd class="flex flex-wrap items-center gap-2 text-ink-gray-8">
                  <ReviewLaneBadge :lane="state.data.expected" />
                  <span class="text-sm text-ink-gray-5">
                    Fixture expectation for routing — an officer decides the outcome.
                  </span>
                </dd>
              </div>
            </dl>
          </section>
        </TabPanel>
        <TabPanel v-for="item in pending" :key="item.value" :value="item.value" class="pt-4">
          <StatePanel
            kind="unavailable"
            :title="`${item.label} are not available in fixture mode`"
            :message="`This section will load from ${item.endpoint} (Vati API contract) when that service runs; the fixture server does not provide it. No ${item.label.toLowerCase()} are shown rather than invented.`"
          />
        </TabPanel>
      </Tabs>
    </template>
  </PageLayout>
</template>
