<script setup lang="ts">
import { Alert, Button, Dialog } from 'frappe-ui';
import type { Refusal } from '../domain/review';

export interface SummaryRow {
  label: string;
  value: string;
}

/**
 * Explicit officer confirmation for a consequential review action. Nothing is
 * sent until Confirm is pressed; Escape or Cancel sends nothing. While the
 * request runs the dialog cannot be dismissed and Confirm cannot be pressed
 * again, so one confirmation sends one request.
 */
const props = defineProps<{
  open: boolean;
  title: string;
  rows: SummaryRow[];
  /** What happens next, e.g. "Nothing is sent to Jube until a supervisor approves." */
  consequence: string;
  confirmLabel: string;
  pending: boolean;
  refusal: Refusal | null;
}>();
const emit = defineEmits<{ confirm: []; cancel: []; reload: [] }>();

function setOpen(value: boolean) {
  if (!value && !props.pending) emit('cancel');
}
</script>

<template>
  <Dialog
    :open="open"
    :title="title"
    :dismissible="!pending"
    :show-close-button="!pending"
    @update:open="setOpen"
  >
    <dl class="grid gap-x-4 gap-y-2 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)]">
      <template v-for="row in rows" :key="row.label">
        <dt class="text-sm text-ink-gray-5">{{ row.label }}</dt>
        <dd class="whitespace-pre-line break-words text-ink-gray-8">{{ row.value }}</dd>
      </template>
    </dl>
    <p class="mt-4 text-ink-gray-6">{{ consequence }}</p>
    <div aria-live="assertive">
      <Alert
        v-if="refusal"
        theme="red"
        class="mt-4"
        :title="refusal.title"
        :description="refusal.message"
      />
    </div>
    <template #actions>
      <div class="flex flex-wrap justify-end gap-2">
        <Button
          :label="refusal ? 'Back to the form' : 'Cancel'"
          :disabled="pending"
          @click="emit('cancel')"
        />
        <Button
          v-if="refusal?.next === 'reload'"
          variant="solid"
          theme="blue"
          label="Reload case"
          icon-left="lucide-rotate-cw"
          @click="emit('reload')"
        />
        <Button
          v-else-if="!refusal || refusal.next === 'retry'"
          variant="solid"
          theme="blue"
          :label="refusal ? 'Try again' : confirmLabel"
          :loading="pending"
          @click="!pending && emit('confirm')"
        />
      </div>
    </template>
  </Dialog>
</template>
