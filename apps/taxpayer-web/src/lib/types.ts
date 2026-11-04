/**
 * Shapes returned by vati_tax.portal.api. The submissions list and status are
 * fixture mode; the VAT refund claim (TAX-043) is live.
 */

export interface FixtureMeta {
  mode: 'fixture';
  synthetic: true;
}

/** A taxpayer the signed-in user may act for (TAX-029 membership). */
export interface TaxpayerScope {
  tenant_id: string;
  taxpayer_id: string;
}

export interface PortalSession extends FixtureMeta {
  user: string;
  full_name: string;
  /** Present from TAX-043; selects the tenant for evidence uploads. */
  taxpayers?: TaxpayerScope[];
}

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
  'draft' | 'under_review' | 'evidence_requested' | 'correction_needed' | 'completed';

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
  submitted_at: string;
  next_step: string;
  steps: SubmissionStep[];
}

export interface FixtureEnvelope<T> extends FixtureMeta {
  data: T;
}
