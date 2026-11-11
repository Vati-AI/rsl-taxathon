<script setup lang="ts">
/**
 * Workspace header (TAX-145; used by the TAX-146 shell): one bar across every Vati
 * Workspace app. Product mark and name, the app tabs (Cases, Explore, Reports, Admin),
 * a search button that opens the command palette (⌘K / Ctrl K), and the account area.
 * Fixture mode shows as a slim strip here, never as a full-width banner.
 *
 * Slots: `account` (user menu), `actions` (Ask Vati, Support).
 */
import type { AppLink } from './types';

withDefaults(
  defineProps<{
    /** Product name shown beside the mark. */
    product?: string;
    apps?: AppLink[];
    /** Show the fixture-mode strip. */
    fixture?: boolean;
    /** Show the search button (needs a command palette on the page). */
    search?: boolean;
  }>(),
  { product: 'Vati Workspace', apps: () => [], fixture: false, search: true },
);
defineEmits<{ search: [] }>();

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
</script>

<template>
  <header
    data-kit="workspace-header"
    class="flex h-vati-header items-center gap-3 border-b border-vati-border bg-vati-surface px-3 sm:px-4"
  >
    <a
      href="/"
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
    </a>

    <nav v-if="apps.length" aria-label="Workspace apps" class="hidden min-w-0 lg:block">
      <ul class="flex items-center gap-1">
        <li v-for="app in apps" :key="app.label">
          <a
            :href="app.href"
            :aria-current="app.current ? 'page' : undefined"
            :class="[
              'inline-flex h-8 items-center rounded-control px-3 font-medium',
              app.current
                ? 'bg-vati-accent-subtle text-vati-accent'
                : 'text-vati-muted hover:bg-vati-surface-subtle hover:text-vati-text',
            ]"
          >
            {{ app.label }}
          </a>
        </li>
      </ul>
    </nav>

    <span
      v-if="fixture"
      data-kit="fixture-strip"
      class="shrink-0 rounded-full bg-vati-fixture-bg px-2 py-0.5 text-xs font-medium text-vati-fixture-text"
    >
      Synthetic data
    </span>

    <div class="ml-auto flex min-w-0 items-center gap-1 sm:gap-2">
      <button
        v-if="search"
        type="button"
        aria-keyshortcuts="Meta+K Control+K"
        class="inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-control border border-vati-border bg-vati-surface px-2.5 text-vati-muted hover:border-vati-border-strong hover:text-vati-text"
        @click="$emit('search')"
      >
        <span aria-hidden="true" class="lucide-search size-4" />
        <span class="hidden lg:inline">Search cases, TINs and pages</span>
        <span class="hidden sm:inline lg:hidden">Search</span>
        <span class="vati-sr-only sm:hidden">Search</span>
        <kbd
          aria-hidden="true"
          class="hidden rounded border border-vati-border px-1 text-xs text-vati-muted lg:inline"
        >
          {{ isMac ? '⌘K' : 'Ctrl K' }}
        </kbd>
      </button>
      <slot name="actions" />
      <slot name="account" />
    </div>
  </header>
</template>
