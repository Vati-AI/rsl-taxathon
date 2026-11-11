<script setup lang="ts">
/** The taxpayer's evidence requests (TAX-045): open ones first, then answered and closed. */
import { Alert, Badge, Button, LoadingText } from 'frappe-ui';
import { onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';
import PortalHeader from '../components/PortalHeader.vue';
import { EmptyState } from '../../../shared/components';
import { fetchEvidenceRequests, PortalError } from '../lib/api';
import { formatDate } from '../lib/format';
import { requestedTypeLabel, requestStatusDisplay } from '../lib/responses';
import type { EvidenceRequestView } from '../lib/types';

const items = ref<EvidenceRequestView[]>([]);
const loading = ref(true);
const error = ref<PortalError | null>(null);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    items.value = await fetchEvidenceRequests();
  } catch (err) {
    error.value = err instanceof PortalError ? err : new PortalError('unavailable', String(err));
  } finally {
    loading.value = false;
  }
}

function summary(item: EvidenceRequestView): string {
  const first = item.items[0];
  if (!first) return 'Documents requested';
  const more = item.items.length - 1;
  return more > 0
    ? `${requestedTypeLabel(first.document_type)} and ${more} more`
    : requestedTypeLabel(first.document_type);
}

onMounted(load);
</script>

<template>
  <section aria-labelledby="page-title">
    <PortalHeader
      title="Evidence requests"
      description="Documents RSL has asked you for about a claim you filed, and the answers you have sent."
    />

    <div v-if="loading" role="status" class="py-8">
      <LoadingText text="Loading evidence requests…" />
    </div>

    <Alert
      v-else-if="error"
      :theme="error.kind === 'denied' ? 'amber' : 'red'"
      :title="error.kind === 'denied' ? 'Access denied' : 'Evidence requests could not be loaded'"
      :description="error.message"
    >
      <template v-if="error.kind !== 'denied'" #actions>
        <Button label="Try again" @click="load" />
      </template>
    </Alert>

    <EmptyState
      v-else-if="items.length === 0"
      title="No evidence requests"
      message="If an officer needs more documents from you, the request will appear here."
      icon="lucide-inbox"
    />

    <ul
      v-else
      class="divide-y divide-outline-gray-1 rounded-lg border border-outline-gray-2"
      aria-label="Evidence requests"
    >
      <li v-for="item in items" :key="item.request_id">
        <RouterLink
          :to="{ name: 'evidence-request', params: { id: item.request_id } }"
          class="flex flex-wrap items-center gap-x-6 gap-y-2 p-4 hover:bg-surface-gray-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-blue-600"
        >
          <div class="min-w-0 flex-1 basis-64">
            <p class="font-medium text-ink-gray-9">{{ summary(item) }}</p>
            <p class="break-words text-sm text-ink-gray-6">
              {{ item.items.length }} {{ item.items.length === 1 ? 'item' : 'items' }} · Reply by
              {{ formatDate(item.due_date) }}
            </p>
          </div>
          <Badge
            size="md"
            :theme="requestStatusDisplay(item.status).theme"
            :label="requestStatusDisplay(item.status).label"
          />
        </RouterLink>
      </li>
    </ul>
  </section>
</template>
