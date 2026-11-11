<script setup lang="ts">
/**
 * Command palette (TAX-145): ⌘K / Ctrl K opens a search over case references, TINs and
 * pages. Combobox pattern: the input owns a listbox of results grouped by kind; arrow
 * keys move the active option, Enter chooses it, Escape closes and focus returns to
 * where it was. The page passes the items it may show (already permission-filtered)
 * and handles `select`.
 */
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import { DialogContent, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui';
import type { CommandItem } from './types';

const props = withDefaults(
  defineProps<{
    items: CommandItem[];
    /** Listen for ⌘K / Ctrl K on the whole page. */
    shortcut?: boolean;
    placeholder?: string;
  }>(),
  { shortcut: true, placeholder: 'Search case reference, TIN or page' },
);
const emit = defineEmits<{ select: [item: CommandItem] }>();
const open = defineModel<boolean>('open', { default: false });

const query = ref('');
const active = ref(0);
const baseId = `vati-palette-${useId()}`;
const listId = `${baseId}-list`;

const results = computed(() => {
  const q = query.value.trim().toLowerCase();
  const matches = q
    ? props.items.filter((item) =>
        [item.label, item.hint ?? '', ...(item.keywords ?? [])].join(' ').toLowerCase().includes(q),
      )
    : props.items;
  return matches.slice(0, 50);
});
const groups = computed(() => {
  const out: { name: string; items: { item: CommandItem; index: number }[] }[] = [];
  results.value.forEach((item, index) => {
    let group = out.find((g) => g.name === item.group);
    if (!group) out.push((group = { name: item.group, items: [] }));
    group.items.push({ item, index });
  });
  return out;
});
const optionId = (index: number) => `${baseId}-option-${index}`;

watch(query, () => (active.value = 0));
watch(open, (value) => {
  if (value) {
    query.value = '';
    active.value = 0;
  }
});

function choose(item: CommandItem | undefined) {
  if (!item) return;
  emit('select', item);
  open.value = false;
}

function onKey(event: KeyboardEvent) {
  const count = results.value.length;
  if (event.key === 'ArrowDown' && count) {
    event.preventDefault();
    active.value = (active.value + 1) % count;
  } else if (event.key === 'ArrowUp' && count) {
    event.preventDefault();
    active.value = (active.value - 1 + count) % count;
  } else if (event.key === 'Enter') {
    event.preventDefault();
    choose(results.value[active.value]);
  }
}

function onGlobalKey(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    open.value = !open.value;
  }
}
onMounted(() => props.shortcut && window.addEventListener('keydown', onGlobalKey));
onBeforeUnmount(() => window.removeEventListener('keydown', onGlobalKey));
</script>

<template>
  <DialogRoot v-model:open="open">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-40 bg-black/30" />
      <DialogContent
        data-kit="command-palette"
        :aria-describedby="undefined"
        class="fixed left-1/2 top-[12vh] z-50 w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-surface border border-vati-border bg-vati-surface shadow-overlay focus:outline-none"
      >
        <DialogTitle class="vati-sr-only">Search Vati Workspace</DialogTitle>
        <div class="flex items-center gap-2 border-b border-vati-border px-3">
          <span aria-hidden="true" class="lucide-search size-4 text-vati-muted" />
          <input
            v-model="query"
            type="text"
            role="combobox"
            aria-autocomplete="list"
            :aria-expanded="results.length > 0"
            :aria-controls="listId"
            :aria-activedescendant="results.length ? optionId(active) : undefined"
            :aria-label="placeholder"
            :placeholder="placeholder"
            class="h-12 w-full border-0 bg-transparent px-0 text-base text-vati-text placeholder:text-vati-muted focus:outline-none focus:ring-0"
            @keydown="onKey"
          />
          <kbd
            aria-hidden="true"
            class="rounded border border-vati-border px-1 text-xs text-vati-muted"
          >
            Esc
          </kbd>
        </div>
        <ul
          v-if="results.length"
          :id="listId"
          role="listbox"
          aria-label="Results"
          class="max-h-80 overflow-y-auto py-2"
        >
          <template v-for="group in groups" :key="group.name">
            <li
              role="presentation"
              class="px-3 pb-1 pt-2 text-xs font-medium uppercase text-vati-muted"
            >
              {{ group.name }}
            </li>
            <li
              v-for="{ item, index } in group.items"
              :id="optionId(index)"
              :key="item.id"
              role="option"
              :aria-selected="index === active"
              :class="[
                'mx-2 flex cursor-pointer items-center justify-between gap-3 rounded-control px-2 py-2',
                index === active ? 'bg-vati-accent-subtle text-vati-accent' : 'text-vati-text',
              ]"
              @mousemove="active = index"
              @click="choose(item)"
            >
              <span class="min-w-0 truncate font-medium">{{ item.label }}</span>
              <span v-if="item.hint" class="shrink-0 text-sm text-vati-muted">{{ item.hint }}</span>
            </li>
          </template>
        </ul>
        <p v-else role="status" class="px-4 py-6 text-center text-vati-muted">
          Nothing matches “{{ query }}”.
        </p>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
