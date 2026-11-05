/**
 * Shapes returned by vati_tax.portal.api. The session, submissions list and
 * status are live from TAX-092 (Frappe records with the TAX-039 review status).
 * Fixture mode stays supported for development servers that explicitly label
 * their data synthetic; the portal shows the fixture notice only then.
 */

export interface LiveMeta {
  mode: 'live';
}

export interface FixtureMeta {
  mode: 'fixture';
  synthetic: true;
}

/** Every read says where its data comes from; anything else is refused. */
export type DataMeta = LiveMeta | FixtureMeta;

/** A taxpayer the signed-in user may act for (TAX-029 membership). */
export interface TaxpayerScope {
  tenant_id: string;
  taxpayer_id: string;
}

export type PortalSession = DataMeta & {
  user: string;
  full_name: string;
  /** Present from TAX-043; selects the tenant for evidence uploads. */
  taxpayers?: TaxpayerScope[];
};

/** One uploaded supporting document (TAX-032 evidence view; never a file path or URL). */
export interface EvidenceView {
  evidence_id: string;
  document_type: string;
  media_type: string;
  size_bytes: number;
  document_sha256: string;
  received_at: string;
  replayed?: boolean;
}

export interface RefundClaimResult {
  mode: 'live';
  replayed: boolean;
  data: {
    submission_id: string;
    reference: string;
    status: string;
    period: string;
    submitted_at: string | null;
    claimed_amount: string;
    currency: string;
    claim_reason: string;
    evidence_count: number;
  };
}

export type StepState = 'complete' | 'current' | 'upcoming';

export interface SubmissionStep {
  label: string;
  state: StepState;
  at: string | null;
}

export type SubmissionStatus =
  'draft' | 'under_review' | 'evidence_requested' | 'correction_needed' | 'completed' | 'withdrawn';

export interface Submission {
  id: string;
  reference: string;
  taxpayer_name: string;
  tax_type: string;
  period: string;
  /** Decimal string; null means not yet provided (distinct from "0.00"). */
  amount: string | null;
  currency: string;
  status: SubmissionStatus;
  /** null until the submission is sent (a draft). */
  submitted_at: string | null;
  next_step: string;
  steps: SubmissionStep[];
}

export type DataEnvelope<T> = DataMeta & {
  data: T;
};

/** Taxpayer-facing statuses of an evidence request (TAX-045); nothing else is shown. */
export type EvidenceRequestStatus = 'awaiting_response' | 'response_sent' | 'no_longer_needed';

export interface RequestedItem {
  index: number;
  document_type: string;
  description: string;
}

export interface ResponseItemView {
  index: number;
  documents: { evidence_id: string; name: string }[];
  unavailable_reason: string | null;
}

/** vati_tax.responses.rules.taxpayer_view: an allowlist, never internal case data. */
export interface EvidenceRequestView {
  request_id: string;
  status: EvidenceRequestStatus;
  due_date: string | null;
  message_to_taxpayer: string;
  items: RequestedItem[];
  response: {
    responded_at: string | null;
    message: string | null;
    items: ResponseItemView[];
  } | null;
}

export interface LiveEnvelope<T> {
  mode: 'live';
  data: T;
  replayed?: boolean;
}

/** TAX-044 pre-submission check: taxpayer-facing figures and guidance only. */
export interface ReviewPrompt {
  code: string;
  /** Zero-based invoice row, or null for the claim as a whole. */
  invoice: number | null;
  message: string;
  /** TAX-078: the earlier-claim reminder that covers this prompt, if any. */
  reminder_id?: string | null;
}

export interface PresubmissionReview {
  calculation_version: string | null;
  figures: Partial<
    Record<
      | 'claimed_refund'
      | 'output_vat'
      | 'documented_input_vat'
      | 'explained_amount'
      | 'unresolved_amount',
      string
    >
  >;
  prompts: ReviewPrompt[];
  complete: boolean;
}

/**
 * TAX-078 correction reminder: guidance from a reviewed omission in one of
 * this taxpayer's earlier claims. Never a decision, risk rating or penalty.
 */
export interface CorrectionReminder {
  reminder_id: string;
  topic: string;
  title: string;
  explanation: string;
  /** TAX-044 prompt codes for the same kind of gap. */
  prompt_codes: string[];
  /** ISO dates (YYYY-MM-DD). */
  recorded_on: string;
  updated_on: string | null;
}
