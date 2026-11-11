<script setup lang="ts">
import { Alert, Badge, Breadcrumbs, Button, LoadingText } from 'frappe-ui';
import { ref, watch } from 'vue';
import PortalHeader from '../components/PortalHeader.vue';
import { fetchSubmission, PortalError } from '../lib/api';
import {
  formatAmount,
  formatDate,
  formatPeriod,
  rslSourceNote,
  rslStatusText,
  statusDisplay,
  stepStateLabel,
} from '../lib/format';
import type { Submission } from '../lib/types';

const props = defineProps<{ id: string }>();

const item = ref<Submission | null>(null);
const loading = ref(true);
const error = ref<PortalError | null>(null);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    item.value = await fetchSubmission(props.id);
  } catch (err) {
    item.value = null;
    error.value = err instanceof PortalError ? err : new PortalError('unavailable', String(err));
  } finally {
    loading.value = false;
  }
}

watch(() => props.id, load, { immediate: true });

const stepIcon = {
  complete: 'lucide-circle-check',
  current: 'lucide-circle-dot',
  upcoming: 'lucide-circle',
};
</script>

<template>
  <section>
    <div v-if="loading" role="status" class="py-8">
      <LoadingText text="Loading case…" />
    </div>

    <template v-else-if="error">
      <PortalHeader :title="error.kind === 'not_found' ? 'Case not found' : 'Case status'" />
      <Alert
        :theme="error.kind === 'not_found' ? 'gray' : error.kind === 'denied' ? 'amber' : 'red'"
        :title="error.kind === 'denied' ? 'Access denied' : error.message"
        :description="
          error.kind === 'not_found'
            ? 'Check the link, or open the case from your list.'
            : error.kind === 'denied'
              ? error.message
              : undefined
        "
      >
        <template #actions>
          <Button label="Back to my cases" :route="{ name: 'submissions' }" />
          <Button v-if="error.kind === 'unavailable'" label="Try again" @click="load" />
        </template>
      </Alert>
    </template>

    <template v-else-if="item">
      <PortalHeader :title="`${item.tax_type} · ${formatPeriod(item.period)}`">
        <template #breadcrumbs>
          <Breadcrumbs
            class="mb-2"
            :items="[
              { label: 'My cases', route: { name: 'submissions' } },
              { label: item.reference, route: { name: 'submission', params: { id: item.id } } },
            ]"
          />
        </template>
        <template #actions>
          <Badge
            size="lg"
            :theme="statusDisplay(item.status).theme"
            :label="statusDisplay(item.status).label"
          />
        </template>
      </PortalHeader>

      <Alert class="mb-6" theme="gray" title="What happens next" :description="item.next_step" />

      <section
        v-if="item.rsl"
        aria-labelledby="rsl-title"
        class="mb-6 rounded-lg border border-outline-gray-2 p-5"
      >
        <h2 id="rsl-title" class="mb-1 text-lg font-semibold text-ink-gray-9">
          In {{ item.rsl.system }}
        </h2>
        <p class="mb-4 text-sm text-ink-gray-6">
          You filed this claim with RSL. RSL keeps its status; to change the claim, use RSL's
          system.
        </p>
        <dl class="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-x-4 gap-y-3">
          <dt class="text-ink-gray-6">RSL claim reference</dt>
          <dd class="break-words text-ink-gray-9">{{ item.rsl.claim_ref }}</dd>
          <dt class="text-ink-gray-6">RSL status</dt>
          <dd class="text-ink-gray-9">
            <span class="font-medium">{{ rslStatusText(item.rsl) }}</span>
            <span class="block text-sm text-ink-gray-6">{{ rslSourceNote(item.rsl) }}</span>
          </dd>
        </dl>
        <p class="mt-4">
          <a
            v-if="item.rsl.dashboard_url"
            :href="item.rsl.dashboard_url"
            target="_blank"
            rel="noopener noreferrer"
            class="font-medium text-ink-blue-8 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            See this claim in {{ item.rsl.system
            }}<span class="sr-only"> (opens in a new tab)</span>
          </a>
          <span v-else class="text-sm text-ink-gray-6">
            Sign in to {{ item.rsl.system }} from RSL's website to see this claim there.
          </span>
        </p>
      </section>

      <div class="grid gap-6 lg:grid-cols-2">
        <section
          aria-labelledby="details-title"
          class="rounded-lg border border-outline-gray-2 p-5"
        >
          <h2 id="details-title" class="mb-4 text-lg font-semibold text-ink-gray-9">Details</h2>
          <dl class="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-x-4 gap-y-3">
            <dt class="text-ink-gray-6">{{ item.rsl ? 'Case reference' : 'Reference' }}</dt>
            <dd class="break-words text-ink-gray-9">{{ item.reference }}</dd>
            <dt class="text-ink-gray-6">Taxpayer</dt>
            <dd class="break-words text-ink-gray-9">{{ item.taxpayer_name }}</dd>
            <dt class="text-ink-gray-6">Tax period</dt>
            <dd class="text-ink-gray-9">{{ formatPeriod(item.period) }}</dd>
            <dt class="text-ink-gray-6">Amount</dt>
            <dd class="tabular-nums text-ink-gray-9">
              {{ formatAmount(item.amount, item.currency) }}
            </dd>
            <dt class="text-ink-gray-6">{{ item.rsl ? 'Filed' : 'Sent' }}</dt>
            <dd class="text-ink-gray-9">
              {{ item.submitted_at ? formatDate(item.submitted_at) : 'Not sent yet' }}
            </dd>
          </dl>
        </section>

        <section
          aria-labelledby="progress-title"
          class="rounded-lg border border-outline-gray-2 p-5"
        >
          <h2 id="progress-title" class="mb-4 text-lg font-semibold text-ink-gray-9">Progress</h2>
          <ol class="space-y-4">
            <li v-for="step in item.steps" :key="step.label" class="flex gap-3">
              <span
                aria-hidden="true"
                class="mt-1 size-4 shrink-0"
                :class="[
                  stepIcon[step.state],
                  step.state === 'upcoming' ? 'text-ink-gray-6' : 'text-ink-blue-8',
                ]"
              />
              <div>
                <p
                  :class="
                    step.state === 'current' ? 'font-semibold text-ink-gray-9' : 'text-ink-gray-8'
                  "
                >
                  {{ step.label }}
                </p>
                <p class="text-sm text-ink-gray-6">
                  {{ stepStateLabel(step.state)
                  }}<template v-if="step.at"> · {{ formatDate(step.at) }}</template>
                </p>
              </div>
            </li>
          </ol>
        </section>
      </div>
    </template>
  </section>
</template>
