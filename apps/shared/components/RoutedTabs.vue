<script setup lang="ts">
/**
 * Tabs bound to routes (TAX-145): each tab is a link to its own URL, so a case tab can be
 * bookmarked, shared and opened with the browser's back button. The current tab carries
 * aria-current="page". Scrolls sideways on small screens instead of wrapping.
 */
import { RouterLink } from 'vue-router';
import type { RoutedTab } from './types';

const props = defineProps<{ tabs: RoutedTab[]; label: string; current: string | null }>();

function isCurrent(tab: RoutedTab): boolean {
  const names = Array.isArray(tab.match) ? tab.match : [tab.match];
  return props.current !== null && names.includes(props.current);
}
</script>

<template>
  <nav data-kit="routed-tabs" :aria-label="label" class="mb-5 border-b border-vati-border">
    <ul class="relative -mb-px flex gap-1 overflow-x-auto">
      <li v-for="tab in tabs" :key="tab.label" class="shrink-0">
        <RouterLink
          :to="tab.to"
          :aria-current="isCurrent(tab) ? 'page' : undefined"
          :class="[
            'inline-flex h-10 items-center gap-2 border-b-2 px-3 font-medium',
            isCurrent(tab)
              ? 'border-vati-accent text-vati-accent'
              : 'border-transparent text-vati-muted hover:border-vati-border-strong hover:text-vati-text',
          ]"
        >
          {{ tab.label }}
          <span
            v-if="tab.count !== undefined"
            class="vati-tabular rounded-full bg-vati-surface-subtle px-1.5 text-xs text-vati-text"
          >
            {{ tab.count }}
          </span>
        </RouterLink>
      </li>
    </ul>
  </nav>
</template>
