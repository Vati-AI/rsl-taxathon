<script setup lang="ts">
/**
 * Showcase of the shared Vati Workspace kit (TAX-145, apps/shared/components). Every
 * component in its states: normal, hover and focus (interactive, see the keyboard
 * screenshots), disabled, loading, empty and error. Synthetic sample data only; nothing
 * here is fixture case data or a real taxpayer.
 */
import { computed, ref } from 'vue';
import { Button } from 'frappe-ui';
import { useRoute } from 'vue-router';
import {
  AppSidebar,
  CommandPalette,
  ConfirmDialog,
  DataTable,
  EmptyState,
  InlineError,
  KeyFacts,
  NoticePanel,
  notify,
  PageHeader,
  RoutedTabs,
  SideDrawer,
  SkeletonBlock,
  StatusChip,
  TableToolbar,
  TONES,
  UsageLine,
  WorkspaceHeader,
  type CommandItem,
  type DataColumn,
  type NavSection,
  type RoutedTab,
} from '../../../shared/components';

const route = useRoute();

// --- table ----------------------------------------------------------------------------
const STATUSES = ['Under review', 'Awaiting evidence', 'Ready for decision', 'Closed'];
const rows = Array.from({ length: 31 }, (_, i) => {
  const n = i + 1;
  return {
    reference: `DEMO-${String(200 + n)}`,
    taxpayer:
      n === 3
        ? 'Synthetic Highland Imports, Logistics and Cold-Chain Warehousing Cooperative Society'
        : `Synthetic Trader ${n}`,
    status: STATUSES[n % STATUSES.length],
    claimed: n % 7 === 0 ? null : (1_250_000 - n * 17_311.5).toFixed(2),
    due: n % 5 === 0 ? null : `2026-12-${String((n % 28) + 1).padStart(2, '0')}`,
  };
});
const columns: DataColumn[] = [
  { key: 'reference', label: 'Case', sortable: true, nowrap: true },
  { key: 'taxpayer', label: 'Taxpayer', sortable: true },
  {
    key: 'status',
    label: 'Status',
    sortable: true,
    info: 'Where the case is in review. A status is a step, not a finding.',
  },
  { key: 'claimed', label: 'Claimed (LSL)', sortable: true, numeric: true, hideOnMobile: true },
  { key: 'due', label: 'Decision due', sortable: true, numeric: true, hideOnMobile: true },
];
const query = ref('');
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  return q ? rows.filter((r) => `${r.reference} ${r.taxpayer}`.toLowerCase().includes(q)) : rows;
});
const sort = ref<{ key: string; direction: 'asc' | 'desc' } | null>({
  key: 'reference',
  direction: 'asc',
});
const page = ref(1);
const opened = ref<string | null>(null);

function money(value: unknown): string {
  const [whole, cents = '00'] = String(value).split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${cents}`;
}

function statusTone(status: unknown) {
  return status === 'Awaiting evidence'
    ? 'warning'
    : status === 'Ready for decision'
      ? 'info'
      : status === 'Closed'
        ? 'neutral'
        : 'info';
}

// --- navigation -----------------------------------------------------------------------
const sections: NavSection[] = [
  {
    label: 'Cases',
    items: [
      { label: 'My cases', to: { name: 'showcase' }, icon: 'lucide-inbox', match: 'showcase', count: 12 },
      { label: 'Awaiting evidence', to: { name: 'showcase', query: { view: 'evidence' } }, icon: 'lucide-clock', match: 'none', count: 4 },
      { label: 'Overdue', to: { name: 'showcase', query: { view: 'overdue' } }, icon: 'lucide-flag', match: 'none', count: 2 },
    ],
  },
  {
    label: 'Reports',
    items: [
      { label: 'Management', to: { name: 'showcase', query: { view: 'management' } }, icon: 'lucide-gauge', match: 'none' },
    ],
  },
]; // prettier-ignore
const collapsed = ref(true);
const tabs: RoutedTab[] = [
  { label: 'Summary', to: { name: 'showcase', query: { tab: 'summary' } }, match: 'summary' },
  { label: 'Evidence', to: { name: 'showcase', query: { tab: 'evidence' } }, match: 'evidence', count: 7 },
  { label: 'Relationships', to: { name: 'showcase', query: { tab: 'relationships' } }, match: 'relationships' },
  { label: 'Findings', to: { name: 'showcase', query: { tab: 'findings' } }, match: 'findings', count: 3 },
  { label: 'Review', to: { name: 'showcase', query: { tab: 'review' } }, match: 'review' },
]; // prettier-ignore
const currentTab = computed(() =>
  typeof route.query.tab === 'string' ? route.query.tab : 'summary',
);

// --- overlays -------------------------------------------------------------------------
const drawerOpen = ref(false);
const confirmOpen = ref(false);
const dangerOpen = ref(false);
const confirming = ref(false);
const paletteOpen = ref(false);
const chosen = ref<string | null>(null);
const commands: CommandItem[] = [
  { id: 'c1', label: 'DEMO-201', group: 'Cases', hint: 'Synthetic Trader 1', keywords: ['400000201'] },
  { id: 'c2', label: 'DEMO-202', group: 'Cases', hint: 'Synthetic Trader 2', keywords: ['400000202'] },
  { id: 't1', label: 'Synthetic Trader 1', group: 'Taxpayers', hint: 'TIN 400000201' },
  { id: 'p1', label: 'My cases', group: 'Pages' },
  { id: 'p2', label: 'Management', group: 'Pages' },
]; // prettier-ignore

function confirm() {
  confirming.value = true;
  window.setTimeout(() => {
    confirming.value = false;
    confirmOpen.value = false;
    notify('success', 'Evidence request sent');
  }, 600);
}
const retrying = ref(false);
function retry() {
  retrying.value = true;
  window.setTimeout(() => (retrying.value = false), 800);
}
</script>

<template>
  <section aria-labelledby="kit-title" class="showcase-section" data-kit-showcase>
    <h2 id="kit-title" class="showcase-heading">Vati Workspace kit</h2>
    <p class="mb-4 max-w-3xl text-ink-gray-5">
      Shared components from <code>apps/shared/components</code>, used by both apps. Hover and
      keyboard focus are shown by using them; each block below adds the disabled, loading, empty and
      error states.
    </p>

    <h3 class="kit-sub">Workspace header</h3>
    <div class="kit-frame">
      <WorkspaceHeader
        apps-label="Example workspace apps"
        :apps="[
          { label: 'Cases', href: '#kit-title', current: true },
          { label: 'Explore', href: '#kit-title' },
          { label: 'Reports', href: '#kit-title' },
          { label: 'Admin', href: '#kit-title' },
        ]"
        fixture
        @search="paletteOpen = true"
      >
        <template #actions>
          <Button aria-label="Ask Vati" icon="lucide-sparkles" />
        </template>
        <template #account>
          <Button aria-label="Account" icon="lucide-user" />
        </template>
      </WorkspaceHeader>
    </div>

    <h3 class="kit-sub">App rail and sidebar</h3>
    <div class="flex flex-wrap gap-4">
      <div class="kit-frame h-80">
        <AppSidebar
          label="Example navigation"
          :sections="sections"
          current="showcase"
          :back="{ label: 'Back to cases', to: { name: 'showcase' }, icon: '', match: 'none' }"
        />
      </div>
      <div class="kit-frame h-80">
        <AppSidebar
          v-model:collapsed="collapsed"
          label="Example navigation, collapsed"
          :sections="sections"
          current="showcase"
        />
      </div>
      <div class="kit-frame h-80">
        <AppSidebar label="Example dark navigation" :sections="sections" current="showcase" dark />
      </div>
    </div>

    <h3 class="kit-sub">Page header, status chips and notices</h3>
    <div class="kit-frame p-5">
      <PageHeader
        heading-id="kit-page-title"
        :level="3"
        title="DEMO-203 · Synthetic Highland Imports"
        description="Check the input VAT claimed for September 2026 against the documents supplied."
      >
        <template #breadcrumbs>
          <p class="mb-1 text-sm text-vati-muted">Cases / DEMO-203</p>
        </template>
        <template #status>
          <StatusChip label="Awaiting evidence" tone="warning" size="lg" />
        </template>
        <template #actions>
          <Button label="Export" icon-left="lucide-download" />
          <Button label="Assign" disabled />
          <Button
            variant="solid"
            theme="blue"
            label="Request evidence"
            @click="drawerOpen = true"
          />
        </template>
      </PageHeader>
      <div class="flex flex-col gap-3">
        <NoticePanel
          title="Two invoices could not be matched"
          message="Ask the taxpayer for the supplier invoices before deciding."
          :recommendations="[
            'Request the September invoices from Synthetic Supplies.',
            'Check that the supplier is VAT-registered.',
          ]"
          dismissable
        />
        <NoticePanel
          tone="info"
          title="Read-only for your role"
          message="A supervisor approves decisions on this case."
        />
        <NoticePanel
          tone="success"
          title="Evidence received"
          message="The taxpayer answered every requested item."
        />
        <NoticePanel
          tone="danger"
          title="The decision could not be recorded"
          message="Nothing was saved. Try again; your notes are kept."
        />
      </div>
    </div>
    <div class="mt-3 flex flex-wrap items-center gap-2">
      <StatusChip v-for="tone in TONES" :key="tone" :tone="tone" :label="`${tone} status`" />
      <StatusChip tone="neutral" label="Not available" size="lg" />
    </div>

    <h3 class="kit-sub">Key facts</h3>
    <KeyFacts
      label="Example key facts"
      :facts="[
        { label: 'Claimed', value: 'LSL 1,232,688.50', numeric: true },
        { label: 'Undisputed', value: 'LSL 980,000.00', numeric: true },
        { label: 'Decision due', value: '18 Dec 2026', numeric: true },
        { label: 'Lane', value: 'Standard' },
        { label: 'Officer', value: null },
      ]"
    />

    <h3 class="kit-sub">Tabs bound to routes</h3>
    <RoutedTabs label="Example case sections" :tabs="tabs" :current="currentTab" />

    <h3 class="kit-sub">Table toolbar, usage line and data table</h3>
    <TableToolbar
      v-model:query="query"
      search-label="Search example cases"
      placeholder="Case or taxpayer"
    >
      <template #filters>
        <Button label="Filters" icon-left="lucide-list-filter" />
      </template>
      <template #display>
        <Button label="Display" icon-left="lucide-columns-3" />
      </template>
      <template #export>
        <Button label="Export" icon-left="lucide-download" disabled />
      </template>
      <template #primary>
        <Button variant="solid" theme="blue" label="New case" icon-left="lucide-plus" />
      </template>
    </TableToolbar>
    <UsageLine
      class="mb-2"
      :shown="Math.min(25, filtered.length)"
      :total="filtered.length"
      noun="cases"
      :extras="['4 awaiting evidence']"
    />
    <DataTable
      v-model:sort="sort"
      v-model:page="page"
      label="Example cases"
      :columns="columns"
      :rows="filtered"
      row-key="reference"
      row-action="Open"
      @row-action="(row) => (opened = String(row.reference))"
    >
      <template #cell-claimed="{ value }">
        <template v-if="value">{{ money(value) }}</template>
        <template v-else>
          <span aria-hidden="true" class="text-vati-muted">—</span>
          <span class="vati-sr-only">Not available</span>
        </template>
      </template>
      <template #cell-status="{ value }">
        <StatusChip :label="String(value)" :tone="statusTone(value)" />
      </template>
      <template #empty>
        <EmptyState
          title="No cases match"
          message="Change the search or clear the filters."
          icon="lucide-search-x"
          :heading-level="3"
        />
      </template>
    </DataTable>
    <p v-if="opened" role="status" class="mt-2 text-vati-muted">Opened {{ opened }} (example).</p>

    <div class="mt-4 grid gap-4 lg:grid-cols-3">
      <div>
        <p class="kit-label">Loading</p>
        <DataTable
          label="Loading example"
          :columns="columns"
          :rows="[]"
          row-key="reference"
          loading
        />
      </div>
      <div>
        <p class="kit-label">Empty</p>
        <DataTable label="Empty example" :columns="columns" :rows="[]" row-key="reference">
          <template #empty>
            <EmptyState
              title="No cases yet"
              message="Cases you are assigned appear here."
              :heading-level="3"
            >
              <template #actions>
                <Button label="View all cases" />
              </template>
            </EmptyState>
          </template>
        </DataTable>
      </div>
      <div>
        <p class="kit-label">Error</p>
        <DataTable
          label="Error example"
          :columns="columns"
          :rows="[]"
          row-key="reference"
          error="Cases could not be loaded"
          error-message="The case service did not answer."
          @retry="retry"
        />
      </div>
    </div>

    <h3 class="kit-sub">Drawer, dialogs and toast</h3>
    <div class="flex flex-wrap gap-2">
      <Button label="Open drawer" @click="drawerOpen = true" />
      <Button label="Confirm an action" @click="confirmOpen = true" />
      <Button label="Confirm a danger action" theme="red" @click="dangerOpen = true" />
      <Button label="Show a toast" @click="notify('success', 'Evidence request sent')" />
      <Button label="Open example search" icon-left="lucide-search" @click="paletteOpen = true" />
    </div>
    <p v-if="chosen" role="status" class="mt-2 text-vati-muted">Chose {{ chosen }} (example).</p>
    <SideDrawer
      v-model:open="drawerOpen"
      title="Request evidence"
      description="Ask the taxpayer only for what is missing."
    >
      <p class="text-vati-text">
        Drawer content scrolls inside the drawer; the page behind stays where it was.
      </p>
      <template #footer>
        <Button label="Cancel" @click="drawerOpen = false" />
        <Button variant="solid" theme="blue" label="Review request" @click="drawerOpen = false" />
      </template>
    </SideDrawer>
    <ConfirmDialog
      v-model:open="confirmOpen"
      title="Send this evidence request?"
      message="The taxpayer sees the two items listed. You can withdraw the request later."
      confirm-label="Send request"
      :loading="confirming"
      @confirm="confirm"
    />
    <ConfirmDialog
      v-model:open="dangerOpen"
      tone="danger"
      title="Withdraw this evidence request?"
      message="The taxpayer will no longer be able to answer it. This cannot be undone."
      confirm-label="Withdraw request"
      @confirm="dangerOpen = false"
    />
    <!-- The shell owns Ctrl K; this example opens from its button only. -->
    <CommandPalette
      v-model:open="paletteOpen"
      :shortcut="false"
      :items="commands"
      @select="(i) => (chosen = i.label)"
    />

    <h3 class="kit-sub">Feedback states</h3>
    <div class="grid gap-4 lg:grid-cols-2">
      <InlineError
        title="Evidence could not be loaded"
        message="The document service did not answer."
        reference="9b0c1f2e-0000-4000-8000-000000000145"
        :retrying="retrying"
        @retry="retry"
      />
      <InlineError title="You do not have access" message="Ask a supervisor." :retryable="false" />
      <SkeletonBlock label="Loading example text" />
      <EmptyState
        title="No evidence yet"
        message="Ask the taxpayer for what is missing, or upload a document you hold."
        :heading-level="3"
      >
        <template #actions>
          <Button variant="solid" theme="blue" label="Request evidence" />
          <Button label="Upload" />
        </template>
      </EmptyState>
    </div>
  </section>
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
.kit-sub {
  margin: 24px 0 8px;
  font-size: 16px;
  font-weight: 600;
  color: var(--vati-text);
}
.kit-label {
  margin-bottom: 4px;
  font-size: 13px;
  color: var(--vati-text-muted);
}
.kit-frame {
  overflow: hidden;
  border: 1px solid var(--vati-border);
  border-radius: var(--vati-radius-surface);
  background: var(--vati-canvas);
}
</style>
