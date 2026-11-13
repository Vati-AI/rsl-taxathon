<script setup lang="ts">
/**
 * Case workspace · Summary (TAX-148): why the case was selected, the taxpayer and the
 * processing state of each connected service. The key facts sit in the workspace header.
 */
import { ProvenanceValue } from '../../../../shared/components';
import type { Case } from '../../api/vati';
import SelectionReasons from '../../components/SelectionReasons.vue';
import { serviceLabel, taxpayerTypeLabel } from '../../domain/cases';
import { formatTimestamp } from '../../domain/dates';

defineOptions({ inheritAttrs: false });
defineProps<{ caseId: string; item: Case }>();
const SYNC_LABELS = { synced: 'Up to date', pending: 'Waiting', failed: 'Failed' } as const;
</script>

<template>
  <!-- minmax(0, …) lets wide children (the service table) scroll instead of widening the column. -->
  <div class="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
    <section
      aria-labelledby="reasons-heading"
      class="rounded-surface border border-outline-gray-1 bg-surface-base"
    >
      <h2
        id="reasons-heading"
        class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
      >
        Why RSL selected this case
      </h2>
      <div class="px-4 py-4">
        <SelectionReasons :reasons="item.selection_reasons" />
        <p class="mt-3 text-sm text-ink-gray-5">
          RSL selects cases. Reasons and scores marked RSL are RSL's own; Vati signals corroborate
          or question them. They are not findings, assessments or decisions.
        </p>
      </div>
    </section>
    <div class="space-y-4">
      <section
        aria-labelledby="taxpayer-heading"
        class="rounded-surface border border-outline-gray-1 bg-surface-base"
      >
        <h2
          id="taxpayer-heading"
          class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
        >
          Taxpayer
        </h2>
        <div class="space-y-2 px-4 py-4">
          <p class="break-words text-ink-gray-8">{{ item.taxpayer.display_name }}</p>
          <p class="text-sm text-ink-gray-5">
            {{ taxpayerTypeLabel(item.taxpayer.taxpayer_type) }} · synthetic record ·
            {{ item.assigned_officer_id ? 'assigned officer' : 'unassigned' }}
          </p>
          <RouterLink
            :to="{ name: 'taxpayer', params: { taxpayerId: item.taxpayer_id } }"
            class="inline-flex items-center gap-1 text-ink-blue-link underline-offset-2 hover:underline"
          >
            View taxpayer profile
            <span class="lucide-arrow-right size-4" aria-hidden="true" />
          </RouterLink>
        </div>
      </section>
      <section aria-labelledby="processing-heading">
        <h2 id="processing-heading" class="mb-2 text-lg font-semibold text-ink-gray-9">
          Processing by service
        </h2>
        <div
          role="region"
          aria-label="Service processing table"
          tabindex="0"
          class="relative overflow-x-auto rounded-surface border border-outline-gray-1 bg-surface-base"
        >
          <table class="w-full min-w-[480px] border-collapse text-left text-sm">
            <thead class="border-b border-outline-gray-1 bg-surface-gray-1 text-ink-gray-6">
              <tr>
                <th scope="col" class="px-3 py-2 font-medium">Service</th>
                <th scope="col" class="px-3 py-2 font-medium">Record</th>
                <th scope="col" class="px-3 py-2 font-medium">State</th>
                <th scope="col" class="px-3 py-2 font-medium">Last attempt</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="link in item.service_links"
                :key="link.service + link.external_type"
                class="border-b border-outline-gray-1 last:border-b-0"
              >
                <td class="px-3 py-2 text-ink-gray-8">{{ serviceLabel(link.service) }}</td>
                <td class="px-3 py-2 text-ink-gray-6">
                  <ProvenanceValue v-if="link.external_id" :value="link.external_id" />
                  <template v-else>Not created yet</template>
                </td>
                <td
                  :class="[
                    'px-3 py-2',
                    link.sync.state === 'failed' ? 'text-ink-red-7' : 'text-ink-gray-7',
                  ]"
                >
                  {{ SYNC_LABELS[link.sync.state] }}
                  <span v-if="link.sync.error_code">({{ link.sync.error_code }})</span>
                </td>
                <td class="px-3 py-2 text-ink-gray-6">
                  {{ formatTimestamp(link.sync.last_attempt_at) ?? 'Not attempted' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </div>
</template>
