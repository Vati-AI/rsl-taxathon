<script setup lang="ts">
/**
 * Confirmation dialog (TAX-145) for consequential actions. Says what will happen in
 * plain words, names the action on the confirm button ("Send request", never "OK"),
 * and keeps the dialog open with a loading button until the action finishes. A danger
 * tone is for irreversible actions only. Built on frappe-ui Dialog.
 */
import { Button, Dialog } from 'frappe-ui';

withDefaults(
  defineProps<{
    title: string;
    message: string;
    confirmLabel: string;
    cancelLabel?: string;
    tone?: 'default' | 'danger';
    loading?: boolean;
  }>(),
  { cancelLabel: 'Cancel', tone: 'default', loading: false },
);
const open = defineModel<boolean>('open', { default: false });
const emit = defineEmits<{ confirm: []; cancel: [] }>();

function cancel() {
  open.value = false;
  emit('cancel');
}
</script>

<template>
  <Dialog
    v-model:open="open"
    :title="title"
    :message="message"
    :icon="tone === 'danger' ? 'lucide-triangle-alert' : undefined"
    :theme="tone === 'danger' ? 'red' : undefined"
    size="md"
    :dismissible="!loading"
  >
    <template #actions>
      <div data-kit="confirm-dialog" class="flex w-full flex-wrap justify-end gap-2">
        <Button :label="cancelLabel" :disabled="loading" @click="cancel" />
        <Button
          variant="solid"
          :theme="tone === 'danger' ? 'red' : 'blue'"
          :label="confirmLabel"
          :loading="loading"
          @click="emit('confirm')"
        />
      </div>
    </template>
  </Dialog>
</template>
