<script setup lang="ts">
/**
 * Supporting documents for a refund claim (TAX-043): choose a document type,
 * pick a file, and it is checked and uploaded as private evidence. Each row
 * shows its own state with a retry or remove action.
 */
import { Badge, Button, FormControl } from 'frappe-ui';
import { ref, watch } from 'vue';
import {
  ACCEPT_ATTRIBUTE,
  DOCUMENT_TYPES,
  documentTypeLabel,
  FORMAT_HINT,
  formatSize,
} from '../lib/documents';
import type { DocumentUploads, UploadItem } from '../lib/uploads';

// Defaults keep the TAX-043 claim form unchanged; a page with several panels
// (TAX-045 evidence responses) passes a unique inputId and its own labels.
const props = withDefaults(
  defineProps<{
    uploads: DocumentUploads;
    error?: string;
    inputId?: string;
    fileLabel?: string;
    listLabel?: string;
    defaultType?: string;
    /** What the documents are for, used in the removal note. */
    context?: 'claim' | 'response';
  }>(),
  {
    error: undefined,
    inputId: 'document-file',
    fileLabel: 'Choose a file to upload',
    listLabel: 'Supporting documents',
    defaultType: undefined,
    context: 'claim',
  },
);

const documentType = ref<string>(
  DOCUMENT_TYPES.some((t) => t.value === props.defaultType)
    ? (props.defaultType as string)
    : DOCUMENT_TYPES[0].value,
);
const pickError = ref<string | null>(null);
const announcement = ref('');
const input = ref<HTMLInputElement | null>(null);

const typeOptions = DOCUMENT_TYPES.map((t) => ({ label: t.label, value: t.value }));

function onPick(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  target.value = ''; // allow choosing the same file again after a fix
  if (!file) return;
  pickError.value = props.uploads.add(file, documentType.value);
  announcement.value = pickError.value
    ? `${file.name} was not uploaded. ${pickError.value}`
    : `Uploading ${file.name}.`;
}

// Announce the outcome of each upload once it settles.
watch(
  () => props.uploads.items.map((i) => `${i.id}:${i.status}`).join('|'),
  (_now, before) => {
    const previous = new Map(
      (before ?? '').split('|').map((entry) => entry.split(':') as [string, string]),
    );
    for (const item of props.uploads.items) {
      const was = previous.get(item.id);
      if (was === 'uploading' && item.status === 'uploaded') {
        announcement.value = `${item.name} uploaded.`;
      } else if (was === 'uploading' && item.status === 'failed') {
        announcement.value = `${item.name} could not be uploaded. ${item.error ?? ''}`;
      }
    }
  },
);

const statusBadge: Record<
  UploadItem['status'],
  { label: string; theme: 'gray' | 'green' | 'red' }
> = {
  uploading: { label: 'Uploading…', theme: 'gray' },
  uploaded: { label: 'Uploaded', theme: 'green' },
  failed: { label: 'Not uploaded', theme: 'red' },
};

function focusPicker() {
  input.value?.focus();
}

defineExpose({ focusPicker });
</script>

<template>
  <div class="space-y-5">
    <div class="grid gap-4 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)] sm:items-end">
      <FormControl
        v-model="documentType"
        type="select"
        size="lg"
        variant="outline"
        label="Document type"
        :options="typeOptions"
      />
      <div class="min-w-0">
        <label :for="inputId" class="mb-1.5 block text-sm text-ink-gray-7">
          {{ fileLabel }}
        </label>
        <input
          :id="inputId"
          ref="input"
          type="file"
          :accept="ACCEPT_ATTRIBUTE"
          :aria-invalid="pickError || error ? 'true' : undefined"
          :aria-describedby="`${inputId}-hint ${inputId}-error`"
          class="block w-full min-w-0 rounded border border-outline-gray-2 bg-surface-white text-base text-ink-gray-8 file:mr-3 file:cursor-pointer file:border-0 file:bg-surface-gray-2 file:px-3 file:py-2 file:text-base file:text-ink-gray-8 hover:file:bg-surface-gray-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-outline-gray-4"
          @change="onPick"
        />
      </div>
    </div>
    <p :id="`${inputId}-hint`" class="text-sm text-ink-gray-6">
      {{ FORMAT_HINT }} Uploading starts as soon as you choose a file.
    </p>
    <p v-if="pickError || error" :id="`${inputId}-error`" class="text-sm text-ink-red-7">
      {{ pickError || error }}
    </p>

    <p role="status" aria-live="polite" class="sr-only">{{ announcement }}</p>

    <ul
      v-if="uploads.items.length"
      :aria-label="listLabel"
      class="divide-y divide-outline-gray-1 rounded-lg border border-outline-gray-2"
    >
      <li
        v-for="item in uploads.items"
        :key="item.id"
        class="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between"
      >
        <div class="min-w-0 sm:flex-1">
          <p class="break-all text-base font-medium text-ink-gray-9">{{ item.name }}</p>
          <p class="mt-0.5 text-sm text-ink-gray-6">
            {{ documentTypeLabel(item.documentType) }} · {{ formatSize(item.size) }}
          </p>
          <p v-if="item.error" class="mt-1 text-sm text-ink-red-7">{{ item.error }}</p>
        </div>
        <div class="flex shrink-0 flex-wrap items-center gap-2">
          <Badge
            :label="statusBadge[item.status].label"
            :theme="statusBadge[item.status].theme"
            variant="subtle"
          />
          <!-- Visible text in the slot: frappe-ui's `label` prop would replace the aria-label. -->
          <Button
            v-if="item.status === 'failed'"
            :aria-label="`Retry ${item.name}`"
            @click="uploads.retry(item.id)"
          >
            Retry
          </Button>
          <Button
            v-if="item.status !== 'uploading'"
            :aria-label="`Remove ${item.name}`"
            @click="uploads.remove(item.id)"
          >
            Remove
          </Button>
        </div>
      </li>
    </ul>
    <p v-else class="text-sm text-ink-gray-6">No documents uploaded yet.</p>
    <p v-if="uploads.items.length" class="text-sm text-ink-gray-6">
      Removing a document leaves it out of this {{ context }}. Uploaded files are stored privately
      and are never shown to other taxpayers.
    </p>
  </div>
</template>
