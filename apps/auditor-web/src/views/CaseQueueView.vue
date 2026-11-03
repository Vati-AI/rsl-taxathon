<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router';
import { Button, Select, TextInput } from 'frappe-ui';
import { CASE_STATUSES, listAssignedCases } from '../api/vati';
import CaseTable from '../components/CaseTable.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import {
  queuePage,
  statusInfo,
  type QueueQuery,
  type SortDirection,
  type SortKey,
} from '../domain/cases';

const PAGE_SIZE = 25;
const SORT_KEYS: SortKey[] = [
  'display_id',
  'taxpayer',
  'status',
  'refund_lane',
  'refund_claim',
  'updated_at',
];

const route = useRoute();
const router = useRouter();
const { state, reload } = useLoader((signal) => listAssignedCases(fetch, signal));

function text(value: LocationQueryValue | LocationQueryValue[] | undefined) {
  return typeof value === 'string' ? value : '';
}

// Filters live in the URL so they survive reloads, links from the overview and Back.
const query = computed<QueueQuery>(() => {
  const sort = text(route.query.sort) as SortKey;
  return {
    search: text(route.query.q),
    status: text(route.query.status),
    lane: text(route.query.lane),
    sort: SORT_KEYS.includes(sort) ? sort : 'display_id',
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
const statusOptions = [
  { label: 'All statuses', value: '' },
  ...CASE_STATUSES.map((value) => ({ value, label: statusInfo(value).label })),
];
const laneOptions = [
  { label: 'All refund lanes', value: '' },
  { label: 'Fast', value: 'fast' },
  { label: 'Standard', value: 'standard' },
  { label: 'Targeted', value: 'targeted' },
  { label: 'Not set', value: 'none' },
];
const filtered = computed(() =>
  Boolean(query.value.search || query.value.status || query.value.lane),
);
const clearFilters = { q: null, status: null, lane: null, page: null };

function sortBy(key: SortKey) {
  const dir = query.value.sort === key && query.value.direction === 'asc' ? 'desc' : 'asc';
  update({ sort: key, dir, page: null });
}
</script>

<template>
  <PageLayout
    title="My case queue"
    description="Cases assigned to you for review, with the signals that selected them. Select a case to open it."
    :breadcrumbs="[{ label: 'My case queue' }]"
    wide
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
        label="Status"
        :options="statusOptions"
        :model-value="query.status"
        @update:model-value="(value) => update({ status: (value as string) ?? '', page: null })"
      />
      <Select
        label="Refund lane"
        :options="laneOptions"
        :model-value="query.lane"
        @update:model-value="(value) => update({ lane: (value as string) ?? '', page: null })"
      />
      <Button label="Clear filters" :disabled="!filtered" @click="update(clearFilters)" />
    </form>

    <StatePanel v-if="state.status === 'loading'" kind="loading" title="Loading your cases…" />
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error)"
      @retry="reload"
    />
    <StatePanel
      v-else-if="page.total === 0"
      kind="empty"
      :title="filtered ? 'No cases match these filters' : 'No cases are assigned to you'"
      :message="
        filtered
          ? 'Change the search text or clear the filters to see all your cases.'
          : 'Cases appear here when they are assigned to you for review.'
      "
    >
      <Button v-if="filtered" label="Clear filters" @click="update(clearFilters)" />
    </StatePanel>
    <template v-else>
      <CaseTable
        :rows="page.rows"
        caption="Cases assigned to you"
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
