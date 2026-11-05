<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Alert, Badge, Select } from 'frappe-ui';
import {
  getManagementSummary,
  WINDOW_DAYS,
  type DurationStats,
  type WindowDays,
} from '../api/management';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { fixtureRole } from '../session';
import {
  durationText,
  plural,
  ratioText,
  SIGNAL_TEXT,
  utcDate,
  utcDateTime,
} from '../domain/management';

const route = useRoute();
const router = useRouter();

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

// Reload when the window or the fixture "Acting as" officer changes.
const { state, reload } = useLoader(
  (signal) => getManagementSummary(windowDays.value, fetch, signal),
  () => [windowDays.value, fixtureRole.value],
);
const summary = computed(() => (state.value.status === 'ready' ? state.value.data : null));
const denied = computed(
  () => state.value.status === 'error' && state.value.error.kind === 'denied',
);
// Unavailable gets a dashboard-specific title; malformed data keeps "could not be shown safely".
const failurePanel = computed(() => {
  if (state.value.status !== 'error') return null;
  const panel = errorPanel(state.value.error);
  return state.value.error.kind === 'unavailable'
    ? { ...panel, title: 'Management figures are temporarily unavailable' }
    : panel;
});
const nothingToMeasure = computed(
  () =>
    summary.value !== null &&
    summary.value.workload.open_cases === 0 &&
    summary.value.closure_outcomes.closed_in_window === 0,
);

const CASES = { one: 'case', many: 'cases' };
const FINDINGS = { one: 'finding', many: 'findings' };

function durationRows(elapsed: { open_case_age: DurationStats; time_to_close: DurationStats }) {
  return [
    { label: 'Age of open cases', stats: elapsed.open_case_age },
    { label: 'Time to close (closed in window)', stats: elapsed.time_to_close },
  ];
}
</script>

<template>
  <PageLayout
    title="Management dashboard"
    description="Workload and assurance figures for your tenant, measured from case, decision, evidence and audit records. They describe the process, never the performance or conduct of any officer or taxpayer."
    :breadcrumbs="[{ label: 'Management dashboard' }]"
  >
    <div class="mb-4 w-full sm:w-48">
      <Select
        label="Reporting window"
        :options="windowOptions"
        :model-value="String(windowDays)"
        :disabled="denied"
        @update:model-value="setWindow"
      />
    </div>

    <StatePanel
      v-if="state.status === 'loading'"
      kind="loading"
      title="Loading management figures…"
    />
    <StatePanel
      v-else-if="denied && state.status === 'error'"
      kind="denied"
      title="Supervisors only"
      message="The management dashboard is limited to supervisors in this tenant. No figures have been loaded. Ask a supervisor if you need them."
      :reference="state.error.reference"
    />
    <StatePanel v-else-if="failurePanel" v-bind="failurePanel" @retry="reload" />

    <div v-else-if="summary" class="space-y-5">
      <p class="text-ink-gray-5" data-testid="window-line">
        {{ utcDate(summary.window.since) }} to {{ utcDate(summary.window.until) }} ({{
          summary.window.days
        }}
        days). Measured at {{ utcDateTime(summary.generated_at) }}. Open workload and evidence
        requests are as of that time.
      </p>

      <Alert
        v-if="summary.missing_data.length"
        theme="amber"
        title="Some records could not be measured"
      >
        <template #description>
          <ul class="list-disc space-y-1 pl-5">
            <li v-for="gap in summary.missing_data" :key="gap.metric + gap.detail">
              {{ gap.detail }}
            </li>
          </ul>
        </template>
      </Alert>

      <StatePanel
        v-if="nothingToMeasure"
        kind="empty"
        title="No cases to measure"
        message="There are no open cases and no cases closed in this window. Choose a longer window, or check again when cases are open."
      />

      <!-- Workload -->
      <section aria-labelledby="workload-heading" class="vati-panel">
        <h2 id="workload-heading" class="vati-panel-title">Workload</h2>
        <dl class="vati-figures">
          <div>
            <dt>Open cases</dt>
            <dd>{{ summary.workload.open_cases }}</dd>
          </div>
          <div>
            <dt>Not assigned to an officer</dt>
            <dd>{{ summary.workload.unassigned_open_cases }}</dd>
          </div>
          <div>
            <dt>Awaiting evidence</dt>
            <dd>{{ summary.unresolved_evidence.open_cases_awaiting_evidence }}</dd>
          </div>
        </dl>
        <div class="grid gap-4 p-4 pt-0 lg:grid-cols-2">
          <div role="region" aria-label="Open cases by status" tabindex="0" class="vati-table-wrap">
            <table class="vati-table">
              <caption class="sr-only">
                Open cases by status
              </caption>
              <thead>
                <tr>
                  <th scope="col">Status</th>
                  <th scope="col" class="text-right">Cases</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="summary.workload.by_status.length === 0">
                  <td colspan="2" class="text-ink-gray-5">No open cases.</td>
                </tr>
                <tr v-for="row in summary.workload.by_status" :key="row.status">
                  <td><CaseStatusBadge :status="row.status" /></td>
                  <td class="text-right tabular-nums">{{ row.count }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div
            role="region"
            aria-label="Open cases by officer"
            tabindex="0"
            class="vati-table-wrap"
          >
            <table class="vati-table">
              <caption class="sr-only">
                Open cases by officer
              </caption>
              <thead>
                <tr>
                  <th scope="col">Officer</th>
                  <th scope="col" class="text-right">Open cases</th>
                  <th scope="col" class="text-right">Awaiting officer review</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="summary.workload.by_officer.length === 0">
                  <td colspan="3" class="text-ink-gray-5">
                    No open case is assigned to an officer.
                  </td>
                </tr>
                <tr v-for="row in summary.workload.by_officer" :key="row.officer_id">
                  <th scope="row" class="font-normal">{{ row.display_name }}</th>
                  <td class="text-right tabular-nums">{{ row.open_cases }}</td>
                  <td class="text-right tabular-nums">{{ row.awaiting_officer_review }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- Elapsed time -->
      <section aria-labelledby="elapsed-heading" class="vati-panel">
        <h2 id="elapsed-heading" class="vati-panel-title">Elapsed time</h2>
        <div role="region" aria-label="Elapsed time" tabindex="0" class="vati-table-wrap m-4 mt-0">
          <table class="vati-table min-w-[560px]">
            <caption class="sr-only">
              Elapsed time
            </caption>
            <thead>
              <tr>
                <th scope="col">Measure</th>
                <th scope="col" class="text-right">Cases measured</th>
                <th scope="col" class="text-right">Median</th>
                <th scope="col" class="text-right">90th percentile</th>
                <th scope="col" class="text-right">Longest</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in durationRows(summary.elapsed)" :key="row.label">
                <th scope="row" class="font-normal">
                  {{ row.label }}
                  <span v-if="row.stats.excluded" class="block text-sm text-ink-gray-5">
                    {{ plural(row.stats.excluded, 'case', 'cases') }} not measured (no creation
                    time)
                  </span>
                </th>
                <td class="text-right tabular-nums">{{ row.stats.count }}</td>
                <td class="text-right tabular-nums">{{ durationText(row.stats.median_hours) }}</td>
                <td class="text-right tabular-nums">{{ durationText(row.stats.p90_hours) }}</td>
                <td class="text-right tabular-nums">{{ durationText(row.stats.max_hours) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="grid gap-5 lg:grid-cols-2">
        <!-- Evidence -->
        <section aria-labelledby="evidence-heading" class="vati-panel">
          <h2 id="evidence-heading" class="vati-panel-title">Unresolved evidence</h2>
          <dl class="vati-figures">
            <div>
              <dt>Outstanding requests</dt>
              <dd>{{ summary.unresolved_evidence.outstanding_requests }}</dd>
            </div>
            <div>
              <dt>Past their due date</dt>
              <dd>{{ summary.unresolved_evidence.overdue_requests }}</dd>
            </div>
            <div>
              <dt>Cases with outstanding requests</dt>
              <dd>{{ summary.unresolved_evidence.cases_with_outstanding_requests }}</dd>
            </div>
          </dl>
        </section>

        <!-- Legal routes -->
        <section aria-labelledby="routes-heading" class="vati-panel">
          <h2 id="routes-heading" class="vati-panel-title">Legal-route coverage</h2>
          <dl class="vati-figures">
            <div class="sm:col-span-3">
              <dt>Findings with a reviewer-verified legal route</dt>
              <dd>
                {{ ratioText(summary.legal_route_coverage.verified_by_reviewer, FINDINGS).text }}
              </dd>
            </div>
            <div>
              <dt>Not yet verified</dt>
              <dd>{{ summary.legal_route_coverage.unverified }}</dd>
            </div>
            <div>
              <dt>No verified route (policy gap)</dt>
              <dd>{{ summary.legal_route_coverage.no_verified_route }}</dd>
            </div>
          </dl>
          <p class="vati-note">Findings on open cases and on cases closed in this window.</p>
        </section>
      </div>

      <!-- Closures -->
      <section aria-labelledby="closures-heading" class="vati-panel">
        <h2 id="closures-heading" class="vati-panel-title">Closed cases in this window</h2>
        <dl class="vati-figures">
          <div>
            <dt>Closed</dt>
            <dd>{{ summary.closure_outcomes.closed_in_window }}</dd>
          </div>
          <div>
            <dt>Closed with no further action (false positives)</dt>
            <dd>{{ ratioText(summary.closure_outcomes.no_further_action, CASES).text }}</dd>
          </div>
          <div>
            <dt>Closed after correction</dt>
            <dd>{{ ratioText(summary.closure_outcomes.after_correction, CASES).text }}</dd>
          </div>
        </dl>
        <p class="vati-note">
          Rates are out of closed cases with a recorded decision outcome.
          <template v-if="summary.closure_outcomes.without_recorded_outcome">
            {{
              plural(
                summary.closure_outcomes.without_recorded_outcome,
                'closed case has',
                'closed cases have',
              )
            }}
            no recorded outcome and
            {{ summary.closure_outcomes.without_recorded_outcome === 1 ? 'is' : 'are' }}
            left out.
          </template>
          “No further action” means the review found nothing to correct. It measures how cases are
          selected, not the work of any officer or taxpayer.
        </p>
      </section>

      <!-- Assurance -->
      <section aria-labelledby="assurance-heading" class="vati-panel">
        <h2 id="assurance-heading" class="vati-panel-title">Audit ledger assurance</h2>
        <div v-if="!summary.assurance.available" class="p-4 pt-0">
          <StatePanel
            kind="unavailable"
            title="Audit ledger figures are not available"
            :message="summary.assurance.reason"
          />
        </div>
        <template v-else>
          <div class="flex flex-wrap items-center gap-2 px-4 pb-2">
            <span class="text-ink-gray-7">Ledger check:</span>
            <Badge
              v-if="summary.assurance.ledger_intact"
              theme="green"
              label="Intact: every entry verifies"
            />
            <Badge v-else theme="red" label="Failed: entries changed or missing" />
          </div>
          <dl class="vati-figures">
            <div>
              <dt>Entries in window</dt>
              <dd>{{ summary.assurance.entries }}</dd>
            </div>
            <div>
              <dt>Control overrides</dt>
              <dd>{{ summary.assurance.overrides }}</dd>
            </div>
            <div>
              <dt>Refused actions</dt>
              <dd>{{ summary.assurance.refusals }}</dd>
            </div>
            <div>
              <dt>People recorded</dt>
              <dd>{{ summary.assurance.distinct_actors }}</dd>
            </div>
          </dl>
          <div class="px-4 pb-4">
            <h3 class="mb-2 font-medium text-ink-gray-8">Review signals</h3>
            <p v-if="summary.assurance.review_signals.length === 0" class="text-ink-gray-5">
              No review signals in this window.
            </p>
            <ul
              v-else
              class="divide-y divide-outline-gray-1 rounded-surface border border-outline-gray-1"
            >
              <li
                v-for="signal in summary.assurance.review_signals"
                :key="signal.kind"
                class="flex flex-wrap items-baseline justify-between gap-2 px-3 py-2"
              >
                <span>
                  <span class="font-medium text-ink-gray-8">{{
                    SIGNAL_TEXT[signal.kind].label
                  }}</span>
                  <span class="block text-sm text-ink-gray-5">{{
                    SIGNAL_TEXT[signal.kind].help
                  }}</span>
                </span>
                <span class="tabular-nums text-ink-gray-8">{{
                  plural(signal.count, 'signal', 'signals')
                }}</span>
              </li>
            </ul>
            <p v-if="summary.assurance.withheld_about_viewer" class="mt-2 text-ink-gray-7">
              {{
                plural(
                  summary.assurance.withheld_about_viewer,
                  'signal concerns',
                  'signals concern',
                )
              }}
              you and {{ summary.assurance.withheld_about_viewer === 1 ? 'is' : 'are' }} not shown
              here. Another supervisor can review
              {{ summary.assurance.withheld_about_viewer === 1 ? 'it' : 'them' }}.
            </p>
            <p class="mt-2 text-sm text-ink-gray-5">
              Signals are prompts for a supervisor to look. They are not findings about anyone and
              change nothing. Aggregates by kind are in the
              <RouterLink :to="{ name: 'integrity' }" class="underline">integrity review</RouterLink
              >.
            </p>
          </div>
        </template>
      </section>
    </div>
  </PageLayout>
</template>

<style scoped>
.vati-panel {
  @apply rounded-surface border border-outline-gray-1 bg-surface-base;
}
.vati-panel-title {
  @apply px-4 py-3 text-lg font-semibold text-ink-gray-9;
}
.vati-figures {
  @apply grid gap-3 px-4 pb-4 sm:grid-cols-3;
}
.vati-figures dt {
  @apply text-sm text-ink-gray-5;
}
.vati-figures dd {
  @apply mt-0.5 text-xl font-semibold tabular-nums text-ink-gray-9;
}
.vati-note {
  @apply px-4 pb-4 text-sm text-ink-gray-5;
}
.vati-table-wrap {
  @apply overflow-x-auto rounded-surface border border-outline-gray-1 outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-4;
}
.vati-table {
  @apply w-full border-collapse text-left;
}
.vati-table th,
.vati-table td {
  @apply border-b border-outline-gray-1 px-3 py-2 align-top;
}
.vati-table thead th {
  @apply bg-surface-gray-1 text-sm font-medium text-ink-gray-7;
}
.vati-table tbody tr:last-child > * {
  @apply border-b-0;
}
</style>
