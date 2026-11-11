<script setup lang="ts">
/**
 * A header button with a popover (TAX-146): notifications, help and the user menu.
 * The button's accessible name is its label, visible from 640px or kept for screen
 * readers on phones. Escape and an outside click close it; focus returns to the button.
 */
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui';

withDefaults(
  defineProps<{
    label: string;
    icon: string;
    /** Show the label beside the icon from 640px. */
    showLabel?: boolean;
    /** Small count badge (unread notifications). */
    count?: number;
  }>(),
  { showLabel: false, count: undefined },
);
const open = defineModel<boolean>('open', { default: false });
</script>

<template>
  <PopoverRoot v-model:open="open">
    <PopoverTrigger
      data-kit="header-menu"
      class="relative inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-control px-2 text-vati-muted hover:bg-vati-surface-subtle hover:text-vati-text"
    >
      <span aria-hidden="true" :class="[icon, 'size-4 shrink-0']" />
      <span :class="showLabel ? 'hidden max-w-[10rem] truncate xl:inline' : 'vati-sr-only'">
        {{ label }}
      </span>
      <span v-if="showLabel" class="vati-sr-only xl:hidden">{{ label }}</span>
      <span
        v-if="count"
        class="vati-tabular absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-vati-accent px-1 text-center text-[11px] leading-4 text-white"
      >
        {{ count }}
      </span>
    </PopoverTrigger>
    <PopoverPortal>
      <PopoverContent
        align="end"
        :side-offset="6"
        class="z-50 w-72 max-w-[calc(100vw-1rem)] rounded-surface border border-vati-border bg-vati-surface p-3 text-vati-text shadow-overlay"
      >
        <slot :close="() => (open = false)" />
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
