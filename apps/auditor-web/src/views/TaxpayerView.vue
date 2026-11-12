<script setup lang="ts">
/**
 * Taxpayer profile (TAX-147): key facts (type, masked TIN, registration, sector) and the
 * taxpayer's open cases as a toolbar-plus-table, with an "Open" row action. Read-only
 * context from Vati records; identifiers are masked by the server.
 */
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { Button } from 'frappe-ui';
import {
  DataTable,
  KeyFacts,
  SkeletonBlock,
  TableToolbar,
  UsageLine,
  type DataColumn,
  type KeyFact,
} from '../../../shared/components';
import { getTaxpayer } from '../api/vati';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { statusInfo, taxpayerTypeLabel } from '../domain/cases';
import { formatDate } from '../domain/dates';

const props = defineProps<{ taxpayerId: string }>();
const router = useRouter();
const { state, reload } = useLoader(
  (signal) => getTaxpayer(props.taxpayerId, fetch, signal),
  () => props.taxpayerId,
);
const profile = computed(() => (state.value.status === 'ready' ? state.value.data : null));
const facts = computed<KeyFact[]>(() => {
  const p = profile.value;
  if (!p) return [];
  return [
    { label: 'Type', value: taxpayerTypeLabel(p.taxpayer_type) },
    { label: 'Taxpayer ID number (masked)', value: p.tin_masked, numeric: true },
    { label: 'Registered', value: formatDate(p.registered_on), numeric: true },
    { label: 'Sector', value: p.sector },
    { label: 'Open cases you can view', value: String(p.open_cases.length), numeric: true },
  ];
});

const search = ref('');
const columns: DataColumn[] = [
  { key: 'display_id', label: 'Case', sortable: true, nowrap: true },
  {
    key: 'status',
    label: 'Status',
    sortable: true,
    info: 'The case status in Investigation, the case management app.',
  },
];
const rows = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase();
  return (profile.value?.open_cases ?? [])
    .filter((c) => !needle || c.display_id.toLocaleLowerCase().includes(needle))
    .map((c) => ({ case_id: c.case_id, display_id: c.display_id, status: statusInfo(c.status).label, raw: c.status }));
}); // prettier-ignore
const sort = ref<{ key: string; direction: 'asc' | 'desc' } | null>({
  key: 'display_id',
  direction: 'asc',
});
function open(row: { case_id: string }) {
  void router.push({ name: 'case', params: { caseId: row.case_id } });
}
</script>

<template>
  <PageLayout
    :title="profile ? profile.display_name : 'Taxpayer'"
    description="Read-only taxpayer context from Vati records. Identifiers are masked."
    :breadcrumbs="[
      { label: 'My case queue', route: { name: 'queue' } },
      { label: profile ? profile.display_name : 'Taxpayer' },
    ]"
  >
    <SkeletonBlock
      v-if="state.status === 'loading'"
      label="Loading taxpayer"
      :lines="4"
      variant="rows"
    />
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error, 'taxpayer')"
      @retry="reload"
    >
      <Button label="Back to my case queue" :route="{ name: 'queue' }" />
    </StatePanel>
    <div v-else-if="profile" class="space-y-6">
      <KeyFacts :facts="facts" label="Taxpayer details" />
      <section aria-labelledby="open-cases-heading">
        <h2 id="open-cases-heading" class="mb-3 text-lg font-semibold text-vati-text">
          Open cases you can view
        </h2>
        <StatePanel
          v-if="!profile.open_cases.length"
          kind="empty"
          title="No open cases"
          message="This taxpayer has no open case you are allowed to see."
        />
        <template v-else>
          <TableToolbar
            v-model:query="search"
            search-label="Search this taxpayer's cases"
            placeholder="Case reference"
          />
          <UsageLine
            :shown="rows.length"
            :total="profile.open_cases.length"
            noun="open cases"
            class="mb-2"
          />
          <DataTable
            v-model:sort="sort"
            label="Open cases you can view"
            :columns="columns"
            :rows="rows"
            row-key="display_id"
            row-action="Open"
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
            <template #cell-status="{ row }">
              <CaseStatusBadge :status="row.raw" />
            </template>
            <template #empty>
              <p class="py-4 text-vati-muted">No case matches this search.</p>
            </template>
          </DataTable>
        </template>
      </section>
    </div>
  </PageLayout>
</template>
