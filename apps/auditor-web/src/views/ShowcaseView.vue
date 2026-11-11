<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Dialog,
  Select,
  TabList,
  TabPanel,
  TabTrigger,
  Tabs,
  TextInput,
  Textarea,
  toast,
} from 'frappe-ui';
import { CASE_STATUSES, type Case, type CaseStatus, type RefundLane } from '../api/vati';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import CaseTable from '../components/CaseTable.vue';
import KitShowcase from '../components/KitShowcase.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { queuePage, type SortDirection, type SortKey } from '../domain/cases';

// Showcase-only synthetic rows: long names, large amounts, missing values and a
// failed service exercise layout limits. They are not part of the fixture data.
function sample(
  n: number,
  name: string,
  status: CaseStatus,
  amount: string | null,
  lane: RefundLane | null,
  reasons: string[],
  failed = false,
): Case {
  const id = `90000000-0000-4000-8000-00000000010${n}`;
  return {
    case_id: id,
    tenant_id: '10000000-0000-4000-8000-000000000001',
    display_id: `DEMO-10${n}`,
    taxpayer_id: id,
    taxpayer: { taxpayer_id: id, display_name: name, taxpayer_type: 'company' },
    status,
    version: 1,
    refund_claim: amount === null ? null : { amount, currency: 'LSL' },
    refund_lane: lane,
    assigned_officer_id: null,
    selection_reasons: reasons.map((summary, i) => ({
      code: `demo_reason_${i}`,
      summary,
      source: 'jube',
    })),
    service_links: failed
      ? [
          {
            service: 'docling',
            external_type: 'extraction',
            external_id: null,
            sync: {
              state: 'failed',
              last_attempt_at: '2026-11-03T09:45:00Z',
              error_code: 'upstream_timeout',
            },
          },
        ]
      : [],
    created_at: '2026-11-03T09:00:00Z',
    updated_at: '2026-11-03T09:00:00Z',
    synthetic: true,
  };
}
const sampleRows = [
  sample(
    1,
    'Synthetic Maloti Mountains Agricultural Cooperative and Rural Transport Services (Proprietary) Limited',
    'AWAITING_HUMAN',
    '1250000.00',
    'fast',
    ['Refund claim is larger than previous claims.'],
  ),
  sample(2, 'Synthetic Quthing Border Retail', 'INVESTIGATING', '98500.50', 'targeted', [
    'Management fees paid to a company sharing a director.',
    'Losses reported for several periods.',
  ]),
  sample(3, 'Synthetic Senqu Valley Consulting', 'FAILED_PROCESSING', null, null, [], true),
  sample(
    4,
    'Synthetic Highlands Import & Export Holdings',
    'AWAITING_EVIDENCE',
    '7.25',
    'standard',
    ['Imported service invoices lack withholding evidence.'],
  ),
];
const tableSort = ref<SortKey>('display_id');
const tableDirection = ref<SortDirection>('asc');
const tableSearch = ref('');
const tableRows = computed(
  () =>
    queuePage(sampleRows, {
      search: tableSearch.value,
      status: '',
      lane: '',
      sort: tableSort.value,
      direction: tableDirection.value,
      page: 1,
      pageSize: 10,
    }).rows,
);
function sortTable(key: SortKey) {
  tableDirection.value = tableSort.value === key && tableDirection.value === 'asc' ? 'desc' : 'asc';
  tableSort.value = key;
}

// Form with validation that preserves entered values and gives actionable errors.
const form = reactive({ reference: '', lane: '', note: '', confirmed: false });
const errors = reactive({ reference: '', lane: '', confirmed: '' });
const saving = ref(false);
function validate() {
  errors.reference = /^SYN-\d{3}$/.test(form.reference.trim())
    ? ''
    : 'Enter a case reference in the format SYN-001.';
  errors.lane = form.lane ? '' : 'Choose a refund lane.';
  errors.confirmed = form.confirmed ? '' : 'Confirm that this example uses synthetic data only.';
  return !errors.reference && !errors.lane && !errors.confirmed;
}
async function submit() {
  if (!validate()) return;
  saving.value = true;
  await new Promise((resolve) => setTimeout(resolve, 800));
  saving.value = false;
  toast.success('Example form is valid', { description: 'Showcase only — nothing was saved.' });
}

const tab = ref('summary');
const dialogOpen = ref(false);
const loadingDemo = ref(false);
function showLoading() {
  loadingDemo.value = true;
  setTimeout(() => (loadingDemo.value = false), 1500);
}
</script>

<template>
  <PageLayout
    title="Component showcase"
    description="The shared Vati Workspace components and visual contract. Every example uses synthetic data and performs no real action."
    :breadcrumbs="[{ label: 'Component showcase' }]"
  >
    <div class="space-y-6">
      <KitShowcase />
      <section aria-labelledby="sc-buttons" class="showcase-section">
        <h2 id="sc-buttons" class="showcase-heading">Buttons</h2>
        <p class="showcase-note">
          One solid blue primary action per page. Hover and keyboard focus (Tab) show their states
          on each button.
        </p>
        <div class="flex flex-wrap items-center gap-3">
          <Button variant="solid" theme="blue" label="Primary action" />
          <Button label="Secondary" />
          <Button variant="outline" label="Outline" />
          <Button variant="ghost" label="Ghost" />
          <Button icon="lucide-refresh-cw" label="Refresh list" />
          <Button
            variant="solid"
            theme="blue"
            label="Saving"
            :loading="loadingDemo"
            @click="showLoading"
          >
            {{ loadingDemo ? 'Saving…' : 'Show loading' }}
          </Button>
          <Button label="Disabled" disabled aria-describedby="sc-disabled-reason" />
          <span id="sc-disabled-reason" class="text-sm text-ink-gray-5">
            Disabled actions state why: “Not available in fixture mode”.
          </span>
        </div>
      </section>

      <section aria-labelledby="sc-form" class="showcase-section">
        <h2 id="sc-form" class="showcase-heading">Form fields and validation</h2>
        <form class="grid max-w-2xl gap-4" novalidate @submit.prevent="submit">
          <TextInput
            v-model="form.reference"
            label="Case reference"
            description="Format SYN-001."
            required
            :error="errors.reference"
          />
          <Select
            v-model="form.lane"
            label="Refund lane"
            required
            placeholder="Choose a refund lane"
            :error="errors.lane"
            :options="[
              { label: 'Fast', value: 'fast' },
              { label: 'Standard', value: 'standard' },
              { label: 'Targeted', value: 'targeted' },
            ]"
          />
          <Textarea v-model="form.note" label="Officer note (optional)" :rows="3" />
          <Checkbox
            v-model="form.confirmed"
            label="I understand this example uses synthetic data only"
            :error="errors.confirmed"
          />
          <TextInput label="Read-only field" model-value="Locked by fixture mode" disabled />
          <div class="flex flex-wrap gap-3">
            <Button
              type="submit"
              variant="solid"
              theme="blue"
              label="Validate example"
              :loading="saving"
            />
            <Button
              label="Reset"
              @click="
                Object.assign(form, { reference: '', lane: '', note: '', confirmed: false });
                Object.assign(errors, { reference: '', lane: '', confirmed: '' });
              "
            />
          </div>
        </form>
      </section>

      <section aria-labelledby="sc-tabs" class="showcase-section">
        <h2 id="sc-tabs" class="showcase-heading">Tabs</h2>
        <Tabs v-model="tab">
          <TabList>
            <TabTrigger value="summary" label="Summary" />
            <TabTrigger value="evidence" label="Evidence" />
            <TabTrigger value="history" label="History" disabled />
          </TabList>
          <TabPanel value="summary" class="pt-3 text-ink-gray-6">Summary panel content.</TabPanel>
          <TabPanel value="evidence" class="pt-3 text-ink-gray-6">Evidence panel content.</TabPanel>
        </Tabs>
      </section>

      <section aria-labelledby="sc-table" class="showcase-section">
        <h2 id="sc-table" class="showcase-heading">Table and filter controls</h2>
        <p class="showcase-note">
          Showcase sample rows (synthetic) with a long name, large amounts and missing values.
          Numeric columns are right-aligned; wide tables scroll inside their own region.
        </p>
        <div class="mb-3 max-w-sm">
          <TextInput v-model="tableSearch" type="search" label="Search sample rows" />
        </div>
        <CaseTable
          v-if="tableRows.length"
          :rows="tableRows"
          caption="Showcase sample cases"
          :sort="tableSort"
          :direction="tableDirection"
          @sort="sortTable"
        />
        <StatePanel
          v-else
          kind="empty"
          title="No sample rows match"
          message="Clear the search text."
        />
      </section>

      <section aria-labelledby="sc-badges" class="showcase-section">
        <h2 id="sc-badges" class="showcase-heading">Badges</h2>
        <p class="showcase-note">
          Case status uses text plus colour. Status describes processing, never a verdict.
        </p>
        <div class="flex flex-wrap gap-2">
          <CaseStatusBadge v-for="status in CASE_STATUSES" :key="status" :status="status" />
          <Badge theme="blue" label="Fixture" />
        </div>
      </section>

      <section aria-labelledby="sc-dialogs" class="showcase-section">
        <h2 id="sc-dialogs" class="showcase-heading">Dialogs</h2>
        <p class="showcase-note">
          Consequential actions show the exact proposed action and require explicit officer
          confirmation.
        </p>
        <Button label="Open confirmation dialog" @click="dialogOpen = true" />
        <Dialog
          v-model:open="dialogOpen"
          title="Confirm the proposed action"
          :actions="[
            {
              label: 'Confirm (showcase only)',
              variant: 'solid',
              theme: 'blue',
              onClick: ({ close }) => {
                close();
                toast.info('Showcase only', { description: 'No decision was recorded.' });
              },
            },
            { label: 'Cancel', onClick: ({ close }) => close() },
          ]"
        >
          <p class="text-ink-gray-6">
            Request a bank statement from Synthetic Border Retail for SYN-002. In the product, an
            authorised officer confirms this and the decision is recorded with its rationale.
          </p>
        </Dialog>
      </section>

      <section aria-labelledby="sc-notify" class="showcase-section">
        <h2 id="sc-notify" class="showcase-heading">Notifications</h2>
        <div class="grid gap-3">
          <Alert theme="blue" title="Fixture mode" description="Data shown is synthetic." />
          <Alert theme="green" title="Evidence received" description="Example success message." />
          <Alert
            theme="amber"
            title="Evidence missing"
            description="Missing information is reported separately from discrepancies."
          />
          <Alert
            theme="red"
            title="Service unavailable"
            description="The fixture service could not be reached. Try again."
          />
        </div>
        <div class="mt-3 flex flex-wrap gap-3">
          <Button
            label="Show success toast"
            @click="toast.success('Saved', { description: 'Showcase only.' })"
          />
          <Button
            label="Show error toast"
            @click="toast.error('Could not load', { description: 'Showcase only.' })"
          />
        </div>
      </section>

      <section aria-labelledby="sc-states" class="showcase-section">
        <h2 id="sc-states" class="showcase-heading">Page states</h2>
        <div class="grid gap-3 lg:grid-cols-2">
          <StatePanel kind="loading" title="Loading cases…" />
          <StatePanel
            kind="empty"
            title="No cases match these filters"
            message="Clear the filters."
          />
          <StatePanel
            kind="error"
            title="Case data is temporarily unavailable"
            message="The fixture service could not be reached."
            retryable
          />
          <StatePanel
            kind="denied"
            title="You do not have access"
            message="Your role or tenant does not permit viewing this record."
          />
        </div>
      </section>
    </div>
  </PageLayout>
</template>

<style scoped>
.showcase-section {
  border: 1px solid var(--vati-border);
  border-radius: var(--vati-radius-surface);
  background: var(--vati-surface);
  padding: var(--vati-space-4);
}
.showcase-heading {
  font-size: 16px;
  font-weight: 600;
  color: var(--vati-text);
  margin-bottom: var(--vati-space-2);
}
.showcase-note {
  color: var(--vati-text-muted);
  margin-bottom: var(--vati-space-3);
}
</style>
