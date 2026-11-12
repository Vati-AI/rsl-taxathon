<script setup lang="ts">
/**
 * Officer home (TAX-147): the work that needs action now, as action items: Overdue,
 * Awaiting approval, Awaiting evidence and Failed processing. Each shows its count and
 * the first few cases, and opens the matching queue view. Counts come from the same
 * case list as the queue and the sidebar; an item whose data the server did not send
 * says so instead of showing a number.
 */
import { computed, onMounted } from 'vue';
import { Button } from 'frappe-ui';
import type { Case } from '../api/vati';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useCaseList } from '../composables/useCaseList';
import { processingState, statusCounts } from '../domain/cases';
import { formatDate } from '../domain/dates';
import { VIEW_INFO, casesInView, viewCounts, type QueueView } from '../domain/queue';

const { state, reload, refresh } = useCaseList();
onMounted(() => void (state.value.status === 'ready' ? refresh() : reload()));
const cases = computed(() => (state.value.status === 'ready' ? state.value.data : []));
const counts = computed(() => viewCounts(cases.value));
const PREVIEW = 3;

interface ActionItem {
  key: string;
  title: string;
  description: string;
  tone: 'danger' | 'warning' | 'info';
  icon: string;
  count: number | undefined;
  cases: Case[];
  to: { name: string; query?: Record<string, string> };
  detail: (c: Case) => string;
}

const items = computed<ActionItem[]>(() => {
  const view = (v: QueueView, tone: ActionItem['tone'], detail: ActionItem['detail']) => ({
    key: v,
    title: VIEW_INFO[v].label,
    description: VIEW_INFO[v].description,
    tone,
    icon: VIEW_INFO[v].icon,
    count: counts.value[v],
    cases: casesInView(cases.value, v),
    to: { name: 'queue', query: { view: v } },
    detail,
  });
  const failed = cases.value.filter((c) => processingState(c).kind === 'failed');
  return [
    view('overdue', 'danger', (c) => `Evidence was due ${formatDate(c.attention?.evidence_due_date ?? null) ?? ''}`),
    view('awaiting_approval', 'info', () => 'A proposed decision waits for a second officer'),
    view('awaiting_evidence', 'warning', (c) =>
      c.attention?.evidence_due_date ? `Due ${formatDate(c.attention.evidence_due_date)}` : 'Waiting for the taxpayer'),
    {
      key: 'failed',
      title: 'Processing failed',
      description: 'A connected service could not finish. These cases have no findings until processing succeeds.',
      tone: 'danger',
      icon: 'lucide-circle-alert',
      count: failed.length,
      cases: failed,
      to: { name: 'queue', query: { status: 'FAILED_PROCESSING' } },
      detail: (c: Case) => processingState(c).text,
    },
  ];
}); // prettier-ignore
const byStatus = computed(() => statusCounts(cases.value));
const TONES = {
  danger: 'text-vati-danger-text',
  warning: 'text-vati-warning-text',
  info: 'text-vati-info-text',
} as const;
</script>

<template>
  <PageLayout
    title="Overview"
    description="What needs your action now. Statuses come from Investigation, the case management app; they describe processing, not outcomes."
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
    <div v-else class="space-y-6">
      <section aria-labelledby="actions-heading">
        <h2 id="actions-heading" class="mb-3 text-lg font-semibold text-vati-text">
          Needs your action
        </h2>
        <ul class="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2 xl:grid-cols-4">
          <li
            v-for="item in items"
            :key="item.key"
            data-kit="action-item"
            class="flex flex-col rounded-surface border border-vati-border bg-vati-surface"
          >
            <div class="flex items-start justify-between gap-3 px-4 pt-4">
              <h3 :id="`action-${item.key}`" class="flex items-center gap-2 font-semibold">
                <span aria-hidden="true" :class="[item.icon, 'size-4', TONES[item.tone]]" />
                {{ item.title }}
              </h3>
              <span
                v-if="item.count !== undefined"
                class="vati-tabular text-2xl font-semibold text-vati-text"
                :aria-label="`${item.count} ${item.count === 1 ? 'case' : 'cases'}`"
              >
                {{ item.count }}
              </span>
              <span v-else class="text-sm text-vati-muted">Not reported</span>
            </div>
            <p class="px-4 pt-1 text-sm text-vati-muted">{{ item.description }}</p>
            <ul
              v-if="item.cases.length"
              class="mt-3 divide-y divide-vati-border border-t border-vati-border"
            >
              <li v-for="c in item.cases.slice(0, PREVIEW)" :key="c.case_id">
                <RouterLink
                  :to="{ name: 'case', params: { caseId: c.case_id } }"
                  class="block px-4 py-2 hover:bg-vati-surface-subtle"
                >
                  <span class="font-medium text-vati-accent">{{ c.display_id }}</span>
                  <span class="text-vati-text"> · {{ c.taxpayer.display_name }}</span>
                  <span class="block text-xs text-vati-muted">{{ item.detail(c) }}</span>
                </RouterLink>
              </li>
            </ul>
            <p v-else-if="item.count === 0" class="px-4 pb-1 pt-3 text-sm text-vati-muted">
              Nothing here right now.
            </p>
            <RouterLink
              v-if="item.count"
              :to="item.to"
              class="mt-auto border-t border-vati-border px-4 py-2 text-sm font-medium text-vati-accent hover:underline"
            >
              View all {{ item.count }}
              <span class="vati-sr-only">: {{ item.title.toLowerCase() }}</span>
            </RouterLink>
          </li>
        </ul>
      </section>

      <section aria-labelledby="status-heading">
        <div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="status-heading" class="text-lg font-semibold text-vati-text">
            Assigned cases by status
          </h2>
          <p class="text-sm text-vati-muted">{{ cases.length }} assigned to you</p>
        </div>
        <ul class="flex flex-wrap gap-2">
          <li v-for="entry in byStatus" :key="entry.status">
            <RouterLink
              :to="{ name: 'queue', query: { status: entry.status } }"
              class="inline-flex items-center gap-2 rounded-control border border-vati-border bg-vati-surface px-3 py-1.5 hover:bg-vati-surface-subtle"
            >
              <CaseStatusBadge :status="entry.status" />
              <span class="vati-tabular text-vati-text">
                {{ entry.count }} {{ entry.count === 1 ? 'case' : 'cases' }}
                <span class="vati-sr-only">— view in queue</span>
              </span>
            </RouterLink>
          </li>
        </ul>
      </section>
    </div>
  </PageLayout>
</template>
