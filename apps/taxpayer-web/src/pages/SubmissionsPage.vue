<script setup lang="ts">
import { Alert, Badge, Button, LoadingText } from 'frappe-ui';
import { onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';
import PortalHeader from '../components/PortalHeader.vue';
import { fetchSubmissions, PortalError } from '../lib/api';
import { formatAmount, formatDate, formatPeriod, statusDisplay } from '../lib/format';
import type { Submission } from '../lib/types';

const items = ref<Submission[]>([]);
const loading = ref(true);
const error = ref<PortalError | null>(null);

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
  <section aria-labelledby="page-title">
    <PortalHeader title="Your submissions" description="Track returns and claims you have sent.">
      <template #actions>
        <Button
          variant="solid"
          label="New submission"
          icon-left="lucide-plus"
          :route="{ name: 'new-submission' }"
        />
      </template>
    </PortalHeader>

    <div v-if="loading" role="status" class="py-8">
      <LoadingText text="Loading submissions…" />
    </div>

    <Alert
      v-else-if="error"
      :theme="error.kind === 'denied' ? 'amber' : 'red'"
      :title="error.kind === 'denied' ? 'Access denied' : 'Submissions could not be loaded'"
      :description="error.message"
    >
      <template v-if="error.kind !== 'denied'" #actions>
        <Button label="Try again" @click="load" />
      </template>
    </Alert>

    <div
      v-else-if="items.length === 0"
      class="rounded-lg border border-outline-gray-2 p-8 text-center"
    >
      <h2 class="text-lg font-semibold text-ink-gray-9">No submissions yet</h2>
      <p class="mt-1 text-ink-gray-6">Returns and claims you send will appear here.</p>
    </div>

    <ul
      v-else
      class="divide-y divide-outline-gray-1 rounded-lg border border-outline-gray-2"
      aria-label="Submissions"
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
            <p class="break-words text-sm text-ink-gray-6">
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
