<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Badge, Button, Textarea } from 'frappe-ui';
import type { CaseReview, Decision, Finding } from '../api/review';
import { statusInfo } from '../domain/cases';
import { formatTimestamp } from '../domain/dates';
import {
  DECISION_STATUS_LABELS,
  OUTCOME_INFO,
  PERMISSION_REASONS,
  RATIONALE_MIN,
  syncText,
} from '../domain/review';

/**
 * A proposed decision waiting for review. Approve and Reject are offered to a
 * supervisor who did not propose it; the proposer may withdraw. Each button only
 * opens the confirmation step.
 */
const props = defineProps<{
  decision: Decision;
  viewer: CaseReview['viewer'];
  findings: Finding[];
  resetToken: number;
}>();
const emit = defineEmits<{
  request: [action: 'approve' | 'reject' | 'withdraw', reason?: string];
}>();

const info = computed(() => OUTCOME_INFO[props.decision.outcome]);
const mine = computed(() => props.decision.proposed_by === props.viewer.officer_id);
const canApprove = computed(() => props.viewer.allowed_actions.includes('decision.approve'));
const cited = computed(() =>
  props.findings.filter((f) => props.decision.finding_ids.includes(f.finding_id)),
);
const sync = computed(() => syncText(props.decision));
const rejecting = ref(false);
const reason = ref('');
const reasonError = ref('');

watch(
  () => props.resetToken,
  () => {
    rejecting.value = false;
    reason.value = '';
    reasonError.value = '';
  },
);

function startReject() {
  rejecting.value = true;
  reasonError.value = '';
  // Let the field render, then move focus into it.
  setTimeout(() => document.getElementById('reject-reason')?.focus(), 0);
}

function reject() {
  if (reason.value.trim().length < RATIONALE_MIN) {
    reasonError.value = `Give the reason for rejecting (at least ${RATIONALE_MIN} characters).`;
    document.getElementById('reject-reason')?.focus();
    return;
  }
  emit('request', 'reject', reason.value.trim());
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-2">
      <Badge theme="violet" :label="DECISION_STATUS_LABELS[decision.status]" />
      <span class="text-sm text-ink-gray-5">
        Proposed {{ formatTimestamp(decision.proposed_at) }} by
        {{ mine ? 'you' : 'another officer' }}
      </span>
    </div>
    <div>
      <p class="text-lg font-semibold text-ink-gray-9">{{ info.label }}</p>
      <p class="text-ink-gray-6">{{ info.description }}</p>
    </div>
    <dl class="grid gap-x-4 gap-y-2 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)]">
      <dt class="text-sm text-ink-gray-5">Rationale</dt>
      <dd class="whitespace-pre-line break-words text-ink-gray-8">{{ decision.rationale }}</dd>
      <dt class="text-sm text-ink-gray-5">Findings cited</dt>
      <dd class="text-ink-gray-8">
        <ul v-if="cited.length" class="list-disc space-y-1 pl-5">
          <li v-for="f in cited" :key="f.finding_id" class="break-words">{{ f.explanation }}</li>
        </ul>
        <span v-else>None</span>
      </dd>
      <dt class="text-sm text-ink-gray-5">Investigator rating</dt>
      <dd class="text-ink-gray-8">{{ decision.rating }} of 5</dd>
      <dt class="text-sm text-ink-gray-5">Case status seen</dt>
      <dd class="text-ink-gray-8">{{ statusInfo(decision.case_status_at_proposal).label }}</dd>
      <dt class="text-sm text-ink-gray-5">Investigation</dt>
      <dd :class="sync.tone === 'red' ? 'text-ink-red-7' : 'text-ink-gray-8'">{{ sync.text }}</dd>
    </dl>

    <div
      v-if="decision.status === 'proposed'"
      class="space-y-3 border-t border-outline-gray-1 pt-4"
    >
      <div v-if="rejecting && canApprove" class="space-y-3">
        <Textarea
          id="reject-reason"
          v-model="reason"
          label="Reason for rejecting"
          :rows="3"
          :error="reasonError"
          description="The proposing officer and the review trail see this reason."
        />
        <div class="flex flex-wrap gap-2">
          <Button variant="solid" theme="red" label="Review rejection…" @click="reject" />
          <Button label="Back" @click="rejecting = false" />
        </div>
      </div>
      <div v-else class="flex flex-wrap items-center gap-2">
        <Button
          variant="solid"
          theme="blue"
          label="Approve…"
          :disabled="!canApprove"
          aria-describedby="approve-reason"
          @click="emit('request', 'approve')"
        />
        <Button
          label="Reject…"
          :disabled="!canApprove"
          aria-describedby="approve-reason"
          @click="startReject"
        />
        <Button v-if="mine" label="Withdraw…" @click="emit('request', 'withdraw')" />
        <span v-if="!canApprove" id="approve-reason" class="text-sm text-ink-gray-5">
          {{
            mine
              ? 'You proposed this decision, so a different supervisor must approve or reject it.'
              : PERMISSION_REASONS['decision.approve']
          }}
        </span>
      </div>
    </div>
  </div>
</template>
