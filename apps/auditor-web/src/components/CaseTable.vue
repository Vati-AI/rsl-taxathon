<script setup lang="ts">
import type { Case } from '../api/vati';
import { laneLabel, taxpayerTypeLabel, type SortDirection, type SortKey } from '../domain/cases';
import AmountText from './AmountText.vue';
import CaseStatusBadge from './CaseStatusBadge.vue';
import ProcessingIndicator from './ProcessingIndicator.vue';
import SelectionReasons from './SelectionReasons.vue';

const props = defineProps<{
  rows: Case[];
  caption: string;
  sort: SortKey;
  direction: SortDirection;
}>();
const emit = defineEmits<{ sort: [key: SortKey] }>();

const columns: { key: SortKey | null; label: string; numeric?: boolean }[] = [
  { key: 'display_id', label: 'Case' },
  { key: 'taxpayer', label: 'Taxpayer (synthetic)' },
  { key: null, label: 'Why selected' },
  { key: 'status', label: 'Status' },
  { key: 'refund_lane', label: 'Refund lane' },
  { key: 'refund_claim', label: 'Refund claimed', numeric: true },
  { key: null, label: 'Processing' },
];

function ariaSort(key: SortKey | null) {
  if (!key) return undefined;
  if (props.sort !== key) return 'none';
  return props.direction === 'asc' ? 'ascending' : 'descending';
}
</script>

<template>
  <!-- Wide officer tables scroll inside a labelled, focusable region rather than the page. -->
  <div
    role="region"
    :aria-label="caption"
    tabindex="0"
    class="overflow-x-auto rounded-surface border border-outline-gray-1 bg-surface-base"
  >
    <table class="w-full min-w-[1040px] border-collapse text-left">
      <caption class="sr-only">
        {{
          caption
        }}
      </caption>
      <thead class="border-b border-outline-gray-1 bg-surface-gray-1 text-sm text-ink-gray-6">
        <tr>
          <th
            v-for="column in columns"
            :key="column.label"
            scope="col"
            :aria-sort="ariaSort(column.key)"
            :class="['px-3 py-2 align-bottom font-medium', column.numeric ? 'text-right' : '']"
          >
            <button
              v-if="column.key"
              type="button"
              :class="[
                'inline-flex items-center gap-1 whitespace-nowrap rounded-control hover:text-ink-gray-9',
                column.numeric ? 'flex-row-reverse' : '',
              ]"
              @click="emit('sort', column.key)"
            >
              <span>{{ column.label }}</span>
              <span
                :class="[
                  'size-3.5',
                  sort !== column.key
                    ? 'lucide-chevrons-up-down text-ink-gray-4'
                    : direction === 'asc'
                      ? 'lucide-arrow-up'
                      : 'lucide-arrow-down',
                ]"
                aria-hidden="true"
              />
            </button>
            <span v-else class="whitespace-nowrap">{{ column.label }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.case_id"
          class="border-b border-outline-gray-1 align-top last:border-b-0 hover:bg-surface-gray-1"
        >
          <td class="whitespace-nowrap px-3 py-2">
            <RouterLink
              :to="{ name: 'case', params: { caseId: row.case_id } }"
              class="font-medium text-ink-blue-link underline-offset-2 hover:underline"
            >
              {{ row.display_id }}
            </RouterLink>
          </td>
          <td class="max-w-[240px] px-3 py-2">
            <span class="block break-words text-ink-gray-8">{{ row.taxpayer.display_name }}</span>
            <span class="text-sm text-ink-gray-5">{{
              taxpayerTypeLabel(row.taxpayer.taxpayer_type)
            }}</span>
          </td>
          <td class="max-w-[280px] px-3 py-2 text-ink-gray-7">
            <SelectionReasons :reasons="row.selection_reasons" compact />
          </td>
          <td class="px-3 py-2"><CaseStatusBadge :status="row.status" /></td>
          <td class="whitespace-nowrap px-3 py-2 text-ink-gray-7">
            {{ laneLabel(row.refund_lane) }}
          </td>
          <td class="px-3 py-2 text-right">
            <AmountText
              :value="row.refund_claim?.amount ?? null"
              :currency="row.refund_claim?.currency ?? ''"
            />
          </td>
          <td class="max-w-[220px] px-3 py-2 text-sm">
            <ProcessingIndicator :item="row" />
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
