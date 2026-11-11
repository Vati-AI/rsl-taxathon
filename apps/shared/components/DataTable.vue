<script setup lang="ts" generic="Row extends Record<string, unknown>">
/**
 * Data table (TAX-145, DESIGN_STANDARD.md v2): dense rows (40px, 14px text), a sticky
 * header inside a labelled scroll region, sortable columns (aria-sort), column info
 * popovers, a muted dash for an empty value, one text row action and pagination at
 * 25 rows. Loading, empty and error states are part of the component.
 *
 * Sorting and paging happen here on the rows given; `v-model:sort` and `v-model:page`
 * let a page keep them in the URL. Cell slots: `#cell-<key>="{ row, value }"`.
 * Empty slot: `#empty`.
 */
import { computed } from 'vue';
import { Button } from 'frappe-ui';
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui';
import InlineError from './InlineError.vue';
import SkeletonBlock from './SkeletonBlock.vue';
import type { DataColumn, SortDirection } from './types';

const props = withDefaults(
  defineProps<{
    /** Accessible name of the table and its scroll region. */
    label: string;
    columns: DataColumn[];
    rows: Row[];
    rowKey: keyof Row & string;
    pageSize?: number;
    /** Text action on each row, e.g. "Open". Emits `row-action`. */
    rowAction?: string;
    loading?: boolean;
    /** An error title; the table shows InlineError with retry instead of rows. */
    error?: string | null;
    errorMessage?: string;
  }>(),
  {
    pageSize: 25,
    rowAction: undefined,
    loading: false,
    error: null,
    errorMessage: undefined,
  },
);
const emit = defineEmits<{ 'row-action': [row: Row]; retry: [] }>();
const sort = defineModel<{ key: string; direction: SortDirection } | null>('sort', {
  default: null,
});
const page = defineModel<number>('page', { default: 1 });

function isEmpty(value: unknown): boolean {
  return value === null || value === undefined || value === '';
}

function compare(a: unknown, b: unknown): number {
  if (isEmpty(a) && isEmpty(b)) return 0;
  if (isEmpty(a)) return 1; // empty values last in both directions
  if (isEmpty(b)) return -1;
  const na = Number(a);
  const nb = Number(b);
  if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

const sorted = computed(() => {
  const current = sort.value;
  if (!current) return props.rows;
  const factor = current.direction === 'asc' ? 1 : -1;
  return [...props.rows].sort((a, b) => {
    const result = compare(a[current.key], b[current.key]);
    if (isEmpty(a[current.key]) || isEmpty(b[current.key])) return result;
    return result * factor;
  });
});
const pages = computed(() => Math.max(1, Math.ceil(props.rows.length / props.pageSize)));
const currentPage = computed(() => Math.min(Math.max(1, page.value), pages.value));
const visible = computed(() =>
  sorted.value.slice((currentPage.value - 1) * props.pageSize, currentPage.value * props.pageSize),
);
const firstShown = computed(() =>
  props.rows.length ? (currentPage.value - 1) * props.pageSize + 1 : 0,
);
const lastShown = computed(() => Math.min(currentPage.value * props.pageSize, props.rows.length));

function ariaSort(column: DataColumn): 'ascending' | 'descending' | 'none' | undefined {
  if (!column.sortable) return undefined;
  const current = sort.value;
  if (!current || current.key !== column.key) return 'none';
  return current.direction === 'asc' ? 'ascending' : 'descending';
}

function sortIcon(column: DataColumn): string {
  const current = sort.value;
  if (!current || current.key !== column.key) return 'lucide-chevrons-up-down text-vati-muted';
  return current.direction === 'asc' ? 'lucide-arrow-up' : 'lucide-arrow-down';
}

function toggleSort(column: DataColumn) {
  const same = sort.value?.key === column.key;
  sort.value = {
    key: column.key,
    direction: same && sort.value?.direction === 'asc' ? 'desc' : 'asc',
  };
  page.value = 1;
}

function go(to: number) {
  page.value = Math.min(Math.max(1, to), pages.value);
}
</script>

<template>
  <div data-kit="data-table" class="w-full">
    <SkeletonBlock
      v-if="loading"
      :label="`Loading ${label.toLowerCase()}`"
      :lines="6"
      variant="rows"
    />

    <InlineError v-else-if="error" :title="error" :message="errorMessage" @retry="emit('retry')" />

    <slot v-else-if="rows.length === 0" name="empty" />

    <template v-else>
      <div
        role="region"
        :aria-label="label"
        tabindex="0"
        class="relative max-h-[640px] overflow-auto rounded-surface border border-vati-border bg-vati-surface"
      >
        <table class="w-full border-collapse text-left text-[14px]">
          <caption class="vati-sr-only">
            {{
              label
            }}
          </caption>
          <thead class="sticky top-0 z-10 bg-vati-surface-subtle">
            <tr>
              <th
                v-for="column in columns"
                :key="column.key"
                scope="col"
                :aria-sort="ariaSort(column)"
                :class="[
                  'h-vati-row whitespace-nowrap border-b border-vati-border px-3 font-medium text-vati-text',
                  column.numeric ? 'text-right' : 'text-left',
                  column.hideOnMobile && 'hidden md:table-cell',
                ]"
              >
                <span class="inline-flex items-center gap-1">
                  <button
                    v-if="column.sortable"
                    type="button"
                    class="inline-flex items-center gap-1 rounded-control hover:text-vati-accent"
                    @click="toggleSort(column)"
                  >
                    {{ column.label }}
                    <span aria-hidden="true" :class="['size-3.5', sortIcon(column)]" />
                  </button>
                  <template v-else>{{ column.label }}</template>
                  <PopoverRoot v-if="column.info">
                    <PopoverTrigger
                      :aria-label="`About ${column.label}`"
                      class="grid size-5 place-items-center rounded-full text-vati-muted hover:text-vati-text"
                    >
                      <span aria-hidden="true" class="lucide-info size-3.5" />
                    </PopoverTrigger>
                    <PopoverPortal>
                      <PopoverContent
                        side="bottom"
                        :side-offset="6"
                        class="z-50 max-w-xs rounded-surface border border-vati-border bg-vati-surface px-3 py-2 text-sm font-normal text-vati-text shadow-overlay"
                      >
                        {{ column.info }}
                      </PopoverContent>
                    </PopoverPortal>
                  </PopoverRoot>
                </span>
              </th>
              <th v-if="rowAction" scope="col" class="h-vati-row border-b border-vati-border px-3">
                <span class="vati-sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in visible"
              :key="String(row[rowKey])"
              class="border-b border-vati-border last:border-b-0 hover:bg-vati-surface-subtle"
            >
              <td
                v-for="column in columns"
                :key="column.key"
                :class="[
                  'h-vati-row px-3 align-middle',
                  column.numeric
                    ? 'vati-tabular whitespace-nowrap text-right'
                    : column.nowrap
                      ? 'whitespace-nowrap'
                      : 'break-words',
                  column.hideOnMobile && 'hidden md:table-cell',
                ]"
              >
                <slot :name="`cell-${column.key}`" :row="row" :value="row[column.key]">
                  <template v-if="!isEmpty(row[column.key])">{{ row[column.key] }}</template>
                  <template v-else>
                    <span aria-hidden="true" class="text-vati-muted">—</span>
                    <span class="vati-sr-only">Not available</span>
                  </template>
                </slot>
              </td>
              <td v-if="rowAction" class="h-vati-row whitespace-nowrap px-3 text-right">
                <button
                  type="button"
                  class="rounded-control font-medium text-vati-accent hover:underline"
                  @click="emit('row-action', row)"
                >
                  {{ rowAction }}<span class="vati-sr-only">{{ ` ${String(row[rowKey])}` }}</span>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <nav
        v-if="pages > 1"
        :aria-label="`${label} pages`"
        class="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm text-vati-muted"
      >
        <p class="vati-tabular">
          Rows {{ firstShown }}–{{ lastShown }} of {{ rows.length }} · Page {{ currentPage }} of
          {{ pages }}
        </p>
        <div class="flex gap-2">
          <Button
            label="Previous page"
            icon-left="lucide-chevron-left"
            :disabled="currentPage <= 1"
            @click="go(currentPage - 1)"
          />
          <Button
            label="Next page"
            icon-right="lucide-chevron-right"
            :disabled="currentPage >= pages"
            @click="go(currentPage + 1)"
          />
        </div>
      </nav>
    </template>
  </div>
</template>
