<script setup lang="ts">
/**
 * Reports · Integrity (TAX-149): the TAX-077 restricted aggregates (ADR-036, small groups
 * combined) as a tab of the Reports page, which owns the reporting window.
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { Alert, Badge } from 'frappe-ui';
import {
  getIntegrityReview,
  INTEGRITY_WINDOW_DAYS,
  type CombinedRow,
  type IntegrityRow,
  type IntegrityWindowDays,
} from '../../api/integrity';
import StatePanel from '../../components/StatePanel.vue';
import { errorPanel } from '../../components/errorPanel';
import { useLoader } from '../../composables/useLoader';
import { fixtureRole } from '../../session';
import { combinedText, INTEGRITY_SIGNAL_TEXT } from '../../domain/integrity';
import { plural, utcDate, utcDateTime } from '../../domain/management';

defineOptions({ inheritAttrs: false });
const route = useRoute();

const windowDays = computed<IntegrityWindowDays>(() => {
  const requested = Number(route.query.window);
  return (INTEGRITY_WINDOW_DAYS as readonly number[]).includes(requested)
    ? (requested as IntegrityWindowDays)
    : 30;
});

// Reload when the window or the fixture "Acting as" officer changes.
const { state, reload } = useLoader(
  (signal) => getIntegrityReview(windowDays.value, fetch, signal),
  () => [windowDays.value, fixtureRole.value],
);
const review = computed(() => (state.value.status === 'ready' ? state.value.data : null));
const denied = computed(
  () => state.value.status === 'error' && state.value.error.kind === 'denied',
);
const failurePanel = computed(() => {
  if (state.value.status !== 'error') return null;
  const panel = errorPanel(state.value.error);
  return state.value.error.kind === 'unavailable'
    ? { ...panel, title: 'The integrity review is temporarily unavailable' }
    : panel;
});

type Row = (IntegrityRow | CombinedRow) & { groups?: number };
function tableRows(shown: IntegrityRow[], combined: CombinedRow | null): Row[] {
  return combined ? [...shown, { ...combined, groups: combined.groups_combined }] : shown;
}
/** Bar width as a share of the largest row; decorative only (the table carries the figures). */
function barWidth(rows: Row[], row: Row) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return `${Math.round((row.count / max) * 100)}%`;
}
</script>

<template>
  <div class="space-y-4">
    <StatePanel
      v-if="state.status === 'loading'"
      kind="loading"
      title="Loading the integrity review…"
    />
    <StatePanel
      v-else-if="denied && state.status === 'error'"
      kind="denied"
      title="Supervisors only"
      message="The integrity review is limited to supervisors in this tenant. No figures have been loaded."
      :reference="state.error.reference"
    />
    <StatePanel v-else-if="failurePanel" v-bind="failurePanel" @retry="reload" />

    <div v-else-if="review" class="space-y-4">
      <p class="text-ink-gray-5" data-testid="window-line">
        {{ utcDate(review.window.since) }} to {{ utcDate(review.window.until) }} ({{
          review.window.days
        }}
        days). Measured at {{ utcDateTime(review.generated_at) }}.
      </p>

      <Alert theme="blue" title="Aggregates only" data-testid="privacy-note">
        <template #description>{{ review.privacy_note }}</template>
      </Alert>

      <Alert
        v-if="review.missing_data.length"
        theme="amber"
        title="Some records could not be measured"
      >
        <template #description>
          <ul class="list-disc space-y-1 pl-5">
            <li v-for="gap in review.missing_data" :key="gap.metric + gap.detail">
              {{ gap.detail }}
            </li>
          </ul>
        </template>
      </Alert>

      <!-- Policy exposure: about the rules, never about a taxpayer's compliance. -->
      <div class="grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-2">
        <section aria-labelledby="exposure-heading" class="vati-panel">
          <h2 id="exposure-heading" class="vati-panel-title">Policy exposure</h2>
          <div v-if="review.policy_exposure.status === 'unavailable'" class="p-4 pt-0">
            <StatePanel
              kind="unavailable"
              title="Policy-gap figures are not available"
              message="Reviewed policy-gap findings are not recorded in this environment yet."
            />
          </div>
          <template v-else>
            <p class="vati-note">{{ review.policy_exposure.note }}</p>
            <dl class="vati-figures">
              <div>
                <dt>Confirmed policy gaps</dt>
                <dd>{{ review.policy_exposure.confirmed_gaps ?? '—' }}</dd>
              </div>
              <div>
                <dt>Waiting for reviewer confirmation</dt>
                <dd>{{ review.policy_exposure.pending_review }}</dd>
              </div>
            </dl>
            <p
              v-if="review.policy_exposure.status === 'insufficient_volume'"
              class="vati-note"
              data-testid="exposure-insufficient"
            >
              Too few taxpayers are covered in this window to show any breakdown without risking
              identification. Choose a longer window.
            </p>
            <div v-else class="px-4 pb-4">
              <div
                role="region"
                aria-label="Confirmed policy gaps by policy area"
                tabindex="0"
                class="vati-table-wrap"
              >
                <table class="vati-table">
                  <caption class="sr-only">
                    Confirmed policy gaps by policy area
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Policy area</th>
                      <th scope="col" class="hidden w-2/5 sm:table-cell">
                        <span class="sr-only">Share</span>
                      </th>
                      <th scope="col" class="text-right">Gaps</th>
                      <th scope="col" class="text-right">Cases</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in tableRows(
                        review.policy_exposure.areas,
                        review.policy_exposure.combined,
                      )"
                      :key="row.key"
                    >
                      <th scope="row" class="font-normal">
                        {{ row.label }}
                        <span v-if="row.groups" class="block text-sm text-ink-gray-5">{{
                          combinedText(row.groups)
                        }}</span>
                      </th>
                      <td aria-hidden="true" class="hidden sm:table-cell">
                        <div class="vati-bar">
                          <div
                            class="vati-bar-fill"
                            :style="{
                              width: barWidth(
                                tableRows(
                                  review.policy_exposure.areas,
                                  review.policy_exposure.combined,
                                ),
                                row,
                              ),
                            }"
                          />
                        </div>
                      </td>
                      <td class="text-right tabular-nums">{{ row.count }}</td>
                      <td class="text-right tabular-nums">{{ row.cases }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
        </section>

        <!-- Override patterns: how controls are used, not a measure of any officer. -->
        <section aria-labelledby="overrides-heading" class="vati-panel">
          <h2 id="overrides-heading" class="vati-panel-title">Control overrides</h2>
          <div v-if="review.override_patterns.status === 'unavailable'" class="p-4 pt-0">
            <StatePanel
              kind="unavailable"
              title="Override figures are not available"
              message="The audit ledger could not be read."
            />
          </div>
          <template v-else>
            <p class="vati-note">{{ review.override_patterns.note }}</p>
            <dl class="vati-figures">
              <div>
                <dt>Overrides recorded</dt>
                <dd>{{ review.override_patterns.total_overrides ?? '—' }}</dd>
              </div>
            </dl>
            <p
              v-if="review.override_patterns.status === 'insufficient_volume'"
              class="vati-note"
              data-testid="overrides-insufficient"
            >
              Too few people are involved in this window to show any breakdown without risking
              identification. Choose a longer window.
            </p>
            <div v-else class="px-4 pb-4">
              <div
                role="region"
                aria-label="Overrides by control"
                tabindex="0"
                class="vati-table-wrap"
              >
                <table class="vati-table">
                  <caption class="sr-only">
                    Overrides by control
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Control</th>
                      <th scope="col" class="hidden w-2/5 sm:table-cell">
                        <span class="sr-only">Share</span>
                      </th>
                      <th scope="col" class="text-right">Overrides</th>
                      <th scope="col" class="text-right">Cases</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in tableRows(
                        review.override_patterns.by_control,
                        review.override_patterns.combined,
                      )"
                      :key="row.key"
                    >
                      <th scope="row" class="font-normal">
                        {{ row.label }}
                        <span v-if="row.groups" class="block text-sm text-ink-gray-5">{{
                          combinedText(row.groups)
                        }}</span>
                      </th>
                      <td aria-hidden="true" class="hidden sm:table-cell">
                        <div class="vati-bar">
                          <div
                            class="vati-bar-fill"
                            :style="{
                              width: barWidth(
                                tableRows(
                                  review.override_patterns.by_control,
                                  review.override_patterns.combined,
                                ),
                                row,
                              ),
                            }"
                          />
                        </div>
                      </td>
                      <td class="text-right tabular-nums">{{ row.count }}</td>
                      <td class="text-right tabular-nums">{{ row.cases }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
        </section>

        <!-- Review signals: prompts to look, never findings about anyone. -->
        <section aria-labelledby="signals-heading" class="vati-panel">
          <h2 id="signals-heading" class="vati-panel-title">Review signals</h2>
          <div v-if="review.review_signals.status === 'unavailable'" class="p-4 pt-0">
            <StatePanel
              kind="unavailable"
              title="Review signals are not available"
              message="The audit ledger could not be read."
            />
          </div>
          <div v-else class="px-4 pb-4">
            <p class="mb-3 text-sm text-ink-gray-5">{{ review.review_signals.note }}</p>
            <p v-if="review.review_signals.by_kind.length === 0" class="text-ink-gray-5">
              No review signals in this window.
            </p>
            <ul
              v-else
              class="divide-y divide-outline-gray-1 rounded-surface border border-outline-gray-1"
            >
              <li
                v-for="signal in review.review_signals.by_kind"
                :key="signal.kind"
                class="flex flex-wrap items-baseline justify-between gap-2 px-3 py-2"
              >
                <span>
                  <span class="font-medium text-ink-gray-8">{{
                    INTEGRITY_SIGNAL_TEXT[signal.kind].label
                  }}</span>
                  <span class="block text-sm text-ink-gray-5">{{
                    INTEGRITY_SIGNAL_TEXT[signal.kind].help
                  }}</span>
                </span>
                <span class="tabular-nums text-ink-gray-8">{{
                  plural(signal.signals, 'signal', 'signals')
                }}</span>
              </li>
            </ul>
            <p
              v-if="review.review_signals.withheld_about_viewer"
              class="mt-2 text-ink-gray-7"
              data-testid="withheld"
            >
              {{
                plural(
                  review.review_signals.withheld_about_viewer,
                  'signal concerns',
                  'signals concern',
                )
              }}
              you and {{ review.review_signals.withheld_about_viewer === 1 ? 'is' : 'are' }} not
              counted here. Another supervisor can review
              {{ review.review_signals.withheld_about_viewer === 1 ? 'it' : 'them' }}.
            </p>
            <p class="mt-2 text-sm text-ink-gray-5">
              Counts show how many patterns were found, not who they concern. A signal is a prompt
              to look at the records; it is not a finding about anyone and changes nothing.
            </p>
          </div>
        </section>

        <!-- Ledger check -->
        <section aria-labelledby="ledger-heading" class="vati-panel">
          <h2 id="ledger-heading" class="vati-panel-title">Audit ledger check</h2>
          <div class="flex flex-wrap items-center gap-2 px-4 pb-4">
            <Badge
              v-if="!review.ledger.available"
              theme="amber"
              label="Not available in this environment"
            />
            <Badge
              v-else-if="review.ledger.intact"
              theme="green"
              label="Intact: every entry verifies"
            />
            <Badge v-else theme="red" label="Failed: entries changed or missing" />
          </div>
        </section>
      </div>
    </div>
  </div>
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
  @apply border-b border-outline-gray-1 px-3 py-2 align-middle;
}
.vati-table thead th {
  @apply bg-surface-gray-1 text-sm font-medium text-ink-gray-7;
}
.vati-table tbody tr:last-child > * {
  @apply border-b-0;
}
.vati-bar {
  @apply h-2 w-full rounded-full bg-surface-gray-2;
}
.vati-bar-fill {
  @apply h-2 rounded-full bg-surface-gray-7;
}
</style>
