<script setup lang="ts">
/**
 * App rail and sidebar (TAX-145; used by the TAX-146 shell): named sections of views with
 * counts (My cases 12, Awaiting evidence 4), a "Back to …" item in object context, and a
 * collapse button that turns the 240px panel into a 56px icon rail. The dark variant is
 * the optional dark navigation panel; content stays light.
 */
import { RouterLink } from 'vue-router';
import type { NavItem, NavSection } from './types';

const props = withDefaults(
  defineProps<{
    label: string;
    sections: NavSection[];
    /** Current route name, to mark the current item. */
    current: string | null;
    /** "Back to cases" in object context. */
    back?: NavItem;
    dark?: boolean;
  }>(),
  { back: undefined, dark: false },
);
const collapsed = defineModel<boolean>('collapsed', { default: false });

function isCurrent(item: NavItem): boolean {
  const names = Array.isArray(item.match) ? item.match : [item.match];
  return props.current !== null && names.includes(props.current);
}
</script>

<template>
  <nav
    data-kit="app-sidebar"
    :aria-label="label"
    :data-collapsed="collapsed"
    :class="[
      'flex h-full shrink-0 flex-col border-r py-3 transition-[width]',
      collapsed ? 'w-vati-rail' : 'w-vati-sidebar',
      dark
        ? 'border-vati-nav-active bg-vati-nav-bg text-vati-nav-text'
        : 'border-vati-border bg-vati-surface',
    ]"
  >
    <div class="flex-1 overflow-y-auto px-2">
      <RouterLink
        v-if="back"
        :to="back.to"
        :class="[
          'mb-3 flex h-9 items-center gap-2 rounded-control px-2 font-medium',
          dark
            ? 'text-vati-nav-text hover:bg-vati-nav-active'
            : 'text-vati-muted hover:bg-vati-surface-subtle hover:text-vati-text',
        ]"
      >
        <span aria-hidden="true" class="lucide-arrow-left size-4 shrink-0" />
        <span :class="collapsed ? 'vati-sr-only' : 'truncate'">{{ back.label }}</span>
      </RouterLink>
      <div v-for="(section, index) in sections" :key="section.label ?? index" class="mb-3">
        <p
          v-if="section.label && !collapsed"
          :class="[
            'px-2 pb-1 text-xs font-medium uppercase tracking-wide',
            dark ? 'text-vati-nav-muted' : 'text-vati-muted',
          ]"
        >
          {{ section.label }}
        </p>
        <ul class="flex flex-col gap-0.5">
          <li v-for="item in section.items" :key="item.label">
            <RouterLink
              :to="item.to"
              :aria-current="isCurrent(item) ? 'page' : undefined"
              :title="collapsed ? item.label : undefined"
              :class="[
                'flex h-9 items-center gap-2 rounded-control px-2',
                isCurrent(item)
                  ? dark
                    ? 'bg-vati-nav-active font-medium text-vati-nav-text'
                    : 'bg-vati-accent-subtle font-medium text-vati-accent'
                  : dark
                    ? 'text-vati-nav-text hover:bg-vati-nav-active'
                    : 'text-vati-text hover:bg-vati-surface-subtle',
              ]"
            >
              <span aria-hidden="true" :class="[item.icon, 'size-4 shrink-0']" />
              <span :class="collapsed ? 'vati-sr-only' : 'min-w-0 flex-1 truncate'">
                {{ item.label }}
              </span>
              <span
                v-if="item.count !== undefined && !collapsed"
                :class="['vati-tabular text-xs', dark ? 'text-vati-nav-muted' : 'text-vati-muted']"
              >
                {{ item.count }}
              </span>
            </RouterLink>
          </li>
        </ul>
      </div>
    </div>
    <div class="px-2 pt-2">
      <button
        type="button"
        :aria-expanded="!collapsed"
        :aria-label="collapsed ? 'Expand navigation' : 'Collapse navigation'"
        :class="[
          'flex h-9 w-full items-center gap-2 rounded-control px-2',
          dark
            ? 'text-vati-nav-muted hover:bg-vati-nav-active'
            : 'text-vati-muted hover:bg-vati-surface-subtle',
        ]"
        @click="collapsed = !collapsed"
      >
        <span
          aria-hidden="true"
          :class="[
            collapsed ? 'lucide-panel-left-open' : 'lucide-panel-left-close',
            'size-4 shrink-0',
          ]"
        />
        <span v-if="!collapsed">Collapse</span>
      </button>
    </div>
  </nav>
</template>
