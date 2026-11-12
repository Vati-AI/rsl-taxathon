<script setup lang="ts">
/**
 * "From your earlier claims" (TAX-078): reminders from reviewed omissions in
 * this taxpayer's earlier claims. Guidance only — it never blocks a claim and
 * never shows decisions, risk or staff details. Renders nothing when there are
 * no reminders, so first-time claimants see no extra panel.
 */
import { Alert, Button } from 'frappe-ui';
import { reminderDateLabel } from '../lib/reminders';
import type { CorrectionReminder } from '../lib/types';

withDefaults(
  defineProps<{
    reminders: readonly CorrectionReminder[];
    error: string | null;
    /** TAX-126: shown on an evidence response as well as on a claim. */
    context?: 'claim' | 'response';
  }>(),
  { context: 'claim' },
);
const emit = defineEmits<{ retry: [] }>();
</script>

<template>
  <Alert
    v-if="error"
    theme="gray"
    title="Reminders from your earlier claims could not be loaded"
    :description="error"
  >
    <template #actions>
      <Button label="Try again" @click="emit('retry')" />
    </template>
  </Alert>

  <!-- TAX-126: on an evidence response, a disclosure that keeps the steps in view. -->
  <details
    v-else-if="reminders.length && context === 'response'"
    class="rounded-lg border border-outline-gray-2 p-4"
    data-kit="response-reminders"
  >
    <summary class="cursor-pointer font-semibold text-ink-gray-9">
      {{
        reminders.length === 1
          ? '1 reminder from your earlier claims'
          : `${reminders.length} reminders from your earlier claims`
      }}
    </summary>
    <section aria-labelledby="reminders-title" class="mt-3 space-y-3">
      <h3 id="reminders-title" class="vati-sr-only">From your earlier claims</h3>
      <p class="text-sm text-ink-gray-6">
        When your earlier claims were reviewed, these details were missing or needed correcting.
        They may help you choose the documents for this request. This is guidance, not a decision
        about your claim.
      </p>
      <ul class="space-y-2" aria-label="Reminders from your earlier claims">
        <li
          v-for="reminder in reminders"
          :key="reminder.reminder_id"
          :data-reminder="reminder.topic"
          class="rounded-md bg-surface-gray-1 p-3"
        >
          <p class="font-medium text-ink-gray-9">{{ reminder.title }}</p>
          <p class="mt-1 break-words text-ink-gray-8">{{ reminder.explanation }}</p>
          <p class="mt-1 text-sm text-ink-gray-6">{{ reminderDateLabel(reminder) }}</p>
        </li>
      </ul>
    </section>
  </details>

  <section
    v-else-if="reminders.length"
    aria-labelledby="reminders-title"
    class="space-y-3 rounded-lg border border-outline-gray-2 p-5"
  >
    <h3 id="reminders-title" class="text-base font-semibold text-ink-gray-9">
      From your earlier claims
    </h3>
    <p class="text-sm text-ink-gray-6">
      When your earlier claims were reviewed, these details were missing or needed correcting. Check
      them before you send this claim. This is guidance, not a decision about this claim.
    </p>
    <ul class="space-y-2" aria-label="Reminders from your earlier claims">
      <li
        v-for="reminder in reminders"
        :key="reminder.reminder_id"
        :data-reminder="reminder.topic"
        class="rounded-md bg-surface-gray-1 p-3"
      >
        <p class="font-medium text-ink-gray-9">{{ reminder.title }}</p>
        <p class="mt-1 break-words text-ink-gray-8">{{ reminder.explanation }}</p>
        <p class="mt-1 text-sm text-ink-gray-6">{{ reminderDateLabel(reminder) }}</p>
      </li>
    </ul>
    <p class="text-sm text-ink-gray-6">
      If a reminder does not look right, tell the officer handling your claim. They can correct or
      withdraw it.
    </p>
  </section>
</template>
