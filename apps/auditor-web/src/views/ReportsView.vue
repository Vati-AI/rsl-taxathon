<script setup lang="ts">
/**
 * Reports (TAX-149, DESIGN_STANDARD.md v2): the management dashboard and the integrity
 * review as one page with routed tabs (Workload, Timeliness, Evidence, Outcomes, Ledger
 * assurance, Integrity), each its own URL, under one reporting window (?window=7|30|90).
 * The management summary loads once here for its five tabs; the Integrity tab loads the
 * integrity review itself. Both APIs refuse anyone but a supervisor, so a direct URL
 * shows the denied state with no figures.
 */
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Select } from 'frappe-ui';
import { RoutedTabs, type RoutedTab } from '../../../shared/components';
import { getManagementSummary, WINDOW_DAYS, type WindowDays } from '../api/management';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { fixtureRole } from '../session';

const route = useRoute();
const router = useRouter();
const current = computed(() => (typeof route.name === 'string' ? route.name : null));
const onIntegrity = computed(() => current.value === 'integrity');

const windowDays = computed<WindowDays>(() => {
  const requested = Number(route.query.window);
  return (WINDOW_DAYS as readonly number[]).includes(requested) ? (requested as WindowDays) : 30;
});
const windowOptions = WINDOW_DAYS.map((days) => ({
  label: `Last ${days} days`,
  value: String(days),
}));
function setWindow(value: unknown) {
  const days = Number(value);
  void router.replace({ query: days === 30 ? {} : { window: String(days) } });
}
const keep = computed(() => (windowDays.value === 30 ? {} : { window: String(windowDays.value) }));
const tabs = computed<RoutedTab[]>(() =>
  [
    ['Workload', 'management'],
    ['Timeliness', 'management-timeliness'],
    ['Evidence', 'management-evidence'],
    ['Outcomes', 'management-outcomes'],
    ['Ledger assurance', 'management-assurance'],
    ['Integrity', 'integrity'],
  ].map(([label, name]) => ({ label, to: { name, query: keep.value }, match: name })),
);
const tabLabel = computed(
  () => tabs.value.find((t) => t.match === current.value)?.label ?? 'Reports',
);

// The management summary, for the five management tabs (not fetched on Integrity).
const { state, reload } = useLoader(
  async (signal) =>
    onIntegrity.value ? null : getManagementSummary(windowDays.value, fetch, signal),
  () => [windowDays.value, fixtureRole.value, onIntegrity.value],
);
const denied = computed(
  () => state.value.status === 'error' && state.value.error.kind === 'denied',
);
const failurePanel = computed(() => {
  if (state.value.status !== 'error') return null;
  const panel = errorPanel(state.value.error);
  return state.value.error.kind === 'unavailable'
    ? { ...panel, title: 'Management figures are temporarily unavailable' }
    : panel;
});
</script>

<template>
  <PageLayout
    :title="onIntegrity ? 'Integrity review' : 'Management dashboard'"
    :description="
      onIntegrity
        ? 'Restricted aggregates for supervisors: where the rules do not clearly cover observed patterns, and how controls are used. Counts describe rules and processes, never the conduct of any taxpayer or officer.'
        : 'Workload and assurance figures for your tenant, measured from case, decision, evidence and audit records. They describe the process, never the performance or conduct of any officer or taxpayer.'
    "
    :breadcrumbs="[{ label: 'Reports' }, { label: tabLabel }]"
    wide
  >
    <div class="mb-3 flex flex-wrap items-end justify-between gap-3">
      <RoutedTabs
        :tabs="tabs"
        label="Report sections"
        :current="current"
        class="!mb-0 min-w-0 flex-1"
      />
      <div class="w-full sm:w-44">
        <Select
          aria-label="Reporting window"
          :options="windowOptions"
          :model-value="String(windowDays)"
          :disabled="denied"
          @update:model-value="setWindow"
        />
      </div>
    </div>
    <section :aria-label="tabLabel" data-kit="report-panel">
      <RouterView v-if="onIntegrity" />
      <template v-else>
        <StatePanel
          v-if="state.status === 'loading'"
          kind="loading"
          title="Loading management figures…"
        />
        <StatePanel
          v-else-if="denied && state.status === 'error'"
          kind="denied"
          title="Supervisors only"
          message="Reports are limited to supervisors in this tenant. No figures have been loaded. Ask a supervisor if you need them."
          :reference="state.error.reference"
        />
        <StatePanel v-else-if="failurePanel" v-bind="failurePanel" @retry="reload" />
        <RouterView v-else-if="state.status === 'ready' && state.data" v-slot="{ Component }">
          <component :is="Component" :summary="state.data" />
        </RouterView>
      </template>
    </section>
  </PageLayout>
</template>
