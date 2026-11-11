<script setup lang="ts">
import { computed } from 'vue';
import { Alert, Button } from 'frappe-ui';
import { listAssignedCases } from '../api/vati';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { processingState, statusCounts } from '../domain/cases';

const { state, reload } = useLoader((signal) => listAssignedCases(fetch, signal));
const cases = computed(() => (state.value.status === 'ready' ? state.value.data : []));
const counts = computed(() => statusCounts(cases.value));
const failed = computed(() => cases.value.filter((c) => processingState(c).kind === 'failed'));
</script>

<template>
  <PageLayout
    title="Overview"
    description="Your assigned cases by status. Statuses come from Investigation, the case management app; they describe processing, not outcomes."
    :breadcrumbs="[{ label: 'Overview' }]"
  >
    <template #actions>
      <Button variant="solid" theme="blue" label="Open my case queue" :route="{ name: 'queue' }" />
    </template>

    <StatePanel v-if="state.status === 'loading'" kind="loading" title="Loading your cases…" />
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error)"
      @retry="reload"
    />
    <StatePanel
      v-else-if="cases.length === 0"
      kind="empty"
      title="No cases are assigned to you"
      message="Cases appear here when they are assigned to you for review."
    />
    <div v-else class="space-y-4">
      <Alert
        v-if="failed.length"
        theme="red"
        :title="`${failed.length} ${failed.length === 1 ? 'case needs' : 'cases need'} attention: processing failed`"
        description="A connected service could not finish processing. These cases have no findings until processing succeeds."
      >
        <template #actions>
          <Button
            label="View failed cases"
            :route="{ name: 'queue', query: { status: 'FAILED_PROCESSING' } }"
          />
        </template>
      </Alert>
      <section
        aria-labelledby="status-heading"
        class="rounded-surface border border-outline-gray-1 bg-surface-base"
      >
        <div
          class="flex flex-wrap items-baseline justify-between gap-2 border-b border-outline-gray-1 px-4 py-3"
        >
          <h2 id="status-heading" class="text-lg font-semibold text-ink-gray-9">
            Assigned cases by status
          </h2>
          <p class="text-ink-gray-5">{{ cases.length }} assigned to you</p>
        </div>
        <ul class="divide-y divide-outline-gray-1">
          <li v-for="entry in counts" :key="entry.status">
            <RouterLink
              :to="{ name: 'queue', query: { status: entry.status } }"
              class="flex items-center justify-between gap-3 px-4 py-3 hover:bg-surface-gray-1"
            >
              <CaseStatusBadge :status="entry.status" />
              <span class="tabular-nums text-ink-gray-8">
                {{ entry.count }} {{ entry.count === 1 ? 'case' : 'cases' }}
                <span class="sr-only">— view in queue</span>
              </span>
            </RouterLink>
          </li>
        </ul>
      </section>
    </div>
  </PageLayout>
</template>
