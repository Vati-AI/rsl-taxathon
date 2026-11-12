<script setup lang="ts">
import { Alert, Badge, Button, LoadingText } from 'frappe-ui';
import { computed, onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';
import PortalHeader from '../components/PortalHeader.vue';
import { EmptyState } from '../../../shared/components';
import { fetchSubmissions, PortalError } from '../lib/api';
import {
  formatAmount,
  formatDate,
  formatPeriod,
  rslStatusText,
  statusDisplay,
} from '../lib/format';
import { sessionState } from '../lib/session';
import type { Submission } from '../lib/types';

// TAX-125 (ADR-043): a read-only list. Claims are filed in RSL's Online Refunds
// Application; RSL keeps their status, and this list only shows it.
const items = ref<Submission[]>([]);
const loading = ref(true);
const error = ref<PortalError | null>(null);
const rslUrl = computed(() =>
  sessionState.value.status === 'ready'
    ? (sessionState.value.session.rsl_online_refunds_url ?? null)
    : null,
);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    items.value = await fetchSubmissions();
  } catch (err) {
    error.value = err instanceof PortalError ? err : new PortalError('unavailable', String(err));
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section aria-labelledby="page-title" class="max-w-4xl">
    <PortalHeader
      title="My cases"
      description="Refund claims you filed with RSL that RSL is reviewing with you. RSL's evidence requests appear under Evidence requests."
    />

    <Alert class="mb-6" theme="gray" title="To file or change a refund claim, use RSL's system">
      <template #description>
        <p>
          Refund claims are filed in RSL's Online Refunds Application. RSL keeps each claim's status
          there; this list shows it for reference.
        </p>
      </template>
      <template v-if="rslUrl" #actions>
        <a
          :href="rslUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="text-base font-medium text-ink-blue-8 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
        >
          Open RSL's Online Refunds Application<span class="sr-only"> (opens in a new tab)</span>
        </a>
      </template>
    </Alert>

    <div v-if="loading" role="status" class="py-8">
      <LoadingText text="Loading your cases…" />
    </div>

    <Alert
      v-else-if="error"
      :theme="error.kind === 'denied' ? 'amber' : 'red'"
      :title="error.kind === 'denied' ? 'Access denied' : 'Your cases could not be loaded'"
      :description="error.message"
    >
      <template v-if="error.kind !== 'denied'" #actions>
        <Button label="Try again" @click="load" />
      </template>
    </Alert>

    <EmptyState
      v-else-if="items.length === 0"
      title="No cases yet"
      message="When RSL reviews a refund claim you filed, it appears here."
      icon="lucide-file-text"
    />

    <ul
      v-else
      class="divide-y divide-outline-gray-1 rounded-lg border border-outline-gray-2"
      aria-label="My cases"
    >
      <li v-for="item in items" :key="item.id">
        <RouterLink
          :to="{ name: 'submission', params: { id: item.id } }"
          class="flex flex-wrap items-center gap-x-6 gap-y-2 p-4 hover:bg-surface-gray-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-blue-600"
        >
          <div class="min-w-0 flex-1 basis-64">
            <p class="font-medium text-ink-gray-9">
              {{ item.tax_type }} · {{ formatPeriod(item.period) }}
            </p>
            <p v-if="item.rsl" class="break-words text-sm text-ink-gray-6">
              RSL claim {{ item.rsl.claim_ref }} · RSL status: {{ rslStatusText(item.rsl) }}
            </p>
            <p v-else class="break-words text-sm text-ink-gray-6">
              {{ item.reference }} ·
              {{ item.submitted_at ? `Sent ${formatDate(item.submitted_at)}` : 'Not sent yet' }}
            </p>
          </div>
          <p class="text-right font-medium tabular-nums text-ink-gray-8">
            {{ formatAmount(item.amount, item.currency) }}
          </p>
          <Badge
            size="md"
            :theme="statusDisplay(item.status).theme"
            :label="statusDisplay(item.status).label"
          />
        </RouterLink>
      </li>
    </ul>
  </section>
</template>
