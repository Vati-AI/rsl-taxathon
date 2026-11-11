<script setup lang="ts">
/**
 * Vati Workspace app shell (TAX-146, DESIGN_STANDARD.md v2 layout rule 1). One workspace
 * header, a 240px sidebar that collapses to a 56px icon rail (remembered per browser),
 * and the page content left-aligned beside it up to 1280px. Below 1024px the sidebar and
 * the app tabs move into a navigation drawer opened from the header's Menu button.
 * ⌘K / Ctrl K and the header search open the command palette over `commands`.
 *
 * Both apps use it: the auditor app with app tabs and search, the taxpayer portal as a
 * simpler variant (no app tabs, no search). What appears is decided by the caller from
 * the server's access scope; the shell never adds navigation of its own.
 *
 * Slots: default (the page), `notice` (above the page, e.g. the session failed to load),
 * `actions` (notifications, help), `account` (user menu), `fixture` (fixture-only controls).
 */
import { ref, useId, watch } from 'vue';
import {
  DialogClose,
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from 'reka-ui';
import { RouterLink, type RouteLocationRaw } from 'vue-router';
import AppSidebar from './AppSidebar.vue';
import CommandPalette from './CommandPalette.vue';
import SkeletonBlock from './SkeletonBlock.vue';
import WorkspaceHeader from './WorkspaceHeader.vue';
import type { AppLink, CommandItem, NavItem, NavSection } from './types';

const props = withDefaults(
  defineProps<{
    product?: string;
    home?: RouteLocationRaw;
    /** Accessible name of the sidebar navigation. */
    label: string;
    apps?: AppLink[];
    sections: NavSection[];
    current: string | null;
    back?: NavItem;
    fixture?: boolean;
    search?: boolean;
    searchLabel?: string;
    commands?: CommandItem[];
    /** localStorage key for the collapsed sidebar; none means not remembered. */
    storageKey?: string;
    /** The navigation is still loading (the server has not said what is permitted). */
    navLoading?: boolean;
  }>(),
  {
    product: 'Vati Workspace',
    home: '/',
    apps: () => [],
    back: undefined,
    fixture: false,
    search: true,
    searchLabel: undefined,
    commands: () => [],
    storageKey: undefined,
    navLoading: false,
  },
);
const emit = defineEmits<{ select: [item: CommandItem]; 'palette-open': [] }>();

const drawerId = `vati-nav-drawer-${useId()}`;
const drawerOpen = ref(false);
const paletteOpen = ref(false);
watch(paletteOpen, (open) => open && emit('palette-open'));

function stored(): boolean {
  if (!props.storageKey) return false;
  try {
    return globalThis.localStorage?.getItem(props.storageKey) === 'collapsed';
  } catch {
    return false;
  }
}
const collapsed = ref(stored());
watch(collapsed, (value) => {
  if (!props.storageKey) return;
  try {
    globalThis.localStorage?.setItem(props.storageKey, value ? 'collapsed' : 'expanded');
  } catch {
    // Storage can be unavailable (private windows); the choice then lasts for this page.
  }
});
</script>

<template>
  <div data-kit="workspace-shell" class="flex h-full min-h-0 flex-col bg-vati-canvas">
    <a class="vati-skip-link" href="#main-content">Skip to main content</a>
    <WorkspaceHeader
      :product="product"
      :home="home"
      :apps="apps"
      :fixture="fixture"
      :search="search"
      :search-label="searchLabel"
      menu
      :menu-open="drawerOpen"
      :menu-controls="drawerId"
      @menu="drawerOpen = true"
      @search="paletteOpen = true"
    >
      <template #actions><slot name="actions" /></template>
      <template #account><slot name="account" /></template>
      <template v-if="$slots.fixture" #fixture><slot name="fixture" /></template>
    </WorkspaceHeader>

    <div class="flex min-h-0 flex-1">
      <div
        v-if="navLoading"
        class="hidden w-vati-sidebar shrink-0 border-r border-vati-border bg-vati-surface p-3 lg:block"
      >
        <SkeletonBlock label="Loading navigation" :lines="5" />
      </div>
      <div v-else class="hidden lg:flex">
        <AppSidebar
          v-model:collapsed="collapsed"
          :label="label"
          :sections="sections"
          :current="current"
          :back="back"
        />
      </div>
      <main
        id="main-content"
        tabindex="-1"
        class="relative min-h-0 min-w-0 flex-1 overflow-y-auto outline-none"
      >
        <div class="w-full max-w-vati-content">
          <slot name="notice" />
          <slot />
        </div>
      </main>
    </div>

    <DialogRoot v-model:open="drawerOpen">
      <DialogPortal>
        <DialogOverlay class="fixed inset-0 z-40 bg-black/30 lg:hidden" />
        <DialogContent
          :id="drawerId"
          data-kit="nav-drawer"
          :aria-describedby="undefined"
          class="fixed inset-y-0 left-0 z-50 flex w-[300px] max-w-[85vw] flex-col bg-vati-surface shadow-overlay focus:outline-none lg:hidden"
        >
          <div class="flex h-vati-header items-center gap-2 border-b border-vati-border px-3">
            <DialogTitle class="min-w-0 flex-1 truncate font-semibold text-vati-text">
              {{ product }}
            </DialogTitle>
            <DialogClose
              aria-label="Close menu"
              class="grid size-8 place-items-center rounded-control text-vati-muted hover:bg-vati-surface-subtle hover:text-vati-text"
            >
              <span aria-hidden="true" class="lucide-x size-4" />
            </DialogClose>
          </div>
          <nav
            v-if="apps.length"
            aria-label="Workspace apps"
            class="border-b border-vati-border p-2"
          >
            <ul class="flex flex-col gap-0.5">
              <li v-for="app in apps" :key="app.label">
                <component
                  :is="app.to ? RouterLink : 'a'"
                  v-bind="app.to ? { to: app.to } : { href: app.href }"
                  :aria-current="app.current ? 'page' : undefined"
                  :class="[
                    'flex h-9 items-center gap-2 rounded-control px-2 font-medium',
                    app.current
                      ? 'bg-vati-accent-subtle text-vati-accent'
                      : 'text-vati-text hover:bg-vati-surface-subtle',
                  ]"
                  @click="drawerOpen = false"
                >
                  <span class="min-w-0 flex-1 truncate">{{ app.label }}</span>
                  <span
                    v-if="app.unavailable"
                    class="rounded-full bg-vati-neutral-bg px-1.5 text-xs font-normal text-vati-neutral-text"
                  >
                    {{ app.unavailable }}
                  </span>
                </component>
              </li>
            </ul>
          </nav>
          <div class="min-h-0 flex-1">
            <AppSidebar
              :label="label"
              :sections="sections"
              :current="current"
              :back="back"
              :collapsible="false"
              fill
              @navigate="drawerOpen = false"
            />
          </div>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>

    <CommandPalette
      v-if="search"
      v-model:open="paletteOpen"
      :items="commands"
      @select="(item) => emit('select', item)"
    />
  </div>
</template>
