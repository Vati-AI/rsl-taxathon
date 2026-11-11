<script setup lang="ts">
import { Button } from 'frappe-ui';
import type { Finding, ReviewPermission } from '../api/review';
import { PERMISSION_REASONS, SUGGESTIONS, outcomeAvailability } from '../domain/review';
import type { CaseStatus } from '../api/vati';
import FindingCard from './FindingCard.vue';

/**
 * Agent and rule suggestions shown for consideration only. "Use as a starting
 * point" fills in a form; it never sends anything. The officer still writes the
 * rationale and confirms (no irreversible action from a suggestion).
 */
const props = defineProps<{
  findings: Finding[];
  allowed: readonly ReviewPermission[];
  caseStatus: CaseStatus;
  /**
   * Why a new decision cannot be proposed (the case is resolved, or another decision
   * is waiting for approval), or null when it can.
   */
  proposeBlocked: string | null;
}>();
const emit = defineEmits<{ start: [finding: Finding] }>();

function blocked(finding: Finding): string | null {
  const start = SUGGESTIONS[finding.proposed_action.type as keyof typeof SUGGESTIONS]?.start;
  if (!start) return 'Discuss this with a supervisor; there is no form for it here.';
  if (start.form === 'evidence')
    return props.allowed.includes('evidence_request.authorise')
      ? null
      : PERMISSION_REASONS['evidence_request.authorise'];
  if (!props.allowed.includes('decision.propose')) return PERMISSION_REASONS['decision.propose'];
  if (props.proposeBlocked) return props.proposeBlocked;
  return outcomeAvailability(start.outcome, props.caseStatus, props.allowed).reason;
}
</script>

<template>
  <div class="space-y-4">
    <p class="text-sm text-ink-gray-5">
      Suggestions never act on their own. Using one only fills in a form; you write the rationale
      and confirm before anything is recorded.
    </p>
    <ul class="space-y-3">
      <li
        v-for="finding in findings"
        :key="finding.finding_id"
        class="space-y-2 border-t border-outline-gray-1 pt-3 first:border-t-0 first:pt-0"
      >
        <FindingCard :finding="finding" heading-level="h4" compact />
        <p class="text-sm text-ink-gray-7">
          <span class="font-medium">Suggested next step:</span>
          {{ SUGGESTIONS[finding.proposed_action.type as keyof typeof SUGGESTIONS]?.label }}
        </p>
        <div class="flex flex-wrap items-center gap-2">
          <Button
            label="Use as a starting point"
            icon-left="lucide-clipboard-pen"
            :disabled="blocked(finding) !== null"
            :aria-describedby="
              blocked(finding) ? `suggestion-${finding.finding_id}-reason` : undefined
            "
            @click="emit('start', finding)"
          />
          <span
            v-if="blocked(finding)"
            :id="`suggestion-${finding.finding_id}-reason`"
            class="text-sm text-ink-gray-5"
          >
            {{ blocked(finding) }}
          </span>
        </div>
      </li>
    </ul>
  </div>
</template>
