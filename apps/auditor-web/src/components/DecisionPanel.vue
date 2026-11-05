<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { Alert, Button, Skeleton } from 'frappe-ui';
import { ApiError } from '../api/errors';
import {
  getCaseReview,
  listFindings,
  proposeDecision,
  requestEvidence,
  reviewDecision,
  type CaseReview,
  type Decision,
  type DecisionOutcome,
  type Finding,
} from '../api/review';
import { newCorrelationId, type Case } from '../api/vati';
import { statusInfo } from '../domain/cases';
import { formatDate } from '../domain/dates';
import {
  OUTCOME_INFO,
  PERMISSION_REASONS,
  ROLE_LABELS,
  SUGGESTIONS,
  refusalFor,
  suggestions,
  syncText,
  type Refusal,
} from '../domain/review';
import { fixtureRole } from '../session';
import ConfirmActionDialog, { type SummaryRow } from './ConfirmActionDialog.vue';
import EvidenceRequestForm, { type EvidenceDraft } from './EvidenceRequestForm.vue';
import OpenDecisionCard from './OpenDecisionCard.vue';
import ProposeDecisionForm, { type DecisionDraft } from './ProposeDecisionForm.vue';
import ReviewTrail from './ReviewTrail.vue';
import StatePanel from './StatePanel.vue';
import SuggestionList from './SuggestionList.vue';
import { errorPanel } from './errorPanel';

/**
 * Officer decisions, evidence requests and specialist referrals for one case
 * (TAX-042). Every action goes through ConfirmActionDialog; nothing is sent from a
 * suggestion, a click on a form button or a keyboard shortcut alone. Each
 * confirmed action has one Idempotency-Key, reused if it is retried, so a retry
 * after a lost response cannot record it twice. The case version shown when the
 * action was opened is sent as expected_version, so acting on a stale view is
 * refused by the server.
 */
const props = defineProps<{ item: Case }>();
const emit = defineEmits<{ changed: [] }>();

type Action =
  | { kind: 'propose'; draft: DecisionDraft }
  | {
      kind: 'review';
      decision: Decision;
      action: 'approve' | 'reject' | 'withdraw';
      reason?: string;
    }
  | { kind: 'evidence'; draft: EvidenceDraft };

interface Loaded {
  review: CaseReview;
  findings: Finding[];
}

const data = shallowRef<Loaded | null>(null);
const loadError = shallowRef<ApiError | null>(null);
const refreshing = ref(false);
let controller: AbortController | null = null;

async function load() {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  refreshing.value = true;
  try {
    const [review, findings] = await Promise.all([
      getCaseReview(props.item.case_id, fetch, current.signal),
      listFindings(props.item.case_id, fetch, current.signal),
    ]);
    if (current.signal.aborted) return;
    data.value = { review, findings };
    loadError.value = null;
  } catch (error) {
    if (current.signal.aborted) return;
    loadError.value =
      error instanceof ApiError
        ? error
        : new ApiError('unavailable', 'Something went wrong while loading the review state.');
  } finally {
    if (controller === current) refreshing.value = false;
  }
}

// A new case or (fixture mode) a different acting officer starts from a clean slate.
watch(
  // A string key: a refreshed case object with the same ID must not reset the panel.
  () => `${props.item.case_id}|${fixtureRole.value}`,
  () => {
    data.value = null;
    result.value = null;
    void load();
  },
);
onBeforeUnmount(() => controller?.abort());
void load();

const review = computed(() => data.value?.review ?? null);
const allowed = computed(() => review.value?.viewer.allowed_actions ?? []);
const openDecision = computed(() => review.value?.open_decision ?? null);
const suggested = computed(() => suggestions(data.value?.findings ?? []));
const resolved = computed(() => props.item.status === 'RESOLVED');
/** Why no new decision can be proposed right now, or null when one can. */
const proposeBlocked = computed(() =>
  resolved.value
    ? 'This case is resolved.'
    : openDecision.value
      ? 'Another decision on this case is waiting for approval.'
      : null,
);
const viewerRoles = computed(
  () => review.value?.viewer.roles.map((r) => ROLE_LABELS[r] ?? r).join(', ') || 'Officer',
);

// --- forms, prefills and resets -------------------------------------------------------

const decisionPrefill = ref<{
  outcome: DecisionOutcome;
  findingIds: string[];
  nonce: number;
} | null>(null);
const evidencePrefill = ref<{ findingIds: string[]; nonce: number } | null>(null);
const resetDecision = ref(0);
const resetEvidence = ref(0);
let nonce = 0;

function startFrom(finding: Finding) {
  const start = SUGGESTIONS[finding.proposed_action.type as keyof typeof SUGGESTIONS]?.start;
  if (!start) return;
  if (start.form === 'decision') {
    decisionPrefill.value = {
      outcome: start.outcome,
      findingIds: [finding.finding_id],
      nonce: ++nonce,
    };
    focusLater('outcome-legend');
  } else {
    evidencePrefill.value = { findingIds: [finding.finding_id], nonce: ++nonce };
    focusLater('evidence-cited');
  }
}

function focusLater(id: string) {
  setTimeout(() => {
    const el = document.getElementById(id);
    el?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
    el?.focus({ preventScroll: true });
  }, 0);
}

// --- confirmation ---------------------------------------------------------------------

interface Pending {
  action: Action;
  /** One Idempotency-Key per confirmed action; reused if it is retried. */
  key: string;
  /** Case version the officer was looking at when they opened the action. */
  version: number;
  /** Case status (Jube projection) the officer was looking at. */
  caseStatus: Case['status'];
}

const pending = shallowRef<Pending | null>(null);
const sending = ref(false);
const refusal = ref<Refusal | null>(null);
const result = ref<{ theme: 'green' | 'blue'; title: string; description: string } | null>(null);

function open(action: Action) {
  if (!review.value) return;
  refusal.value = null;
  pending.value = {
    action,
    key: newCorrelationId(),
    version: review.value.case_version,
    caseStatus: props.item.status,
  };
}

function findingText(ids: string[]) {
  const all = data.value?.findings ?? [];
  const texts = all.filter((f) => ids.includes(f.finding_id)).map((f) => `• ${f.explanation}`);
  return texts.length ? texts.join('\n') : 'None';
}

const dialog = computed(() => {
  const p = pending.value;
  if (!p) return null;
  const caseRow: SummaryRow = {
    label: 'Case',
    value: `${props.item.display_id} · ${props.item.taxpayer.display_name}`,
  };
  const a = p.action;
  if (a.kind === 'propose') {
    const info = OUTCOME_INFO[a.draft.outcome];
    return {
      title: 'Confirm the proposed decision',
      confirmLabel: 'Propose decision',
      consequence:
        'The proposal is recorded in Frappe and waits for a supervisor who did not propose it. Nothing is sent to Jube until then.',
      rows: [
        caseRow,
        { label: 'Outcome', value: `${info.label}. ${info.description}` },
        { label: 'Rationale', value: a.draft.rationale },
        { label: 'Findings cited', value: findingText(a.draft.finding_ids) },
        { label: 'Investigator rating', value: `${a.draft.rating} of 5` },
        { label: 'Case version', value: String(p.version) },
      ],
    };
  }
  if (a.kind === 'review') {
    const info = OUTCOME_INFO[a.decision.outcome];
    const jube =
      a.decision.operation === 'escalate'
        ? 'Escalate the case for review'
        : `Move the case to ${statusInfo(a.decision.target_status).label}`;
    const rows: SummaryRow[] = [caseRow, { label: 'Decision', value: info.label }];
    if (a.action === 'approve') {
      rows.push(
        { label: 'Rationale', value: a.decision.rationale },
        { label: 'Jube update', value: jube },
      );
      return {
        title: 'Approve this decision?',
        confirmLabel: 'Approve and apply in Jube',
        consequence:
          "Approving records your approval and runs this decision's one Jube update now. It cannot be undone from this screen.",
        rows,
      };
    }
    if (a.action === 'reject') {
      rows.push({ label: 'Reason', value: a.reason ?? '' });
      return {
        title: 'Reject this decision?',
        confirmLabel: 'Reject decision',
        consequence: 'The proposal is closed with your reason. Nothing is sent to Jube.',
        rows,
      };
    }
    return {
      title: 'Withdraw your proposal?',
      confirmLabel: 'Withdraw proposal',
      consequence: 'Your proposal is closed. Nothing is sent to Jube.',
      rows,
    };
  }
  return {
    title: 'Confirm the evidence request',
    confirmLabel: 'Record evidence request',
    consequence:
      'The request is recorded in Frappe for delivery through the taxpayer portal. It does not change the case status; to wait for the response, propose “Wait for further evidence”.',
    rows: [
      caseRow,
      {
        label: 'Documents',
        value: a.draft.items
          .map((i) => `• ${i.description} (${i.document_type.replaceAll('_', ' ')})`)
          .join('\n'),
      },
      { label: 'Due date', value: formatDate(a.draft.due_date) ?? a.draft.due_date },
      { label: 'Message', value: a.draft.message_to_taxpayer },
      { label: 'Justified by', value: findingText(a.draft.finding_ids) },
    ],
  };
});

async function confirm() {
  const p = pending.value;
  if (!p || sending.value) return;
  sending.value = true;
  refusal.value = null;
  const caseId = props.item.case_id;
  try {
    const a = p.action;
    if (a.kind === 'propose') {
      await proposeDecision(
        caseId,
        {
          ...a.draft,
          evidence_ids: [],
          expected_case_status: p.caseStatus,
          expected_version: p.version,
        },
        p.key,
      );
      result.value = {
        theme: 'blue',
        title: 'Decision proposed',
        description:
          'It now waits for a supervisor who did not propose it. Nothing has been sent to Jube.',
      };
      resetDecision.value++;
    } else if (a.kind === 'review') {
      const body = {
        action: a.action,
        expected_version: p.version,
        ...(a.reason ? { reason: a.reason } : {}),
      };
      const decision = await reviewDecision(caseId, a.decision.decision_id, body, p.key);
      const titles = {
        approve: 'Decision approved',
        reject: 'Decision rejected',
        withdraw: 'Proposal withdrawn',
      };
      const sync = syncText(decision);
      result.value = {
        theme: a.action === 'approve' && sync.tone === 'green' ? 'green' : 'blue',
        title: titles[a.action],
        description: a.action === 'approve' ? sync.text : 'Nothing was sent to Jube.',
      };
      if (a.action === 'approve') emit('changed');
    } else {
      const request = await requestEvidence(
        caseId,
        { ...a.draft, expected_version: p.version },
        p.key,
      );
      result.value = {
        theme: 'green',
        title: 'Evidence request recorded',
        description: `Due ${formatDate(request.due_date) ?? request.due_date}. It will be delivered through the taxpayer portal; the case status is unchanged.`,
      };
      resetEvidence.value++;
    }
    pending.value = null;
    await load();
  } catch (error) {
    refusal.value = refusalFor(
      error instanceof ApiError ? error : new ApiError('invalid', 'Unexpected response.'),
    );
  } finally {
    sending.value = false;
  }
}

function cancel() {
  if (sending.value) return;
  pending.value = null;
  refusal.value = null;
}

function reloadAfterRefusal() {
  pending.value = null;
  refusal.value = null;
  emit('changed');
  void load();
}
</script>

<template>
  <div v-if="!data && !loadError" role="status" aria-label="Loading review state" class="space-y-3">
    <Skeleton class="h-6 w-56 rounded-control" />
    <Skeleton class="h-40 w-full rounded-surface" />
  </div>
  <StatePanel v-else-if="!data && loadError" v-bind="errorPanel(loadError)" @retry="load" />
  <div v-else-if="data && review" class="space-y-4">
    <div aria-live="polite">
      <Alert
        v-if="result"
        :theme="result.theme"
        :title="result.title"
        :description="result.description"
      />
    </div>
    <p class="text-sm text-ink-gray-5">
      Reviewing as {{ viewerRoles }} · case version {{ review.case_version }}
      <span v-if="refreshing"> · updating…</span>
    </p>

    <div class="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <div class="space-y-4">
        <section
          aria-labelledby="decision-heading"
          class="rounded-surface border border-outline-gray-1 bg-surface-base"
        >
          <h2
            id="decision-heading"
            class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
          >
            {{ openDecision ? 'Decision waiting for approval' : 'Propose a decision' }}
          </h2>
          <div class="px-4 py-4">
            <OpenDecisionCard
              v-if="openDecision"
              :decision="openDecision"
              :viewer="review.viewer"
              :findings="data.findings"
              :reset-token="resetDecision"
              @request="
                (action, reason) =>
                  open({ kind: 'review', decision: openDecision!, action, reason })
              "
            />
            <p v-else-if="resolved" class="text-ink-gray-6">
              This case is resolved. No further decision can be proposed.
            </p>
            <ProposeDecisionForm
              v-else-if="allowed.includes('decision.propose')"
              :case-status="item.status"
              :allowed="allowed"
              :findings="data.findings"
              :prefill="decisionPrefill"
              :reset-token="resetDecision"
              @request="(draft) => open({ kind: 'propose', draft })"
            />
            <p v-else class="text-ink-gray-6">{{ PERMISSION_REASONS['decision.propose'] }}</p>
          </div>
        </section>

        <section
          aria-labelledby="evidence-request-heading"
          class="rounded-surface border border-outline-gray-1 bg-surface-base"
        >
          <h2
            id="evidence-request-heading"
            class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
          >
            Request evidence from the taxpayer
          </h2>
          <div class="px-4 py-4">
            <p v-if="resolved" class="text-ink-gray-6">This case is resolved.</p>
            <EvidenceRequestForm
              v-else-if="allowed.includes('evidence_request.authorise')"
              :findings="data.findings"
              :prefill="evidencePrefill"
              :reset-token="resetEvidence"
              @request="(draft) => open({ kind: 'evidence', draft })"
            />
            <p v-else class="text-ink-gray-6">
              {{ PERMISSION_REASONS['evidence_request.authorise'] }}
            </p>
          </div>
        </section>
      </div>

      <div class="space-y-4">
        <section
          aria-labelledby="suggestions-heading"
          class="rounded-surface border border-outline-gray-1 bg-surface-base"
        >
          <h2
            id="suggestions-heading"
            class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
          >
            Suggestions to consider
          </h2>
          <div class="px-4 py-4">
            <SuggestionList
              v-if="suggested.length"
              :findings="suggested"
              :allowed="allowed"
              :case-status="item.status"
              :propose-blocked="proposeBlocked"
              @start="startFrom"
            />
            <p v-else class="text-ink-gray-5">No agent or rule suggestions for this case.</p>
          </div>
        </section>
        <section
          aria-labelledby="trail-heading"
          class="rounded-surface border border-outline-gray-1 bg-surface-base"
        >
          <h2
            id="trail-heading"
            class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
          >
            Review trail
          </h2>
          <div class="px-4 py-4">
            <ReviewTrail :events="review.audit_events" :viewer-id="review.viewer.officer_id" />
            <Button
              class="mt-3"
              label="Refresh"
              icon-left="lucide-rotate-cw"
              :loading="refreshing"
              @click="load"
            />
          </div>
        </section>
      </div>
    </div>

    <ConfirmActionDialog
      v-if="dialog"
      :open="true"
      :title="dialog.title"
      :rows="dialog.rows"
      :consequence="dialog.consequence"
      :confirm-label="dialog.confirmLabel"
      :pending="sending"
      :refusal="refusal"
      @confirm="confirm"
      @cancel="cancel"
      @reload="reloadAfterRefusal"
    />
  </div>
</template>
