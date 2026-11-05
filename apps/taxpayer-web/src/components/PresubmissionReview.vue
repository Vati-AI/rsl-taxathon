<script setup lang="ts">
/**
 * "Before you send" panel (TAX-044): figures and correction prompts from the
 * pre-submission reconciliation. Guidance only — it never blocks sending and
 * never shows lanes, risk or decisions.
 *
 * TAX-078: a prompt the taxpayer was already reminded about after an earlier
 * claim is labelled "Also noted on an earlier claim".
 */
import { Alert, Badge, Button, LoadingText } from 'frappe-ui';
import { formatAmount } from '../lib/format';
import { reminderForPrompt } from '../lib/reminders';
import type { CorrectionReminder, PresubmissionReview } from '../lib/types';

withDefaults(
  defineProps<{
    review: PresubmissionReview | null;
    loading: boolean;
    error: string | null;
    reminders?: readonly CorrectionReminder[];
  }>(),
  { reminders: () => [] },
);
const emit = defineEmits<{ fix: [target: 'details' | 'invoices']; retry: [] }>();

const FIGURES = [
  ['claimed_refund', 'Amount claimed'],
  ['documented_input_vat', 'VAT on the invoices you listed'],
  ['explained_amount', 'Supported by the documents you added'],
  ['unresolved_amount', 'Not yet supported'],
] as const;
</script>

<template>
  <section
    aria-labelledby="checks-title"
    class="space-y-4 rounded-lg border border-outline-gray-2 p-5"
  >
    <h3 id="checks-title" class="text-base font-semibold text-ink-gray-9">Before you send</h3>
    <p class="text-sm text-ink-gray-6">
      These checks help you spot gaps before you send. They are not a decision about your claim, and
      you can still send it.
    </p>

    <div v-if="loading" role="status">
      <LoadingText text="Checking your figures…" />
    </div>

    <Alert v-else-if="error" theme="amber" title="The checks could not be run" :description="error">
      <template #actions>
        <Button label="Try again" @click="emit('retry')" />
      </template>
    </Alert>

    <template v-else-if="review">
      <dl class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2 sm:max-w-md">
        <template v-for="[key, label] in FIGURES" :key="key">
          <dt class="text-ink-gray-6">{{ label }}</dt>
          <dd class="whitespace-nowrap text-right tabular-nums text-ink-gray-9">
            {{ formatAmount(review.figures[key] ?? null, 'LSL') }}
          </dd>
        </template>
      </dl>

      <Alert
        v-if="review.complete"
        theme="green"
        title="Your figures add up"
        description="Every invoice has the documents and payment details the checks look for."
      />
      <div v-else>
        <h4 class="mb-2 text-sm font-semibold text-ink-gray-8">Things to check</h4>
        <ul class="space-y-2" aria-label="Things to check">
          <li
            v-for="(prompt, index) in review.prompts"
            :key="`${prompt.code}-${prompt.invoice ?? 'claim'}-${index}`"
            class="flex flex-col gap-2 rounded-md bg-surface-gray-1 p-3 sm:flex-row sm:items-start sm:justify-between"
          >
            <div :id="`check-${index}`" class="space-y-1">
              <p class="text-ink-gray-8">{{ prompt.message }}</p>
              <Badge
                v-if="reminderForPrompt(prompt, reminders)"
                theme="amber"
                label="Also noted on an earlier claim"
              />
            </div>
            <!-- Visible text in the slot: frappe-ui's `label` prop would replace the aria-label. -->
            <Button
              class="shrink-0"
              :aria-describedby="`check-${index}`"
              :aria-label="
                prompt.invoice === null
                  ? 'Change the amount or period figures'
                  : `Change invoice ${prompt.invoice + 1}`
              "
              @click="emit('fix', prompt.invoice === null ? 'details' : 'invoices')"
            >
              Change
            </Button>
          </li>
        </ul>
      </div>
    </template>
  </section>
</template>
