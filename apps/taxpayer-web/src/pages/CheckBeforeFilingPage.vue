<script setup lang="ts">
import { Alert, Badge, Button, LoadingText } from 'frappe-ui';
import { computed, ref } from 'vue';
import PortalHeader from '../components/PortalHeader.vue';
import { checkBeforeFiling, PortalError } from '../lib/api';
import { formatAmount, formatPeriod } from '../lib/format';
import { sessionState } from '../lib/session';
import type { PrefilingCheck } from '../lib/types';

// TAX-129 (ADR-051): check a VAT period against what RSL already holds (e-Tax return,
// Lekuka invoices, ASYCUDA entries) before filing in RSL's Online Refunds Application.
// Guidance only: nothing is typed, stored, filed, blocked or scored.

function lastMonth(today = new Date()): string {
  const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 1, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}
function thisMonth(today = new Date()): string {
  return `${today.getUTCFullYear()}-${String(today.getUTCMonth() + 1).padStart(2, '0')}`;
}

const period = ref(lastMonth());
const loading = ref(false);
const error = ref<PortalError | null>(null);
const result = ref<PrefilingCheck | null>(null);

const tenantId = computed(() =>
  sessionState.value.status === 'ready'
    ? sessionState.value.session.taxpayers?.[0]?.tenant_id
    : undefined,
);
const sessionUrl = computed(() =>
  sessionState.value.status === 'ready'
    ? (sessionState.value.session.rsl_online_refunds_url ?? null)
    : null,
);
const fileUrl = computed(() => result.value?.file_with.url ?? sessionUrl.value);

const STATUS = {
  no_issues_found: { label: 'No issues found', theme: 'green' },
  check_these: { label: 'Worth checking', theme: 'amber' },
  incomplete: { label: 'Not fully checked', theme: 'gray' },
} as const;

const FIGURES: { key: keyof PrefilingCheck['figures']; label: string }[] = [
  { key: 'output_vat', label: 'Output VAT in your e-Tax return' },
  { key: 'input_vat_claimed', label: 'Input VAT in your e-Tax return' },
  { key: 'refund_claimed', label: 'Refund in your e-Tax return' },
  { key: 'lekuka_input_vat', label: 'VAT on your purchase invoices in Lekuka' },
  { key: 'asycuda_import_vat', label: 'Import VAT on your customs entries in ASYCUDA' },
];

async function run() {
  loading.value = true;
  error.value = null;
  result.value = null;
  try {
    result.value = await checkBeforeFiling(period.value, tenantId.value);
  } catch (err) {
    error.value = err instanceof PortalError ? err : new PortalError('unavailable', String(err));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <section aria-labelledby="page-title">
    <PortalHeader
      title="Check before you file"
      description="See what RSL already holds for a VAT period and anything worth checking before you file a refund claim. This check is guidance only."
    />

    <form
      class="mb-6 flex flex-wrap items-end gap-4 rounded-lg border border-outline-gray-2 p-4"
      aria-label="Choose a VAT period"
      @submit.prevent="run"
    >
      <div class="flex flex-col gap-1">
        <label for="check-period" class="text-sm font-medium text-ink-gray-8">VAT period</label>
        <input
          id="check-period"
          v-model="period"
          type="month"
          required
          :max="thisMonth()"
          class="rounded border border-outline-gray-3 px-3 py-2 text-base text-ink-gray-9 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
        />
      </div>
      <Button type="submit" variant="solid" :loading="loading" label="Check this period" />
    </form>

    <div v-if="loading" role="status" class="py-8">
      <LoadingText text="Reading what RSL holds for this period…" />
    </div>

    <Alert
      v-else-if="error"
      :theme="error.kind === 'invalid' ? 'amber' : 'red'"
      :title="error.kind === 'invalid' ? 'Check the period' : 'The check could not run'"
      :description="
        error.code === 'invalid_period'
          ? 'Choose a VAT period that has started, as month and year.'
          : error.message
      "
    >
      <template v-if="error.kind !== 'invalid'" #actions>
        <Button label="Try again" @click="run" />
      </template>
    </Alert>

    <div v-else-if="result" class="flex flex-col gap-6" aria-live="polite">
      <div class="flex flex-wrap items-center gap-3">
        <h2 class="text-lg font-semibold text-ink-gray-9">{{ formatPeriod(result.period) }}</h2>
        <Badge
          size="md"
          :theme="STATUS[result.status].theme"
          :label="STATUS[result.status].label"
        />
      </div>

      <section aria-labelledby="held-heading">
        <h3 id="held-heading" class="mb-2 font-medium text-ink-gray-9">What RSL holds</h3>
        <dl class="divide-y divide-outline-gray-1 rounded-lg border border-outline-gray-2">
          <div
            v-for="figure in FIGURES"
            :key="figure.key"
            class="flex flex-wrap justify-between gap-x-6 gap-y-1 p-3"
          >
            <dt class="text-ink-gray-7">{{ figure.label }}</dt>
            <dd class="font-medium tabular-nums text-ink-gray-9">
              {{
                result.figures[figure.key] === null
                  ? 'Not available'
                  : formatAmount(result.figures[figure.key], result.currency)
              }}
            </dd>
          </div>
        </dl>
        <p class="mt-2 text-sm text-ink-gray-6">
          {{ result.counts.lekuka_invoices }} purchase invoice(s) in Lekuka and
          {{ result.counts.asycuda_entries }} customs entr{{
            result.counts.asycuda_entries === 1 ? 'y' : 'ies'
          }}
          in ASYCUDA for this period. You did not need to type any of this.
        </p>
      </section>

      <section aria-labelledby="guidance-heading">
        <h3 id="guidance-heading" class="mb-2 font-medium text-ink-gray-9">Worth checking</h3>
        <p v-if="result.prompts.length === 0" class="text-ink-gray-7">
          Nothing to check: your e-Tax return and the records RSL holds agree for this period.
        </p>
        <ul v-else class="list-disc space-y-2 pl-6 text-ink-gray-8">
          <li v-for="prompt in result.prompts" :key="prompt.code + prompt.message">
            {{ prompt.message }}
          </li>
        </ul>
      </section>

      <Alert theme="blue" title="File your claim in RSL's Online Refunds Application">
        <template #description>
          <p>{{ result.note }}</p>
        </template>
        <template v-if="fileUrl" #actions>
          <a
            :href="fileUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="text-base font-medium text-ink-blue-8 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            Go to the Online Refunds Application to file<span class="sr-only">
              (opens in a new tab)</span
            >
          </a>
        </template>
      </Alert>
    </div>
  </section>
</template>
