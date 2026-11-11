<script setup lang="ts">
/**
 * Right-side drawer (TAX-145, DESIGN_STANDARD.md v2 rule 4): review actions and long
 * forms open beside the page instead of stacking below it. Modal: focus moves into the
 * drawer, Tab stays inside, Escape and the close button close it, and focus returns to
 * the control that opened it (reka-ui dialog primitive, the one frappe-ui builds on).
 * Full width below 640px.
 */
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from 'reka-ui';

const props = withDefaults(
  defineProps<{
    title: string;
    description?: string;
    /** Width on wider screens. */
    size?: 'md' | 'lg';
    /** id of the element to focus when the drawer opens (default: the first control). */
    initialFocus?: string | null;
  }>(),
  { description: undefined, size: 'md', initialFocus: null },
);
const open = defineModel<boolean>('open', { default: false });

function onOpenAutoFocus(event: Event) {
  if (!props.initialFocus) return;
  const target = document.getElementById(props.initialFocus);
  if (!target) return;
  event.preventDefault();
  target.focus();
}
</script>

<template>
  <DialogRoot v-model:open="open">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-40 bg-black/30" />
      <DialogContent
        data-kit="drawer"
        @open-auto-focus="onOpenAutoFocus"
        :class="[
          'fixed inset-y-0 right-0 z-50 flex w-full flex-col bg-vati-surface shadow-overlay focus:outline-none',
          size === 'lg' ? 'sm:w-[640px]' : 'sm:w-[480px]',
        ]"
      >
        <header class="flex items-start gap-3 border-b border-vati-border px-5 py-4">
          <div class="min-w-0 flex-1">
            <DialogTitle class="text-lg font-semibold text-vati-text">{{ title }}</DialogTitle>
            <DialogDescription v-if="description" class="mt-0.5 text-vati-muted">
              {{ description }}
            </DialogDescription>
          </div>
          <DialogClose
            aria-label="Close"
            class="-mr-1 grid size-8 shrink-0 place-items-center rounded-control text-vati-muted hover:bg-vati-surface-subtle hover:text-vati-text"
          >
            <span aria-hidden="true" class="lucide-x size-4" />
          </DialogClose>
        </header>
        <div class="relative min-h-0 flex-1 overflow-y-auto px-5 py-4" data-scroll-region>
          <slot />
        </div>
        <footer
          v-if="$slots.footer"
          class="flex flex-wrap justify-end gap-2 border-t border-vati-border px-5 py-3"
        >
          <slot name="footer" />
        </footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
