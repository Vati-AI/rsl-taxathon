<script setup lang="ts">
/**
 * Inline error with retry (TAX-145). Says what failed and what to do; never echoes
 * server text. A correlation reference can be quoted when reporting the problem.
 */
import { Button } from 'frappe-ui';

withDefaults(
  defineProps<{
    title: string;
    message?: string;
    /** Show "Try again". */
    retryable?: boolean;
    retrying?: boolean;
    reference?: string | null;
  }>(),
  { message: undefined, retryable: true, retrying: false, reference: null },
);
defineEmits<{ retry: [] }>();
</script>

<template>
  <div
    data-kit="inline-error"
    role="alert"
    class="flex flex-wrap items-start gap-3 rounded-surface border border-vati-danger-border bg-vati-danger-bg px-4 py-3"
  >
    <span
      aria-hidden="true"
      class="lucide-circle-alert mt-0.5 size-4 shrink-0 text-vati-danger-text"
    />
    <div class="min-w-0 flex-1 basis-56">
      <p class="font-semibold text-vati-danger-text">{{ title }}</p>
      <p v-if="message" class="text-vati-danger-text">{{ message }}</p>
      <p v-if="reference" class="mt-1 text-sm text-vati-danger-text">
        Reference: <span class="break-all font-mono">{{ reference }}</span>
      </p>
    </div>
    <Button
      v-if="retryable"
      label="Try again"
      icon-left="lucide-rotate-cw"
      :loading="retrying"
      @click="$emit('retry')"
    />
  </div>
</template>
