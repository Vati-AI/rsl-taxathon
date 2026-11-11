<script setup lang="ts">
import { computed, reactive, watch } from 'vue';
import { Button, Checkbox, Select, Textarea } from 'frappe-ui';
import { OUTCOMES, type DecisionOutcome, type Finding, type ReviewPermission } from '../api/review';
import type { CaseStatus } from '../api/vati';
import {
  OUTCOME_GROUPS,
  OUTCOME_INFO,
  RATIONALE_MAX,
  RATIONALE_MIN,
  outcomeAvailability,
} from '../domain/review';

export interface DecisionDraft {
  outcome: DecisionOutcome;
  rationale: string;
  rating: number;
  finding_ids: string[];
}

/**
 * The officer's own proposal. Submitting only opens the confirmation step; a
 * prefill from an agent suggestion fills fields and never submits.
 */
const props = defineProps<{
  caseStatus: CaseStatus;
  allowed: readonly ReviewPermission[];
  findings: Finding[];
  prefill: { outcome: DecisionOutcome; findingIds: string[]; nonce: number } | null;
  resetToken: number;
}>();
const emit = defineEmits<{ request: [draft: DecisionDraft] }>();

const form = reactive({
  outcome: '' as DecisionOutcome | '',
  rationale: '',
  rating: '3',
  cited: {} as Record<string, boolean>,
});
const errors = reactive({ outcome: '', rationale: '', cited: '' });

const options = computed(() =>
  OUTCOME_GROUPS.map((g) => ({
    ...g,
    items: OUTCOMES.filter((o) => OUTCOME_INFO[o].group === g.group).map((o) => ({
      value: o,
      info: OUTCOME_INFO[o],
      ...outcomeAvailability(o, props.caseStatus, props.allowed),
    })),
  })),
);
const citedIds = computed(() =>
  props.findings.filter((f) => form.cited[f.finding_id]).map((f) => f.finding_id),
);
const length = computed(() => form.rationale.trim().length);

function clearErrors() {
  Object.assign(errors, { outcome: '', rationale: '', cited: '' });
}

watch(
  () => props.prefill?.nonce,
  () => {
    if (!props.prefill) return;
    form.outcome = props.prefill.outcome;
    for (const id of props.prefill.findingIds) form.cited[id] = true;
    clearErrors();
  },
  // The form mounts inside a drawer opened by the suggestion (TAX-148).
  { immediate: true },
);
watch(
  () => props.resetToken,
  () => {
    Object.assign(form, { outcome: '', rationale: '', rating: '3', cited: {} });
    clearErrors();
  },
);

function submit() {
  clearErrors();
  const outcome = form.outcome;
  if (!outcome) errors.outcome = 'Choose an outcome.';
  else if (!outcomeAvailability(outcome, props.caseStatus, props.allowed).available)
    errors.outcome = 'This outcome is not available for this case.';
  if (length.value < RATIONALE_MIN)
    errors.rationale = `Explain the decision in your own words (at least ${RATIONALE_MIN} characters).`;
  if (outcome && OUTCOME_INFO[outcome].closes && citedIds.value.length === 0)
    errors.cited = 'A clearance must cite at least one finding.';
  if (errors.outcome || errors.rationale || errors.cited || !outcome) {
    // Move focus to the first problem so keyboard and screen reader users hear it.
    const first = errors.outcome
      ? 'outcome-legend'
      : errors.rationale
        ? 'decision-rationale'
        : 'cited-legend';
    document.getElementById(first)?.focus();
    return;
  }
  emit('request', {
    outcome,
    rationale: form.rationale.trim(),
    rating: Number(form.rating),
    finding_ids: citedIds.value,
  });
}
</script>

<template>
  <form class="space-y-5" novalidate aria-label="Propose a decision" @submit.prevent="submit">
    <fieldset class="space-y-3" :aria-describedby="errors.outcome ? 'outcome-error' : undefined">
      <legend
        id="outcome-legend"
        tabindex="-1"
        class="mb-1 font-medium text-ink-gray-8 outline-none"
      >
        Outcome
      </legend>
      <div v-for="group in options" :key="group.group" class="space-y-2">
        <p class="text-sm font-medium text-ink-gray-5">{{ group.label }}</p>
        <label
          v-for="item in group.items"
          :key="item.value"
          :class="[
            'flex items-start gap-3 rounded-control border px-3 py-2',
            form.outcome === item.value
              ? 'border-outline-gray-4 bg-surface-gray-1'
              : 'border-outline-gray-1',
            item.available ? 'cursor-pointer' : 'cursor-not-allowed opacity-70',
          ]"
        >
          <input
            v-model="form.outcome"
            type="radio"
            name="decision-outcome"
            class="mt-1"
            :value="item.value"
            :disabled="!item.available"
            :aria-describedby="`outcome-${item.value}-desc`"
          />
          <span class="min-w-0">
            <span class="block text-ink-gray-8">{{ item.info.label }}</span>
            <span :id="`outcome-${item.value}-desc`" class="block text-sm text-ink-gray-5">
              {{ item.available ? item.info.description : item.reason }}
            </span>
          </span>
        </label>
      </div>
      <p v-if="errors.outcome" id="outcome-error" class="text-sm text-ink-red-7">
        {{ errors.outcome }}
      </p>
    </fieldset>

    <div>
      <Textarea
        id="decision-rationale"
        v-model="form.rationale"
        label="Rationale in your own words"
        :rows="4"
        :maxlength="RATIONALE_MAX"
        :error="errors.rationale"
        :description="`${length} of at least ${RATIONALE_MIN} characters. The supervisor and the review trail see this text.`"
      />
    </div>

    <fieldset class="space-y-2" :aria-describedby="errors.cited ? 'cited-error' : undefined">
      <legend id="cited-legend" tabindex="-1" class="mb-1 font-medium text-ink-gray-8 outline-none">
        Findings this decision relies on
      </legend>
      <p v-if="!findings.length" class="text-sm text-ink-gray-5">
        This case has no findings yet. Clearance needs at least one cited finding.
      </p>
      <Checkbox
        v-for="finding in findings"
        :key="finding.finding_id"
        v-model="form.cited[finding.finding_id]"
        :label="finding.explanation"
      />
      <p v-if="errors.cited" id="cited-error" class="text-sm text-ink-red-7">{{ errors.cited }}</p>
    </fieldset>

    <div class="max-w-xs">
      <Select
        v-model="form.rating"
        label="Investigator rating (1–5)"
        description="Recorded in Investigation with the case update. It is not a risk verdict."
        :options="['1', '2', '3', '4', '5'].map((v) => ({ label: v, value: v }))"
      />
    </div>

    <div class="flex flex-wrap items-center gap-3">
      <Button type="submit" variant="solid" theme="blue" label="Review and propose…" />
      <span class="text-sm text-ink-gray-5">You confirm the exact decision in the next step.</span>
    </div>
  </form>
</template>
