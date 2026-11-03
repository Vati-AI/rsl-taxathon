<script setup lang="ts">
import { computed } from 'vue';
import { Button, Spinner } from 'frappe-ui';

export type PanelKind = 'loading' | 'empty' | 'error' | 'denied' | 'not_found' | 'unavailable';

const props = defineProps<{
  kind: PanelKind;
  title: string;
  message?: string;
  retryable?: boolean;
  /** Correlation ID to quote when reporting a problem. */
  reference?: string | null;
}>();
defineEmits<{ retry: [] }>();

const icon = computed(
  () =>
    ({
      loading: '',
      empty: 'lucide-inbox',
      error: 'lucide-circle-alert',
      denied: 'lucide-lock',
      not_found: 'lucide-search-x',
      unavailable: 'lucide-plug-zap',
    })[props.kind],
);
// Errors and denials interrupt; other states are polite status updates.
const role = computed(() =>
  props.kind === 'error' || props.kind === 'denied' ? 'alert' : 'status',
);
</script>

<template>
  <div
    :role="role"
    :data-state="kind"
    class="flex flex-col items-center gap-2 rounded-surface border border-outline-gray-1 bg-surface-base px-4 py-8 text-center"
  >
    <Spinner v-if="kind === 'loading'" size="lg" class="text-ink-gray-5" />
    <span
      v-else
      :class="[icon, 'size-6', kind === 'error' ? 'text-ink-red-6' : 'text-ink-gray-5']"
      aria-hidden="true"
    />
    <p class="text-base font-medium text-ink-gray-8">{{ title }}</p>
    <p v-if="message" class="max-w-prose text-ink-gray-5">{{ message }}</p>
    <p v-if="reference" class="text-sm text-ink-gray-5">
      Reference: <span class="break-all font-mono">{{ reference }}</span>
    </p>
    <div v-if="retryable || $slots.default" class="mt-2 flex flex-wrap justify-center gap-2">
      <Button
        v-if="retryable"
        label="Try again"
        icon-left="lucide-rotate-cw"
        @click="$emit('retry')"
      />
      <slot />
    </div>
  </div>
</template>
