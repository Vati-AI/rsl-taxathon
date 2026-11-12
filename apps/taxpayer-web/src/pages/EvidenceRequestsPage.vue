<script setup lang="ts">
/**
 * Evidence requests as an inbox (TAX-152; requests from TAX-045). The requests are listed
 * like messages, grouped by reply-by date, beside a reading pane that opens one request
 * at its own URL (/requests/:id). Below 1024px the list and the request take turns, with
 * a link back to the list.
 */
import { Alert, Badge, Button, LoadingText } from 'frappe-ui';
import { computed, onMounted, ref } from 'vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';
import PortalHeader from '../components/PortalHeader.vue';
import { EmptyState } from '../../../shared/components';
import { fetchEvidenceRequests, PortalError } from '../lib/api';
import { formatDate } from '../lib/format';
import { groupRequests, todayIso } from '../lib/inbox';
import { requestedTypeLabel, requestStatusDisplay } from '../lib/responses';
import type { EvidenceRequestView } from '../lib/types';

const route = useRoute();
const items = ref<EvidenceRequestView[]>([]);
const loading = ref(true);
const error = ref<PortalError | null>(null);
const selected = computed(() => (typeof route.params.id === 'string' ? route.params.id : null));
const groups = computed(() => groupRequests(items.value, todayIso()));

async function load(silent = false) {
  if (!silent) loading.value = true;
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

onMounted(() => load());
</script>

<template>
  <section aria-labelledby="page-title">
    <PortalHeader
      :title="selected ? 'Evidence request' : 'Evidence requests'"
      :description="
        selected
          ? undefined
          : 'Documents RSL has asked you for about a claim you filed, and the answers you have sent.'
      "
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
        <Button label="Try again" @click="load()" />
      </template>
    </Alert>

    <EmptyState
      v-else-if="items.length === 0 && !selected"
      title="No evidence requests"
      message="If an officer needs more documents from you, the request will appear here."
      icon="lucide-inbox"
    />

    <div
      v-else
      class="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
    >
      <nav
        aria-label="Evidence requests"
        data-kit="request-list"
        :class="selected ? 'hidden lg:block' : ''"
      >
        <div v-for="group in groups" :key="group.key" class="mb-4">
          <h2
            :id="`group-${group.key}`"
            class="mb-1 text-xs font-medium uppercase tracking-wide text-vati-muted"
          >
            {{ group.label }} ({{ group.items.length }})
          </h2>
          <ul
            :aria-labelledby="`group-${group.key}`"
            class="divide-y divide-vati-border overflow-hidden rounded-surface border border-vati-border bg-vati-surface"
          >
            <li v-for="item in group.items" :key="item.request_id">
              <RouterLink
                :to="{ name: 'evidence-request', params: { id: item.request_id } }"
                :aria-current="item.request_id === selected ? 'page' : undefined"
                :class="[
                  'flex items-start gap-3 px-3 py-2.5 hover:bg-vati-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-blue-600',
                  item.request_id === selected && 'bg-vati-accent-subtle',
                ]"
              >
                <span class="min-w-0 flex-1">
                  <span class="block font-medium text-vati-text">{{ summary(item) }}</span>
                  <span class="block text-sm text-vati-muted">
                    {{ item.items.length }} {{ item.items.length === 1 ? 'item' : 'items' }}
                    <template v-if="item.due_date">
                      · Reply by {{ formatDate(item.due_date) }}
                    </template>
                  </span>
                </span>
                <Badge
                  :theme="requestStatusDisplay(item.status).theme"
                  :label="requestStatusDisplay(item.status).label"
                  class="shrink-0 whitespace-nowrap"
                />
              </RouterLink>
            </li>
          </ul>
        </div>
      </nav>

      <div data-kit="reading-pane" :class="['min-w-0', !selected && 'hidden lg:block']">
        <RouterLink
          v-if="selected"
          :to="{ name: 'evidence-requests' }"
          class="mb-3 inline-flex items-center gap-1 text-sm text-vati-accent underline-offset-2 hover:underline lg:hidden"
        >
          <span aria-hidden="true" class="lucide-arrow-left size-4" />
          All evidence requests
        </RouterLink>
        <RouterView v-slot="{ Component }">
          <component :is="Component" @changed="load(true)" />
        </RouterView>
      </div>
    </div>
  </section>
</template>
