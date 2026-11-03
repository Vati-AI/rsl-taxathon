<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router';
import { Button, Select, TextInput } from 'frappe-ui';
import { listCases } from '../api/fixtures';
import CaseTable from '../components/CaseTable.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import {
  queuePage,
  reviewLane,
  scenarioLabel,
  type QueueQuery,
  type SortDirection,
  type SortKey,
} from '../domain/cases';

const PAGE_SIZE = 25;
const SORT_KEYS: SortKey[] = [
  'id',
  'name',
  'scenario',
  'refund_claim',
  'supported_refund',
  'expected',
];

const route = useRoute();
const router = useRouter();
const { state, reload } = useLoader((signal) => listCases(fetch, signal));

function text(value: LocationQueryValue | LocationQueryValue[] | undefined) {
  return typeof value === 'string' ? value : '';
}

// Filters live in the URL so they survive reloads, links from the overview and Back.
const query = computed<QueueQuery>(() => {
  const sort = text(route.query.sort) as SortKey;
  return {
    search: text(route.query.q),
    scenario: text(route.query.scenario),
    lane: text(route.query.lane),
    sort: SORT_KEYS.includes(sort) ? sort : 'id',
    direction: (text(route.query.dir) === 'desc' ? 'desc' : 'asc') as SortDirection,
    page: Number.parseInt(text(route.query.page), 10) || 1,
    pageSize: PAGE_SIZE,
  };
});

function update(changes: Record<string, string | number | null>) {
  const next: Record<string, string> = {};
  for (const [key, value] of Object.entries({ ...route.query, ...changes })) {
    if (value !== null && value !== '' && value !== undefined) next[key] = String(value);
  }
  void router.replace({ query: next });
}

const cases = computed(() => (state.value.status === 'ready' ? state.value.data : []));
const page = computed(() => queuePage(cases.value, query.value));
const scenarioOptions = computed(() => [
  { label: 'All scenarios', value: '' },
  ...[...new Set(cases.value.map((x) => x.scenario))].map((value) => ({
    value,
    label: scenarioLabel(value),
  })),
]);
const laneOptions = computed(() => [
  { label: 'All review lanes', value: '' },
  ...[...new Set(cases.value.map((x) => x.expected))].map((value) => ({
    value,
    label: reviewLane(value).label,
  })),
]);
const filtered = computed(() =>
  Boolean(query.value.search || query.value.scenario || query.value.lane),
);

function sortBy(key: SortKey) {
  const dir = query.value.sort === key && query.value.direction === 'asc' ? 'desc' : 'asc';
  update({ sort: key, dir, page: null });
}
</script>

<template>
  <PageLayout
    title="Case queue"
    description="Synthetic cases routed for officer review. Select a case to open its detail page."
    :breadcrumbs="[{ label: 'Case queue' }]"
  >
    <form
      role="search"
      aria-label="Filter cases"
      class="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end"
      @submit.prevent
    >
      <TextInput
        label="Search"
        type="search"
        placeholder="Case reference or taxpayer name"
        :model-value="query.search"
        :debounce="200"
        @update:model-value="(value: string) => update({ q: value, page: null })"
      >
        <template #prefix><span class="lucide-search size-4" aria-hidden="true" /></template>
      </TextInput>
      <Select
        label="Scenario"
        :options="scenarioOptions"
        :model-value="query.scenario"
        @update:model-value="(value) => update({ scenario: (value as string) ?? '', page: null })"
      />
      <Select
        label="Review lane"
        :options="laneOptions"
        :model-value="query.lane"
        @update:model-value="(value) => update({ lane: (value as string) ?? '', page: null })"
      />
      <Button
        label="Clear filters"
        :disabled="!filtered"
        @click="update({ q: null, scenario: null, lane: null, page: null })"
      />
    </form>

    <StatePanel v-if="state.status === 'loading'" kind="loading" title="Loading cases…" />
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error)"
      @retry="reload"
    />
    <StatePanel
      v-else-if="page.total === 0"
      kind="empty"
      :title="filtered ? 'No cases match these filters' : 'No cases waiting for review'"
      :message="
        filtered
          ? 'Change the search text or clear the filters to see all cases.'
          : 'New cases appear here when submissions are routed for officer review.'
      "
    >
      <Button
        v-if="filtered"
        label="Clear filters"
        @click="update({ q: null, scenario: null, lane: null, page: null })"
      />
    </StatePanel>
    <template v-else>
      <CaseTable
        :rows="page.rows"
        caption="Cases waiting for review"
        :sort="query.sort"
        :direction="query.direction"
        @sort="sortBy"
      />
      <nav
        aria-label="Pagination"
        class="mt-3 flex flex-wrap items-center justify-between gap-2 text-ink-gray-6"
      >
        <p aria-live="polite">
          Showing {{ (page.page - 1) * PAGE_SIZE + 1 }}–{{
            (page.page - 1) * PAGE_SIZE + page.rows.length
          }}
          of {{ page.total }} cases
        </p>
        <div class="flex items-center gap-2">
          <Button
            label="Previous page"
            icon="lucide-chevron-left"
            :disabled="page.page <= 1"
            @click="update({ page: page.page - 1 })"
          />
          <span>Page {{ page.page }} of {{ page.pageCount }}</span>
          <Button
            label="Next page"
            icon="lucide-chevron-right"
            :disabled="page.page >= page.pageCount"
            @click="update({ page: page.page + 1 })"
          />
        </div>
      </nav>
    </template>
  </PageLayout>
</template>
