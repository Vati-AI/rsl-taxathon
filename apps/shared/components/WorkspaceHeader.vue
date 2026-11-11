<script setup lang="ts">
/**
 * Workspace header (TAX-145; the TAX-146 shell): one bar across every Vati Workspace app.
 * Product mark and name, the app tabs (Cases, Investigation, Explore, Reports, Admin, as
 * the server permits), a search button that opens the command palette (⌘K / Ctrl K), and
 * the account area. Fixture mode shows as a slim strip here, never as a full-width banner.
 * Below 1024px the app tabs move into the navigation drawer, opened by the menu button.
 *
 * Slots: `actions` (notifications, help), `account` (user menu), `fixture` (fixture-only
 * controls beside the strip, such as the acting-officer switch).
 */
import { RouterLink, type RouteLocationRaw } from 'vue-router';
import type { AppLink } from './types';

withDefaults(
  defineProps<{
    /** Product name shown beside the mark. */
    product?: string;
    /** Where the product mark leads. */
    home?: RouteLocationRaw;
    apps?: AppLink[];
    /** Accessible name of the app tabs (unique per page). */
    appsLabel?: string;
    /** Show the fixture-mode strip. */
    fixture?: boolean;
    /** Show the search button (needs a command palette on the page). */
    search?: boolean;
    searchLabel?: string;
    /** Show the menu button (below 1024px) that opens the navigation drawer. */
    menu?: boolean;
    menuOpen?: boolean;
    menuControls?: string;
  }>(),
  {
    product: 'Vati Workspace',
    home: '/',
    apps: () => [],
    appsLabel: 'Workspace apps',
    fixture: false,
    search: true,
    searchLabel: 'Search cases, TINs and pages',
    menu: false,
    menuOpen: false,
    menuControls: undefined,
  },
);
defineEmits<{ search: []; menu: [] }>();

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
</script>

<template>
  <header
    data-kit="workspace-header"
    class="flex h-vati-header shrink-0 items-center gap-2 border-b border-vati-border bg-vati-surface px-3 sm:gap-3 sm:px-4"
  >
    <button
      v-if="menu"
      type="button"
      :aria-expanded="menuOpen"
      :aria-controls="menuControls"
      class="-ml-1 inline-flex h-8 items-center gap-1.5 rounded-control px-2 font-medium text-vati-text hover:bg-vati-surface-subtle lg:hidden"
      @click="$emit('menu')"
    >
      <span aria-hidden="true" class="lucide-menu size-4" />
      <span class="hidden sm:inline">Menu</span>
      <span class="vati-sr-only sm:hidden">Menu</span>
    </button>

    <RouterLink
      :to="home"
      class="flex shrink-0 items-center gap-2 rounded-control font-semibold text-vati-text"
    >
      <span
        aria-hidden="true"
        class="grid size-7 place-items-center rounded-control bg-vati-accent text-sm font-semibold text-white"
      >
        V
      </span>
      <span class="hidden sm:inline">{{ product }}</span>
      <span class="vati-sr-only sm:hidden">{{ product }}</span>
    </RouterLink>

    <nav v-if="apps.length" :aria-label="appsLabel" class="hidden min-w-0 lg:block">
      <ul class="flex items-center gap-1">
        <li v-for="app in apps" :key="app.label">
          <component
            :is="app.to ? RouterLink : 'a'"
            v-bind="app.to ? { to: app.to } : { href: app.href }"
            :aria-current="app.current ? 'page' : undefined"
            :title="app.unavailable ? `${app.label}: ${app.unavailable.toLowerCase()}` : undefined"
            :class="[
              'inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-control px-2.5 font-medium',
              app.current
                ? 'bg-vati-accent-subtle text-vati-accent'
                : 'text-vati-muted hover:bg-vati-surface-subtle hover:text-vati-text',
            ]"
          >
            {{ app.label }}
            <template v-if="app.unavailable">
              <span aria-hidden="true" class="lucide-plug-zap size-3.5 text-vati-muted" />
              <span class="vati-sr-only">({{ app.unavailable.toLowerCase() }})</span>
            </template>
          </component>
        </li>
      </ul>
    </nav>

    <div v-if="fixture || $slots.fixture" class="flex shrink-0 items-center gap-2">
      <span
        v-if="fixture"
        data-kit="fixture-strip"
        class="shrink-0 whitespace-nowrap rounded-full bg-vati-fixture-bg px-2 py-0.5 text-xs font-medium text-vati-fixture-text"
      >
        Synthetic data
      </span>
      <slot name="fixture" />
    </div>

    <div class="ml-auto flex min-w-0 items-center gap-1 sm:gap-2">
      <button
        v-if="search"
        type="button"
        aria-keyshortcuts="Meta+K Control+K"
        class="inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-control border border-vati-border bg-vati-surface px-2.5 text-vati-muted hover:border-vati-border-strong hover:text-vati-text"
        @click="$emit('search')"
      >
        <span aria-hidden="true" class="lucide-search size-4" />
        <span class="hidden 2xl:inline">{{ searchLabel }}</span>
        <span class="hidden sm:inline lg:hidden xl:inline 2xl:hidden">Search</span>
        <span class="vati-sr-only sm:hidden lg:inline xl:hidden">Search</span>
        <kbd
          aria-hidden="true"
          class="hidden rounded border border-vati-border px-1 text-xs text-vati-muted xl:inline"
        >
          {{ isMac ? '⌘K' : 'Ctrl K' }}
        </kbd>
      </button>
      <slot name="actions" />
      <slot name="account" />
    </div>
  </header>
</template>
