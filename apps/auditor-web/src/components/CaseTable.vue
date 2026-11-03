<script setup lang="ts">
import type { FixtureCase } from '../api/fixtures';
import { scenarioLabel, type SortDirection, type SortKey } from '../domain/cases';
import AmountText from './AmountText.vue';
import ReviewLaneBadge from './ReviewLaneBadge.vue';

const props = defineProps<{
  rows: FixtureCase[];
  caption: string;
  sort: SortKey;
  direction: SortDirection;
}>();
const emit = defineEmits<{ sort: [key: SortKey] }>();

const columns: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: 'id', label: 'Case' },
  { key: 'name', label: 'Taxpayer (synthetic)' },
  { key: 'scenario', label: 'Scenario' },
  { key: 'refund_claim', label: 'Refund claimed', numeric: true },
  { key: 'supported_refund', label: 'Supported refund', numeric: true },
  { key: 'expected', label: 'Review lane' },
];

function ariaSort(key: SortKey) {
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
    <table class="w-full min-w-[760px] border-collapse text-left">
      <caption class="sr-only">
        {{
          caption
        }}
      </caption>
      <thead class="border-b border-outline-gray-1 bg-surface-gray-1 text-sm text-ink-gray-6">
        <tr>
          <th
            v-for="column in columns"
            :key="column.key"
            scope="col"
            :aria-sort="ariaSort(column.key)"
            :class="['px-3 py-2 font-medium', column.numeric ? 'text-right' : '']"
          >
            <button
              type="button"
              :class="[
                'inline-flex items-center gap-1 rounded-control hover:text-ink-gray-9',
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
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.id"
          class="border-b border-outline-gray-1 last:border-b-0 hover:bg-surface-gray-1"
        >
          <td class="whitespace-nowrap px-3 py-2">
            <RouterLink
              :to="{ name: 'case', params: { id: row.id } }"
              class="font-medium text-ink-blue-link underline-offset-2 hover:underline"
            >
              {{ row.id }}
            </RouterLink>
          </td>
          <td class="max-w-[280px] break-words px-3 py-2 text-ink-gray-8">{{ row.name }}</td>
          <td class="px-3 py-2 text-ink-gray-6">{{ scenarioLabel(row.scenario) }}</td>
          <td class="px-3 py-2 text-right">
            <AmountText :value="row.refund_claim" :currency="row.currency" />
          </td>
          <td class="px-3 py-2 text-right">
            <AmountText :value="row.supported_refund" :currency="row.currency" />
          </td>
          <td class="px-3 py-2"><ReviewLaneBadge :lane="row.expected" /></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
