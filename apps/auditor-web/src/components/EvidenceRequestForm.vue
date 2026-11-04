<script setup lang="ts">
import { computed, reactive, watch } from 'vue';
import { Button, Checkbox, Select, TextInput, Textarea } from 'frappe-ui';
import { DOCUMENT_TYPES, type DocumentType, type Finding, type RequestedItem } from '../api/review';
import { formatKey } from '../domain/cases';
import { MESSAGE_MAX, isoDateAfter } from '../domain/review';

export interface EvidenceDraft {
  finding_ids: string[];
  items: RequestedItem[];
  due_date: string;
  message_to_taxpayer: string;
}

/**
 * An officer-authorised evidence request to the taxpayer (requestEvidence). It is
 * recorded in Frappe and delivered through the portal later (TAX-045); it does
 * not move the case. Submitting only opens the confirmation step.
 */
const props = defineProps<{
  findings: Finding[];
  prefill: { findingIds: string[]; nonce: number } | null;
  resetToken: number;
}>();
const emit = defineEmits<{ request: [draft: EvidenceDraft] }>();

const MAX_ITEMS = 20;
const blankItem = () => ({ document_type: 'other' as DocumentType, description: '' });
const form = reactive({
  cited: {} as Record<string, boolean>,
  items: [blankItem()],
  due: isoDateAfter(14),
  message: '',
});
const errors = reactive({ cited: '', items: '', due: '', message: '' });
const typeOptions = DOCUMENT_TYPES.map((t) => ({ label: formatKey(t), value: t }));
const citedIds = computed(() =>
  props.findings.filter((f) => form.cited[f.finding_id]).map((f) => f.finding_id),
);

function clearErrors() {
  Object.assign(errors, { cited: '', items: '', due: '', message: '' });
}

watch(
  () => props.prefill?.nonce,
  () => {
    if (!props.prefill) return;
    for (const id of props.prefill.findingIds) form.cited[id] = true;
    clearErrors();
  },
);
watch(
  () => props.resetToken,
  () => {
    Object.assign(form, { cited: {}, items: [blankItem()], due: isoDateAfter(14), message: '' });
    clearErrors();
  },
);

function submit() {
  clearErrors();
  if (citedIds.value.length === 0)
    errors.cited = 'Cite at least one finding that justifies the request.';
  if (form.items.some((item) => !item.description.trim()))
    errors.items = 'Describe every requested document.';
  const tomorrow = isoDateAfter(1);
  const latest = isoDateAfter(180);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.due)) errors.due = 'Enter a due date.';
  else if (form.due < tomorrow) errors.due = 'Choose a date after today.';
  else if (form.due > latest) errors.due = 'Choose a date at most 180 days from today.';
  if (!form.message.trim()) errors.message = 'Write the message the taxpayer will see.';
  const first = (['cited', 'items', 'due', 'message'] as const).find((k) => errors[k]);
  if (first) {
    document.getElementById(`evidence-${first}`)?.focus();
    return;
  }
  emit('request', {
    finding_ids: citedIds.value,
    items: form.items.map((item) => ({
      document_type: item.document_type,
      description: item.description.trim(),
    })),
    due_date: form.due,
    message_to_taxpayer: form.message.trim(),
  });
}
</script>

<template>
  <form class="space-y-5" novalidate aria-label="Request evidence" @submit.prevent="submit">
    <fieldset class="space-y-2">
      <legend
        id="evidence-cited"
        tabindex="-1"
        class="mb-1 font-medium text-ink-gray-8 outline-none"
      >
        Findings that justify the request
      </legend>
      <p v-if="!findings.length" class="text-sm text-ink-gray-5">
        This case has no findings yet, so no evidence request can be justified.
      </p>
      <Checkbox
        v-for="finding in findings"
        :key="finding.finding_id"
        v-model="form.cited[finding.finding_id]"
        :label="finding.explanation"
      />
      <p v-if="errors.cited" class="text-sm text-ink-red-7">{{ errors.cited }}</p>
    </fieldset>

    <fieldset class="space-y-3">
      <legend
        id="evidence-items"
        tabindex="-1"
        class="mb-1 font-medium text-ink-gray-8 outline-none"
      >
        Documents requested
      </legend>
      <div
        v-for="(item, index) in form.items"
        :key="index"
        class="grid gap-3 rounded-control border border-outline-gray-1 p-3 sm:grid-cols-[12rem_minmax(0,1fr)_auto] sm:items-end"
      >
        <Select
          v-model="item.document_type"
          :label="`Document ${index + 1} type`"
          :options="typeOptions"
        />
        <TextInput
          v-model="item.description"
          :label="`Document ${index + 1} description`"
          maxlength="1000"
        />
        <Button
          v-if="form.items.length > 1"
          :label="`Remove document ${index + 1}`"
          icon="lucide-trash-2"
          variant="ghost"
          @click="form.items.splice(index, 1)"
        />
      </div>
      <p v-if="errors.items" class="text-sm text-ink-red-7">{{ errors.items }}</p>
      <Button
        v-if="form.items.length < MAX_ITEMS"
        label="Add another document"
        icon-left="lucide-plus"
        @click="form.items.push(blankItem())"
      />
    </fieldset>

    <div class="max-w-xs">
      <TextInput
        id="evidence-due"
        v-model="form.due"
        type="date"
        label="Due date"
        :min="isoDateAfter(1)"
        :max="isoDateAfter(180)"
        :error="errors.due"
      />
    </div>

    <Textarea
      id="evidence-message"
      v-model="form.message"
      label="Message to the taxpayer"
      :rows="3"
      :maxlength="MESSAGE_MAX"
      :error="errors.message"
      description="Plain language. Do not mention suspicions or risk scores."
    />

    <div class="flex flex-wrap items-center gap-3">
      <Button type="submit" variant="solid" theme="blue" label="Review request…" />
      <span class="text-sm text-ink-gray-5">You confirm the exact request in the next step.</span>
    </div>
  </form>
</template>
