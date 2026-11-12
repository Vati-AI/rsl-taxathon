<script setup lang="ts">
/**
 * The case queue (TAX-147, DESIGN_STANDARD.md v2): one toolbar row (search, filters,
 * display options, export, one primary action), a usage line and a dense sortable table
 * with column help, muted empty values and an "Open" row action. The saved view, search,
 * filters, sort and page live in the URL, so they survive a reload and the Back button.
 * Rows and counts come from the shared case list (GET /api/v1/cases) the sidebar counts.
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router';
import { Button, Select } from 'frappe-ui';
import {
  DataTable,
  HeaderMenu,
  TableToolbar,
  UsageLine,
  type DataColumn,
  type SortDirection,
} from '../../../shared/components';
import { CASE_STATUSES, type Case } from '../api/vati';
import AmountText from '../components/AmountText.vue';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import PageLayout from '../components/PageLayout.vue';
import ProcessingIndicator from '../components/ProcessingIndicator.vue';
import SelectionReasons from '../components/SelectionReasons.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useCaseList } from '../composables/useCaseList';
import { laneLabel, statusInfo, taxpayerTypeLabel } from '../domain/cases';
import { formatDate, formatTimestamp } from '../domain/dates';
import {
  type QueueRow,
  OPTIONAL_COLUMNS,
  compareRows,
  queueCsv,
  queueRows,
  type OptionalColumn,
} from '../domain/queueTable';
import { VIEW_INFO, casesInView, inView, parseView } from '../domain/queue';

const PAGE_SIZE = 25;
const route = useRoute();
const router = useRouter();
const { state, reload, refresh } = useCaseList();
onMounted(() => void (state.value.status === 'ready' ? refresh() : reload()));

function text(value: LocationQueryValue | LocationQueryValue[] | undefined) {
  return typeof value === 'string' ? value : '';
}
function update(changes: Record<string, string | number | null>) {
  const next: Record<string, string> = {};
  for (const [key, value] of Object.entries({ ...route.query, ...changes })) {
    if (value !== null && value !== '' && value !== undefined) next[key] = String(value);
  }
  void router.replace({ query: next });
}

// --- URL state --------------------------------------------------------------------
const view = computed(() => parseView(route.query.view));
const info = computed(() => VIEW_INFO[view.value]);
const search = computed({
  get: () => text(route.query.q),
  set: (value: string) => update({ q: value, page: null }),
});
const status = computed(() => text(route.query.status));
const lane = computed(() => text(route.query.lane));
const SORTABLE = [
  'display_id',
  'taxpayer',
  'status',
  'refund_lane',
  'refund_claim',
  'due',
  'updated_at',
];
const sort = computed({
  get: () => {
    const key = text(route.query.sort);
    return SORTABLE.includes(key)
      ? { key, direction: (text(route.query.dir) === 'desc' ? 'desc' : 'asc') as SortDirection }
      : { key: 'display_id', direction: 'asc' as SortDirection };
  },
  set: (value) => update({ sort: value?.key ?? null, dir: value?.direction ?? null, page: null }),
});
const page = computed({
  get: () => Number.parseInt(text(route.query.page), 10) || 1,
  set: (value: number) => update({ page: value > 1 ? value : null }),
});

// --- display options (this browser only) ------------------------------------------
const STORAGE_KEY = 'vati.queue.columns';
function storedColumns(): OptionalColumn[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
    if (Array.isArray(raw)) return raw.filter((k) => k in OPTIONAL_COLUMNS) as OptionalColumn[];
  } catch {
    // storage unavailable: use the defaults
  }
  return (Object.keys(OPTIONAL_COLUMNS) as OptionalColumn[]).filter(
    (k) => OPTIONAL_COLUMNS[k].default,
  );
}
const shownColumns = ref<OptionalColumn[]>(storedColumns());
watch(shownColumns, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // a convenience only
  }
});

const ALL_COLUMNS: (DataColumn & { optional?: OptionalColumn })[] = [
  { key: 'display_id', label: 'Case', sortable: true, nowrap: true },
  { key: 'taxpayer', label: 'Taxpayer', sortable: true },
  { key: 'why', label: 'Why selected', optional: 'why', hideOnMobile: true,
    info: 'The signals that routed the case for review. They are not findings or decisions.' },
  { key: 'status', label: 'Status', sortable: true, nowrap: true,
    info: 'The case status in Investigation, the case management app. It describes processing, not an outcome.' },
  { key: 'refund_lane', label: 'Refund lane', sortable: true, optional: 'refund_lane', hideOnMobile: true },
  { key: 'refund_claim', label: 'Refund claimed', sortable: true, numeric: true },
  { key: 'due', label: 'Evidence due', sortable: true, numeric: true, optional: 'due',
    info: 'Earliest due date of an outstanding evidence request, from the case service.' },
  { key: 'processing', label: 'Processing', optional: 'processing', hideOnMobile: true },
  { key: 'updated_at', label: 'Last updated', sortable: true, numeric: true, optional: 'updated_at', hideOnMobile: true },
]; // prettier-ignore
const columns = computed(() =>
  ALL_COLUMNS.filter((c) => !c.optional || shownColumns.value.includes(c.optional)),
);

// --- rows ---------------------------------------------------------------------------
const cases = computed(() => (state.value.status === 'ready' ? state.value.data : []));
const unknownView = computed(() => cases.value.some((c) => inView(c, view.value) === null));
const filtered = computed(() => Boolean(search.value || status.value || lane.value));
const rows = computed<QueueRow[]>(() => {
  const needle = search.value.trim().toLocaleLowerCase();
  const inScope = casesInView(cases.value, view.value).filter(
    (c) =>
      (!needle ||
        c.display_id.toLocaleLowerCase().includes(needle) ||
        c.taxpayer.display_name.toLocaleLowerCase().includes(needle)) &&
      (!status.value || c.status === status.value) &&
      (!lane.value || (lane.value === 'none' ? c.refund_lane === null : c.refund_lane === lane.value)),
  );
  return queueRows(inScope).sort((a, b) => compareRows(a, b, sort.value));
}); // prettier-ignore
const byId = computed(() => new Map(cases.value.map((c) => [c.case_id, c] as [string, Case])));
const shown = computed(() => {
  const pages = Math.max(1, Math.ceil(rows.value.length / PAGE_SIZE));
  const current = Math.min(Math.max(1, page.value), pages);
  return rows.value.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE).length;
});
const extras = computed(() => {
  const inRows = rows.value.map((r) => byId.value.get(r.case_id)!);
  const out: string[] = [];
  const overdue = inRows.filter((c) => inView(c, 'overdue') === true).length;
  const approval = inRows.filter((c) => inView(c, 'awaiting_approval') === true).length;
  if (overdue) out.push(`${overdue} overdue`);
  if (approval) out.push(`${approval} awaiting approval`);
  return out;
});

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
const clearFilters = { q: null, status: null, lane: null, page: null };

function open(row: QueueRow) {
  void router.push({ name: 'case', params: { caseId: row.case_id } });
}

function exportCsv() {
  const csv = queueCsv(rows.value, columns.value);
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `vati-cases-${view.value.replace('_', '-')}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}
</script>

<template>
  <PageLayout
    :title="view === 'all' ? 'My case queue' : `My case queue · ${info.label}`"
    :description="`${info.description} Select a case to open it.`"
    :breadcrumbs="[{ label: 'My case queue' }]"
    wide
  >
    <StatePanel v-if="state.status === 'loading'" kind="loading" title="Loading your cases…" />
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error)"
      @retry="reload"
    />
    <template v-else>
      <TableToolbar
        v-model:query="search"
        search-label="Search cases"
        placeholder="Case reference or taxpayer name"
      >
        <template #filters>
          <div class="w-44">
            <Select
              aria-label="Status"
              :options="statusOptions"
              :model-value="status"
              @update:model-value="(v) => update({ status: (v as string) ?? '', page: null })"
            />
          </div>
          <div class="w-44">
            <Select
              aria-label="Refund lane"
              :options="laneOptions"
              :model-value="lane"
              @update:model-value="(v) => update({ lane: (v as string) ?? '', page: null })"
            />
          </div>
          <Button
            v-if="filtered"
            label="Clear filters"
            variant="ghost"
            @click="update(clearFilters)"
          />
        </template>
        <template #display>
          <HeaderMenu label="Display options" icon="lucide-columns-3" show-label>
            <fieldset>
              <legend class="mb-2 font-semibold">Columns</legend>
              <label
                v-for="(col, key) in OPTIONAL_COLUMNS"
                :key="key"
                class="flex items-center gap-2 py-1 text-sm"
              >
                <input v-model="shownColumns" type="checkbox" :value="key" />
                {{ col.label }}
              </label>
            </fieldset>
            <p class="mt-2 text-xs text-vati-muted">Saved in this browser only.</p>
          </HeaderMenu>
        </template>
        <template #export>
          <Button
            label="Export CSV"
            icon-left="lucide-download"
            :disabled="!rows.length"
            @click="exportCsv"
          />
        </template>
        <template #primary>
          <Button
            variant="solid"
            theme="blue"
            label="Open first case"
            icon-left="lucide-arrow-right"
            :disabled="!rows.length"
            @click="open(rows[0])"
          />
        </template>
      </TableToolbar>

      <UsageLine :shown="shown" :total="rows.length" noun="cases" :extras="extras" class="mb-2" />

      <StatePanel
        v-if="unknownView && !rows.length"
        kind="empty"
        title="This view is not available"
        message="The case service did not report the data this view needs, so no count is shown rather than a guess."
      />
      <StatePanel
        v-else-if="!rows.length"
        kind="empty"
        :title="
          filtered
            ? 'No cases match these filters'
            : view === 'all'
              ? 'No cases are assigned to you'
              : `No cases in ${info.label.toLowerCase()}`
        "
        :message="
          filtered
            ? 'Change the search text or clear the filters to see all cases in this view.'
            : 'Cases appear here as their state changes.'
        "
      >
        <Button v-if="filtered" label="Clear filters" @click="update(clearFilters)" />
      </StatePanel>
      <DataTable
        v-else
        v-model:sort="sort"
        v-model:page="page"
        :label="`${info.label}: assigned cases`"
        :columns="columns"
        :rows="rows"
        row-key="display_id"
        row-action="Open"
        :page-size="PAGE_SIZE"
        @row-action="open"
      >
        <template #cell-display_id="{ row }">
          <RouterLink
            :to="{ name: 'case', params: { caseId: row.case_id } }"
            class="font-medium text-vati-accent underline-offset-2 hover:underline"
          >
            {{ row.display_id }}
          </RouterLink>
        </template>
        <template #cell-taxpayer="{ row }">
          <span class="block text-vati-text">{{ row.taxpayer }}</span>
          <span class="text-xs text-vati-muted">{{
            taxpayerTypeLabel(byId.get(row.case_id)!.taxpayer.taxpayer_type)
          }}</span>
        </template>
        <template #cell-why="{ row }">
          <SelectionReasons :reasons="byId.get(row.case_id)!.selection_reasons" compact />
        </template>
        <template #cell-status="{ row }">
          <CaseStatusBadge :status="byId.get(row.case_id)!.status" />
        </template>
        <template #cell-refund_lane="{ row }">
          <template v-if="byId.get(row.case_id)!.refund_lane">
            {{ laneLabel(byId.get(row.case_id)!.refund_lane) }}
          </template>
          <template v-else>
            <span aria-hidden="true" class="text-vati-muted">—</span>
            <span class="vati-sr-only">Not set</span>
          </template>
        </template>
        <template #cell-refund_claim="{ row }">
          <AmountText
            v-if="byId.get(row.case_id)!.refund_claim"
            :value="byId.get(row.case_id)!.refund_claim!.amount"
            :currency="byId.get(row.case_id)!.refund_claim!.currency"
          />
          <template v-else>
            <span aria-hidden="true" class="text-vati-muted">—</span>
            <span class="vati-sr-only">Not provided</span>
          </template>
        </template>
        <template #cell-due="{ row }">
          <template v-if="row.due">
            <span :class="row.overdue ? 'font-medium text-vati-danger-text' : ''">
              {{ formatDate(row.due) }}
            </span>
            <span v-if="row.overdue" class="block text-xs text-vati-danger-text">Overdue</span>
          </template>
          <template v-else>
            <span aria-hidden="true" class="text-vati-muted">—</span>
            <span class="vati-sr-only">No evidence request outstanding</span>
          </template>
        </template>
        <template #cell-processing="{ row }">
          <span class="text-sm"><ProcessingIndicator :item="byId.get(row.case_id)!" /></span>
        </template>
        <template #cell-updated_at="{ row }">
          {{ formatTimestamp(row.updated_at) }}
        </template>
      </DataTable>
    </template>
  </PageLayout>
</template>
