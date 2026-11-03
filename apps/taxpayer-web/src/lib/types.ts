/** Shapes returned by vati_tax.portal.api (fixture mode until TAX-031). */

export interface FixtureMeta {
  mode: 'fixture';
  synthetic: true;
}

export interface PortalSession extends FixtureMeta {
  user: string;
  full_name: string;
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
