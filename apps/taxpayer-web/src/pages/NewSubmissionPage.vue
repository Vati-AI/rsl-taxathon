<script setup lang="ts">
import { Alert, Button, FormControl } from 'frappe-ui';
import { computed, nextTick, reactive, ref } from 'vue';
import PortalHeader from '../components/PortalHeader.vue';
import { formatAmount, formatPeriod } from '../lib/format';
import {
  emptyDraft,
  normaliseAmount,
  TAX_TYPES,
  validateDraft,
  type DraftErrors,
} from '../lib/validation';

const draft = reactive(emptyDraft());
const errors = ref<DraftErrors>({});
const step = ref<'details' | 'review'>('details');
const form = ref<HTMLFormElement | null>(null);
const errorCount = computed(() => Object.keys(errors.value).length);
const taxTypeOptions = [
  { label: 'Choose a type', value: '' },
  ...TAX_TYPES.map((value) => ({ label: value, value })),
];

const NOT_AVAILABLE =
  'Sending is not available yet: the Submission record is added in TAX-031. Your answers stay on this page only.';

async function review() {
  errors.value = validateDraft(draft);
  if (errorCount.value > 0) {
    // Keep entered values; move focus to the first field that needs attention.
    await nextTick();
    form.value?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
    return;
  }
  step.value = 'review';
  await nextTick();
  document.getElementById('review-title')?.focus();
}

async function edit() {
  step.value = 'details';
  await nextTick();
  document.getElementById('details-title')?.focus();
}
</script>

<template>
  <section class="max-w-2xl">
    <PortalHeader title="New submission" description="Step-by-step return or claim." />

    <ol class="mb-6 flex gap-4 text-sm" aria-label="Progress">
      <li
        :aria-current="step === 'details' ? 'step' : undefined"
        :class="step === 'details' ? 'font-semibold text-ink-gray-9' : 'text-ink-gray-6'"
      >
        1. Your details
      </li>
      <li
        :aria-current="step === 'review' ? 'step' : undefined"
        :class="step === 'review' ? 'font-semibold text-ink-gray-9' : 'text-ink-gray-6'"
      >
        2. Review
      </li>
    </ol>

    <form
      v-if="step === 'details'"
      ref="form"
      novalidate
      class="space-y-6"
      @submit.prevent="review"
    >
      <h2 id="details-title" tabindex="-1" class="text-lg font-semibold text-ink-gray-9">
        Your details
      </h2>

      <Alert
        v-if="errorCount > 0"
        theme="red"
        role="alert"
        :title="
          errorCount === 1 ? 'Fix 1 field to continue' : `Fix ${errorCount} fields to continue`
        "
        description="Your other answers have been kept."
      />

      <FormControl
        v-model="draft.taxType"
        type="select"
        size="lg"
        variant="outline"
        label="Type of return or claim"
        required
        :options="taxTypeOptions"
        :error="errors.taxType"
      />
      <FormControl
        v-model="draft.period"
        type="text"
        size="lg"
        variant="outline"
        label="Tax period"
        description="Month the return covers, as YYYY-MM."
        placeholder="2026-09"
        autocomplete="off"
        required
        :error="errors.period"
      />
      <FormControl
        v-model="draft.amount"
        type="text"
        size="lg"
        variant="outline"
        inputmode="decimal"
        label="Amount (LSL)"
        description="Total payable or claimed, for example 15000.00."
        required
        :error="errors.amount"
      />
      <FormControl
        v-model="draft.contactEmail"
        type="email"
        size="lg"
        variant="outline"
        autocomplete="email"
        label="Contact email (optional)"
        description="Where we can reach you about this submission."
        :error="errors.contactEmail"
      />
      <FormControl
        v-model="draft.declaration"
        type="checkbox"
        label="I declare that the information I have given is true and complete."
        :error="errors.declaration"
      />

      <div class="flex flex-wrap gap-3">
        <Button type="submit" variant="solid" size="lg" label="Continue to review" />
        <Button size="lg" label="Cancel" :route="{ name: 'submissions' }" />
      </div>
    </form>

    <section v-else aria-labelledby="review-title" class="space-y-6">
      <h2 id="review-title" tabindex="-1" class="text-lg font-semibold text-ink-gray-9">
        Check your answers
      </h2>
      <dl
        class="grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)] gap-x-4 gap-y-3 rounded-lg border border-outline-gray-2 p-5"
      >
        <dt class="text-ink-gray-6">Type</dt>
        <dd class="text-ink-gray-9">{{ draft.taxType }}</dd>
        <dt class="text-ink-gray-6">Tax period</dt>
        <dd class="text-ink-gray-9">{{ formatPeriod(draft.period) }}</dd>
        <dt class="text-ink-gray-6">Amount</dt>
        <dd class="tabular-nums text-ink-gray-9">
          {{ formatAmount(normaliseAmount(draft.amount), 'LSL') }}
        </dd>
        <dt class="text-ink-gray-6">Contact email</dt>
        <dd class="break-words text-ink-gray-9">{{ draft.contactEmail || 'Not provided' }}</dd>
      </dl>

      <Alert theme="amber" title="Sending is not available yet" :description="NOT_AVAILABLE" />

      <div class="flex flex-wrap items-center gap-3">
        <Button size="lg" label="Change answers" icon-left="lucide-arrow-left" @click="edit" />
        <span class="inline-flex" :title="NOT_AVAILABLE">
          <Button
            variant="solid"
            size="lg"
            label="Send submission"
            disabled
            aria-describedby="send-unavailable"
          />
        </span>
        <p id="send-unavailable" class="sr-only">{{ NOT_AVAILABLE }}</p>
      </div>
    </section>
  </section>
</template>
