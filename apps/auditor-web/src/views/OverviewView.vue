<script setup lang="ts">
import { computed } from 'vue';
import { Button } from 'frappe-ui';
import { listCases } from '../api/fixtures';
import PageLayout from '../components/PageLayout.vue';
import ReviewLaneBadge from '../components/ReviewLaneBadge.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { laneCounts } from '../domain/cases';

const { state, reload } = useLoader((signal) => listCases(fetch, signal));
const lanes = computed(() => (state.value.status === 'ready' ? laneCounts(state.value.data) : []));
const total = computed(() => (state.value.status === 'ready' ? state.value.data.length : 0));
</script>

<template>
  <PageLayout
    title="Overview"
    description="Cases waiting for officer review, grouped by the review lane proposed for each case."
    :breadcrumbs="[{ label: 'Overview' }]"
  >
    <template #actions>
      <Button variant="solid" theme="blue" label="Open case queue" :route="{ name: 'queue' }" />
    </template>

    <StatePanel v-if="state.status === 'loading'" kind="loading" title="Loading cases…" />
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error)"
      @retry="reload"
    />
    <StatePanel
      v-else-if="total === 0"
      kind="empty"
      title="No cases waiting for review"
      message="New cases appear here when submissions are routed for officer review."
    />
    <section
      v-else
      aria-labelledby="lanes-heading"
      class="rounded-surface border border-outline-gray-1 bg-surface-base"
    >
      <div
        class="flex flex-wrap items-baseline justify-between gap-2 border-b border-outline-gray-1 px-4 py-3"
      >
        <h2 id="lanes-heading" class="text-lg font-semibold text-ink-gray-9">Review lanes</h2>
        <p class="text-ink-gray-5">{{ total }} synthetic cases in the fixture set</p>
      </div>
      <ul class="divide-y divide-outline-gray-1">
        <li v-for="lane in lanes" :key="lane.key">
          <RouterLink
            :to="{ name: 'queue', query: { lane: lane.key } }"
            class="flex items-center justify-between gap-3 px-4 py-3 hover:bg-surface-gray-1"
          >
            <ReviewLaneBadge :lane="lane.key" />
            <span class="tabular-nums text-ink-gray-8">
              {{ lane.count }} {{ lane.count === 1 ? 'case' : 'cases' }}
              <span class="sr-only">— view in queue</span>
            </span>
          </RouterLink>
        </li>
      </ul>
      <p class="border-t border-outline-gray-1 px-4 py-3 text-sm text-ink-gray-5">
        Review lanes route work to an officer. They are not findings, assessments or decisions.
      </p>
    </section>
  </PageLayout>
</template>
