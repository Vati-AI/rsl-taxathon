/**
 * Officer review wording and rules for the decision panel (TAX-042). Outcomes and
 * refusal reasons mirror ADR-013; the server decides, these only explain.
 */
import type { ApiError } from '../api/errors';
import type {
  Decision,
  DecisionOutcome,
  Finding,
  ProposedActionType,
  ReviewPermission,
} from '../api/review';
import type { CaseStatus } from '../api/vati';
import { statusInfo } from './cases';

const statusLabel = (status: CaseStatus) => statusInfo(status).label;

export type OutcomeGroup = 'clear' | 'more' | 'review';

export interface OutcomeInfo {
  label: string;
  description: string;
  group: OutcomeGroup;
  /** Closing outcomes must cite at least one finding or evidence item. */
  closes: boolean;
  /** Case status a transition sets; null for escalations. */
  sets: CaseStatus | null;
  /** Extra permission besides decision.propose. */
  needs: ReviewPermission | null;
}

export const OUTCOME_INFO: Record<DecisionOutcome, OutcomeInfo> = {
  close_no_further_action: {
    label: 'Clear: no further action',
    description: 'Close the case; the review found nothing to correct.',
    group: 'clear',
    closes: true,
    sets: 'RESOLVED',
    needs: null,
  },
  close_after_correction: {
    label: 'Clear after correction',
    description: 'Close the case; the taxpayer has already corrected the issue.',
    group: 'clear',
    closes: true,
    sets: 'RESOLVED',
    needs: null,
  },
  request_further_evidence: {
    label: 'Wait for further evidence',
    description: 'Move the case to Awaiting evidence while the taxpayer responds.',
    group: 'more',
    closes: false,
    sets: 'AWAITING_EVIDENCE',
    needs: null,
  },
  continue_investigation: {
    label: 'Continue the investigation',
    description: 'Return the case to Investigating for more work.',
    group: 'more',
    closes: false,
    sets: 'INVESTIGATING',
    needs: null,
  },
  escalate_to_supervisor: {
    label: 'Escalate to a supervisor',
    description: 'Ask a supervisor to review the case.',
    group: 'review',
    closes: false,
    sets: null,
    needs: null,
  },
  refer_to_specialist: {
    label: 'Refer to a specialist',
    description:
      'Ask a specialist officer for an internal review. Not a referral outside the tax office.',
    group: 'review',
    closes: false,
    sets: null,
    needs: 'specialist.refer',
  },
};

export const OUTCOME_GROUPS: { group: OutcomeGroup; label: string }[] = [
  { group: 'clear', label: 'Clearance' },
  { group: 'more', label: 'More work' },
  { group: 'review', label: 'Another review' },
];

/** Whether an outcome can be proposed now, and why not. */
export function outcomeAvailability(
  outcome: DecisionOutcome,
  caseStatus: CaseStatus,
  allowed: readonly ReviewPermission[],
): { available: boolean; reason: string | null } {
  const info = OUTCOME_INFO[outcome];
  if (info.needs && !allowed.includes(info.needs))
    return { available: false, reason: 'Your role cannot refer cases to a specialist.' };
  if (info.sets && info.sets === caseStatus)
    return { available: false, reason: `The case is already in “${statusLabel(caseStatus)}”.` };
  return { available: true, reason: null };
}

/** Why a review action is not offered, for a disabled control's description. */
export const PERMISSION_REASONS: Record<ReviewPermission, string> = {
  'decision.propose': 'Your role cannot propose decisions on this case.',
  'specialist.refer': 'Your role cannot refer cases to a specialist.',
  'evidence_request.authorise':
    'Only auditors and supervisors can authorise evidence requests to the taxpayer.',
  'decision.approve':
    'Only a supervisor who did not propose this decision can approve or reject it.',
};

export const DECISION_STATUS_LABELS: Record<Decision['status'], string> = {
  proposed: 'Waiting for approval',
  approved: 'Approved',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
};

export function syncText(decision: Decision): { text: string; tone: 'gray' | 'green' | 'red' } {
  const sync = decision.jube_sync;
  if (sync.state === 'synced')
    return {
      text: `Applied in Investigation: case is now ${statusLabel(sync.status_after ?? decision.target_status)}.`,
      tone: 'green',
    };
  if (sync.state === 'failed')
    return {
      text: `Not applied in Investigation (${sync.error_code ?? 'unknown error'}). ${
        sync.retryable
          ? 'A supervisor can retry.'
          : 'Propose a new decision after reviewing the case.'
      }`,
      tone: 'red',
    };
  if (sync.state === 'pending') return { text: 'Sending to Investigation…', tone: 'gray' };
  return { text: 'Nothing is sent to Investigation until a supervisor approves.', tone: 'gray' };
}

export interface Refusal {
  title: string;
  message: string;
  /** What the officer can do next: reload the case, send the same request again, or neither. */
  next: 'reload' | 'retry' | 'none';
}

const REASONS: Record<string, Refusal> = {
  stale_case_version: {
    title: 'This case changed while you were reviewing it',
    message:
      'Another review action was recorded first. Reload to see the latest state; your text is kept.',
    next: 'reload',
  },
  decision_pending: {
    title: 'Another decision is already waiting for approval',
    message: 'Only one decision per case can be open. Reload to see it before proposing another.',
    next: 'reload',
  },
  case_state_changed: {
    title: 'The case status changed in Investigation',
    message:
      'The status you reviewed is no longer current, so this action was not taken. Reload and review again.',
    next: 'reload',
  },
  proposal_expired: {
    title: 'This proposal is too old to approve',
    message:
      'Proposals expire after 72 hours. Reject it with a reason, or ask the officer to propose again.',
    next: 'none',
  },
  not_pending: {
    title: 'This decision is no longer waiting for approval',
    message: 'Someone reviewed or withdrew it first. Reload to see what happened.',
    next: 'reload',
  },
  agent_only_decision: {
    title: 'An agent draft cannot be approved',
    message: 'An officer must propose the decision in their own words first.',
    next: 'none',
  },
  citation_required: {
    title: 'Cite at least one finding',
    message: 'A clearance must cite the findings or evidence it relies on.',
    next: 'none',
  },
  case_closed: {
    title: 'The case is already resolved',
    message: 'No further decision can be proposed.',
    next: 'reload',
  },
  no_change: {
    title: 'The case is already in that status',
    message: 'Choose an outcome that changes the case, or continue reviewing.',
    next: 'none',
  },
  self_approval: {
    title: 'You cannot approve your own proposal',
    message: 'A different supervisor must approve or reject it.',
    next: 'none',
  },
  idempotency_conflict: {
    title: 'This action was already sent with different details',
    message: 'Reload the case and start the action again.',
    next: 'reload',
  },
  due_date_past: {
    title: 'Choose a due date in the future',
    message: 'The taxpayer needs time to respond.',
    next: 'none',
  },
  due_date_too_far: {
    title: 'Choose an earlier due date',
    message: 'Evidence requests can be due at most 180 days from today.',
    next: 'none',
  },
};

/** Officer-facing explanation of a refused or failed review action. */
export function refusalFor(error: ApiError): Refusal {
  const known = error.reason ? REASONS[error.reason] : undefined;
  if (known) return known;
  switch (error.kind) {
    case 'denied':
      return {
        title: 'Your role does not permit this action',
        message: 'Nothing was recorded. Ask a supervisor if you need to act on this case.',
        next: 'none',
      };
    case 'conflict':
      return { ...REASONS.stale_case_version };
    case 'rejected':
      return {
        title: 'The case service refused this action',
        message: 'Check the details and try again. Nothing was recorded.',
        next: 'none',
      };
    case 'not_found':
      return {
        title: 'The case or decision was not found',
        message: 'Reload the case.',
        next: 'reload',
      };
    case 'unavailable':
      return {
        title: 'The case service is unavailable',
        message:
          'We do not know whether the action was recorded. Try again: the same request is sent, so it cannot be recorded twice.',
        next: 'retry',
      };
    default:
      return {
        title: 'The response could not be shown safely',
        message: 'Reload the case to see whether the action was recorded.',
        next: 'reload',
      };
  }
}

export interface SuggestionInfo {
  label: string;
  /** What "Use as a starting point" fills in; nothing is sent. */
  start: { form: 'decision'; outcome: DecisionOutcome } | { form: 'evidence' } | null;
}

export const SUGGESTIONS: Record<Exclude<ProposedActionType, 'none'>, SuggestionInfo> = {
  human_clearance: {
    label: 'Clear the case after officer review',
    start: { form: 'decision', outcome: 'close_no_further_action' },
  },
  request_evidence: { label: 'Request evidence from the taxpayer', start: { form: 'evidence' } },
  specialist_review: {
    label: 'Refer to a specialist for review',
    start: { form: 'decision', outcome: 'refer_to_specialist' },
  },
  policy_gap_review: { label: 'Review the policy gap with a supervisor', start: null },
};

export function suggestions(findings: Finding[]) {
  return findings.filter((f) => f.proposed_action.type !== 'none');
}

export const FINDING_KIND_LABELS: Record<Finding['kind'], string> = {
  observed_fact: 'Observed fact',
  inference: 'Inference',
  missing_evidence: 'Missing evidence',
  unverified_evidence: 'Unverified evidence',
  contradictory_evidence: 'Contradictory evidence',
};

export const PRODUCER_LABELS: Record<Finding['producer'], string> = {
  jube_rule: 'Vati rule check',
  langgraph_run: 'Investigation agent',
  officer: 'Officer',
};

export const ROLE_LABELS: Record<string, string> = {
  auditor: 'Auditor',
  specialist: 'Specialist',
  supervisor: 'Supervisor',
  service: 'Service',
};

export const EVENT_LABELS: Record<string, string> = {
  'decision.proposed': 'Decision proposed',
  'decision.approved': 'Decision approved',
  'decision.rejected': 'Decision rejected',
  'decision.withdrawn': 'Proposal withdrawn',
  'decision.executed': 'Applied in Investigation',
  'decision.execution_failed': 'Investigation update failed',
  'evidence_request.recorded': 'Evidence request recorded',
  'draft.recorded': 'Agent draft recorded',
};

export const RATIONALE_MIN = 20;
export const RATIONALE_MAX = 4000;
export const MESSAGE_MAX = 2000;

/** ISO date `days` after today in local time, for the default evidence due date. */
export function isoDateAfter(days: number, today = new Date()): string {
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
