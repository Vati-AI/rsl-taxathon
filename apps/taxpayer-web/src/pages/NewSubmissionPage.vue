<script setup lang="ts">
/**
 * VAT refund claim (TAX-043, TAX-044): claim details → supporting documents →
 * purchase invoices → review (with the pre-submission checks) and send →
 * confirmation. Documents are uploaded as private evidence as soon as
 * they are chosen; the claim is sent once, with one idempotency key for the
 * whole draft, so pressing Send again after a lost connection cannot create a
 * second claim.
 *
 * TAX-078: reminders from reviewed omissions in this taxpayer's earlier claims
 * are shown on the first step and next to the pre-submission prompts they cover.
 */
import { Alert, Badge, Button, FormControl } from 'frappe-ui';
import { computed, nextTick, onMounted, reactive, ref } from 'vue';
import CorrectionReminders from '../components/CorrectionReminders.vue';
import DocumentUploads from '../components/DocumentUploads.vue';
import InvoiceSchedule from '../components/InvoiceSchedule.vue';
import PortalHeader from '../components/PortalHeader.vue';
import PresubmissionReview from '../components/PresubmissionReview.vue';
import {
  PortalError,
  fetchCorrectionReminders,
  reviewRefundClaim,
  submitRefundClaim,
} from '../lib/api';
import { documentTypeLabel } from '../lib/documents';
import { formatAmount, formatPeriod } from '../lib/format';
import { sessionState } from '../lib/session';
import {
  emptyInvoice,
  mapScheduleErrors,
  toPayload,
  validateSchedule,
  type InvoiceDraft,
  type RowErrors,
} from '../lib/schedule';
import type {
  CorrectionReminder,
  PresubmissionReview as Review,
  RefundClaimResult,
} from '../lib/types';
import { useDocumentUploads } from '../lib/uploads';
import {
  CLAIM_REASONS,
  emptyClaim,
  mapServerErrors,
  newIdempotencyKey,
  normaliseAmount,
  reasonLabel,
  stepForField,
  validateDetails,
  type ClaimErrors,
  type ClaimField,
} from '../lib/validation';

type Step = 'details' | 'documents' | 'invoices' | 'review' | 'sent';

const STEPS: { id: Exclude<Step, 'sent'>; label: string }[] = [
  { id: 'details', label: 'Claim details' },
  { id: 'documents', label: 'Supporting documents' },
  { id: 'invoices', label: 'Purchase invoices' },
  { id: 'review', label: 'Review and send' },
];

const draft = reactive(emptyClaim());
const errors = ref<ClaimErrors>({});
const step = ref<Step>('details');
const form = ref<HTMLFormElement | null>(null);
const documentsPanel = ref<InstanceType<typeof DocumentUploads> | null>(null);
const sending = ref(false);
const sendProblem = ref<{ title: string; description: string; conflict?: boolean } | null>(null);
const result = ref<RefundClaimResult | null>(null);
// One key per draft: a repeated send is recognised by the server and replayed.
const claimKey = newIdempotencyKey('claim');
// TAX-044: the purchase-invoice schedule and the pre-submission checks.
const invoiceRows = ref<InvoiceDraft[]>([emptyInvoice()]);
const rowErrors = ref<RowErrors>({});
const review = ref<Review | null>(null);
const reviewing = ref(false);
const reviewError = ref<string | null>(null);
// TAX-078: reminders from earlier claims (guidance only; never blocks the form).
const reminders = ref<CorrectionReminder[]>([]);
const remindersError = ref<string | null>(null);

async function loadReminders() {
  remindersError.value = null;
  try {
    reminders.value = await fetchCorrectionReminders();
  } catch {
    reminders.value = [];
    remindersError.value = 'You can still fill in and send this claim.';
  }
}
onMounted(loadReminders);

/** The one taxpayer this account acts for; claims need exactly one. */
const tenantId = computed(() => {
  const state = sessionState.value;
  const scope = state.status === 'ready' ? (state.session.taxpayers ?? []) : [];
  return scope.length === 1 ? scope[0].tenant_id : null;
});
const scopeReady = computed(() => sessionState.value.status !== 'ready' || tenantId.value !== null);

const uploads = useDocumentUploads(() => tenantId.value);
const uploadedItems = computed(() => uploads.items.filter((i) => i.status === 'uploaded'));
const scheduleDocuments = computed(() =>
  uploadedItems.value.map((i) => ({ evidenceId: i.evidenceId!, name: i.name })),
);

const reasonOptions = [
  { label: 'Choose a reason', value: '' },
  ...CLAIM_REASONS.map((r) => ({ label: r.label, value: r.value })),
];
const reasonHint = computed(
  () =>
    CLAIM_REASONS.find((r) => r.value === draft.reason)?.hint ??
    'Choose the option that best describes why VAT is owed back to you.',
);
const detailErrorCount = computed(
  () =>
    (
      [
        'period',
        'amount',
        'outputVat',
        'inputVat',
        'reason',
        'explanation',
        'contactEmail',
      ] as const
    ).filter((f) => errors.value[f]).length,
);
const stepIndex = computed(() => STEPS.findIndex((s) => s.id === step.value));

async function focusFirstInvalid(root: ParentNode | null) {
  await nextTick();
  root?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
}

async function goTo(next: Step) {
  step.value = next;
  if (next !== 'review') sendProblem.value = null;
  await nextTick();
  document.getElementById(`${next}-title`)?.focus();
}

async function continueFromDetails() {
  errors.value = validateDetails(draft);
  // Keep entered values; move focus to the first field that needs attention.
  if (detailErrorCount.value > 0) return focusFirstInvalid(form.value);
  await goTo('documents');
}

async function continueFromDocuments() {
  const blocker = uploads.blocker();
  errors.value = blocker ? { documents: blocker } : {};
  if (blocker) {
    await nextTick();
    documentsPanel.value?.focusPicker();
    return;
  }
  // Convenience: with one document and an empty first row, link it for the taxpayer.
  const first = invoiceRows.value[0];
  if (first && !first.invoiceEvidenceId && scheduleDocuments.value.length === 1) {
    first.invoiceEvidenceId = scheduleDocuments.value[0].evidenceId;
  }
  await goTo('invoices');
}

async function focusFirstRowProblem() {
  await nextTick();
  const first = Object.keys(rowErrors.value)
    .map(Number)
    .sort((a, b) => a - b)[0];
  const fieldset = first === undefined ? null : document.getElementById(`invoice-${first}-title`);
  fieldset?.closest('fieldset')?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
}

async function continueFromInvoices() {
  const checked = validateSchedule(invoiceRows.value, {
    period: draft.period,
    uploaded: new Set(scheduleDocuments.value.map((d) => d.evidenceId)),
  });
  rowErrors.value = checked.rows;
  errors.value = checked.general ? { invoices: checked.general } : {};
  if (checked.general) return focusHeadingOf('invoices');
  if (Object.keys(checked.rows).length) {
    errors.value = { invoices: 'Fix the highlighted invoice details to continue.' };
    return focusFirstRowProblem();
  }
  await goTo('review');
  await runReview();
}

async function focusHeadingOf(next: Step) {
  await nextTick();
  document.getElementById(`${next}-title`)?.focus();
}

/** The answers both the review and the send use. */
function claimPayload() {
  return {
    period: draft.period.trim(),
    claimed_amount: normaliseAmount(draft.amount) ?? draft.amount.trim(),
    claim_reason: draft.reason,
    explanation: draft.explanation.trim() || null,
    contact_email: draft.contactEmail.trim() || null,
    evidence_ids: uploads.evidenceIds.value,
    tenant_id: tenantId.value ?? '',
    period_output_vat: normaliseAmount(draft.outputVat) ?? draft.outputVat.trim(),
    period_input_vat: normaliseAmount(draft.inputVat) ?? draft.inputVat.trim(),
    invoices: toPayload(invoiceRows.value),
  };
}

async function runReview() {
  if (!tenantId.value) return;
  reviewing.value = true;
  reviewError.value = null;
  review.value = null;
  try {
    review.value = await reviewRefundClaim(claimPayload());
  } catch (error) {
    const err =
      error instanceof PortalError ? error : new PortalError('unavailable', String(error));
    if (err.kind === 'invalid' && err.fieldErrors.length > 0) {
      await showServerErrors(err.fieldErrors);
    } else {
      reviewError.value =
        'The checks are not available right now. You can still send the claim, or try again.';
    }
  } finally {
    reviewing.value = false;
  }
}

async function showServerErrors(fieldErrors: { field: string; message: string }[]) {
  const { errors: mapped, other } = mapServerErrors(fieldErrors);
  errors.value = mapped;
  const first = (Object.keys(mapped) as ClaimField[])[0];
  if (!first) {
    sendProblem.value = {
      title: 'The claim was not accepted',
      description: other[0] ?? 'Check your answers and send the claim again.',
    };
    return;
  }
  const target = stepForField(first);
  if (target === 'review') return focusFirstInvalid(document.getElementById('review'));
  if (target === 'invoices') {
    const mapped = mapScheduleErrors(fieldErrors);
    rowErrors.value = mapped.rows;
    if (mapped.general) errors.value = { ...errors.value, invoices: mapped.general };
  }
  await goTo(target);
  if (target === 'details') await focusFirstInvalid(form.value);
  if (target === 'invoices') await focusFirstRowProblem();
}

async function send() {
  sendProblem.value = null;
  if (!draft.declaration) {
    errors.value = { declaration: 'Confirm the declaration to send.' };
    return focusFirstInvalid(document.getElementById('review'));
  }
  if (!tenantId.value) return;
  errors.value = {};
  sending.value = true;
  try {
    result.value = await submitRefundClaim({
      ...claimPayload(),
      idempotency_key: claimKey,
      declaration: true,
      tenant_id: tenantId.value,
    });
    await goTo('sent');
  } catch (error) {
    const err =
      error instanceof PortalError ? error : new PortalError('unavailable', String(error));
    if (err.kind === 'invalid' && err.code === 'idempotency_conflict') {
      sendProblem.value = {
        title: 'This claim was already sent with different details',
        description: 'Nothing new was sent. Check your submissions before starting a new claim.',
        conflict: true,
      };
    } else if (err.kind === 'invalid' && err.fieldErrors.length > 0) {
      await showServerErrors(err.fieldErrors);
    } else if (err.kind === 'unavailable') {
      sendProblem.value = {
        title: 'We could not confirm that your claim was received',
        description:
          'Send it again. The portal recognises a repeated send, so it will not create a second claim.',
      };
    } else {
      sendProblem.value = { title: 'The claim was not sent', description: err.message };
    }
  } finally {
    sending.value = false;
  }
}
</script>

<template>
  <section class="max-w-2xl">
    <PortalHeader
      title="Online Refunds simulator"
      description="A synthetic stand-in for RSL's Online Refunds Application, for development and demonstrations only."
    />

    <!-- TAX-125 (ADR-043): real claims are filed with RSL; this form only simulates that. -->
    <Alert
      class="mb-6"
      theme="amber"
      title="Simulator: not RSL's Online Refunds Application"
      description="This page exists only on development, test and demo sites. It sends a synthetic VAT refund claim as if it had been filed with RSL. Taxpayers file real refund claims in RSL's Online Refunds Application."
    />

    <Alert
      v-if="!scopeReady"
      class="mb-6"
      theme="amber"
      title="Claims cannot be sent from this account yet"
      description="This account is linked to more than one taxpayer. Sending a claim for a chosen taxpayer is not available yet."
    />

    <ol
      v-if="step !== 'sent'"
      class="mb-6 flex flex-wrap gap-x-4 gap-y-1 text-sm"
      aria-label="Progress"
    >
      <li
        v-for="(item, index) in STEPS"
        :key="item.id"
        :aria-current="step === item.id ? 'step' : undefined"
        :class="
          step === item.id
            ? 'font-semibold text-ink-gray-9'
            : index < stepIndex
              ? 'text-ink-gray-7'
              : 'text-ink-gray-6'
        "
      >
        {{ index + 1 }}. {{ item.label }}
      </li>
    </ol>

    <!-- Step 1: claim details -->
    <form
      v-if="step === 'details'"
      ref="form"
      novalidate
      class="space-y-6"
      @submit.prevent="continueFromDetails"
    >
      <h2 id="details-title" tabindex="-1" class="text-lg font-semibold text-ink-gray-9">
        Claim details
      </h2>
      <p class="text-base text-ink-gray-6">All fields are required unless marked optional.</p>

      <CorrectionReminders :reminders="reminders" :error="remindersError" @retry="loadReminders" />

      <Alert
        v-if="detailErrorCount > 0"
        theme="red"
        role="alert"
        :title="
          detailErrorCount === 1
            ? 'Fix 1 field to continue'
            : `Fix ${detailErrorCount} fields to continue`
        "
        description="Your other answers have been kept."
      />

      <FormControl
        v-model="draft.period"
        type="text"
        size="lg"
        variant="outline"
        label="Tax period"
        description="The month the claim covers, as YYYY-MM."
        placeholder="2026-09"
        autocomplete="off"
        required
        :error="errors.period"
      />
      <FormControl
        v-model="draft.amount"
        type="text"
        size="lg"
        variant="outline"
        inputmode="decimal"
        label="Amount claimed (LSL)"
        description="The VAT you are claiming back, for example 15000.00. It must be more than 0.00."
        required
        :error="errors.amount"
      />
      <FormControl
        v-model="draft.outputVat"
        type="text"
        size="lg"
        variant="outline"
        inputmode="decimal"
        label="Output VAT for the period (LSL)"
        description="The VAT you charged on sales in this period, as in your return. Enter 0.00 if none."
        required
        :error="errors.outputVat"
      />
      <FormControl
        v-model="draft.inputVat"
        type="text"
        size="lg"
        variant="outline"
        inputmode="decimal"
        label="Input VAT for the period (LSL)"
        description="The VAT you paid on purchases in this period, as in your return."
        required
        :error="errors.inputVat"
      />
      <FormControl
        v-model="draft.reason"
        type="select"
        size="lg"
        variant="outline"
        label="Reason for the claim"
        :description="reasonHint"
        required
        :options="reasonOptions"
        :error="errors.reason"
      />
      <FormControl
        v-model="draft.explanation"
        type="textarea"
        size="lg"
        variant="outline"
        :label="draft.reason === 'other' ? 'Explanation' : 'Explanation (optional)'"
        description="Anything that helps explain the claim. Do not include passwords or bank PINs."
        :required="draft.reason === 'other'"
        :rows="4"
        :error="errors.explanation"
      />
      <FormControl
        v-model="draft.contactEmail"
        type="email"
        size="lg"
        variant="outline"
        autocomplete="email"
        label="Contact email (optional)"
        description="Where we can reach you about this claim."
        :error="errors.contactEmail"
      />

      <div class="flex flex-wrap gap-3">
        <Button type="submit" variant="solid" size="lg" label="Continue to documents" />
        <Button size="lg" label="Cancel" :route="{ name: 'submissions' }" />
      </div>
    </form>

    <!-- Step 2: supporting documents -->
    <section v-else-if="step === 'documents'" aria-labelledby="documents-title" class="space-y-6">
      <h2 id="documents-title" tabindex="-1" class="text-lg font-semibold text-ink-gray-9">
        Supporting documents
      </h2>
      <p class="text-base text-ink-gray-6">
        Upload at least one document that supports the claim, such as the tax invoices for your
        purchases or the export documents for zero-rated sales.
      </p>

      <Alert
        v-if="errors.documents"
        theme="red"
        role="alert"
        title="Documents need attention"
        :description="errors.documents"
      />

      <DocumentUploads ref="documentsPanel" :uploads="uploads" :error="errors.documents" />

      <div class="flex flex-wrap gap-3">
        <Button
          variant="solid"
          size="lg"
          label="Continue to invoices"
          @click="continueFromDocuments"
        />
        <Button size="lg" label="Back" icon-left="lucide-arrow-left" @click="goTo('details')" />
      </div>
    </section>

    <!-- Step 3: purchase invoices (TAX-044) -->
    <section v-else-if="step === 'invoices'" aria-labelledby="invoices-title" class="space-y-6">
      <h2 id="invoices-title" tabindex="-1" class="text-lg font-semibold text-ink-gray-9">
        Purchase invoices
      </h2>
      <p class="text-base text-ink-gray-6">
        List each purchase invoice behind the VAT you are claiming, and choose its uploaded copy. We
        add up the totals for you.
      </p>
      <Alert
        v-if="errors.invoices"
        theme="red"
        role="alert"
        title="Invoices need attention"
        :description="errors.invoices"
      />
      <InvoiceSchedule
        v-model:rows="invoiceRows"
        :documents="scheduleDocuments"
        :errors="rowErrors"
      />
      <div class="flex flex-wrap gap-3">
        <Button
          variant="solid"
          size="lg"
          label="Continue to review"
          @click="continueFromInvoices"
        />
        <Button size="lg" label="Back" icon-left="lucide-arrow-left" @click="goTo('documents')" />
      </div>
    </section>

    <!-- Step 3: review and send -->
    <section
      v-else-if="step === 'review'"
      id="review"
      aria-labelledby="review-title"
      class="space-y-6"
    >
      <h2 id="review-title" tabindex="-1" class="text-lg font-semibold text-ink-gray-9">
        Check your claim
      </h2>

      <Alert
        v-if="sendProblem"
        :theme="sendProblem.conflict ? 'amber' : 'red'"
        role="alert"
        :title="sendProblem.title"
        :description="sendProblem.description"
      />
      <Button v-if="sendProblem?.conflict" label="View my cases" :route="{ name: 'submissions' }" />

      <div class="rounded-lg border border-outline-gray-2 p-5">
        <div class="mb-3 flex items-center justify-between gap-3">
          <h3 class="text-base font-semibold text-ink-gray-9">Claim details</h3>
          <!-- Visible text in the slot: frappe-ui's `label` prop would replace the aria-label. -->
          <Button aria-label="Change claim details" @click="goTo('details')">Change</Button>
        </div>
        <dl class="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-x-4 gap-y-3">
          <dt class="text-ink-gray-6">Tax period</dt>
          <dd class="text-ink-gray-9">{{ formatPeriod(draft.period.trim()) }}</dd>
          <dt class="text-ink-gray-6">Amount claimed</dt>
          <dd class="tabular-nums text-ink-gray-9">
            {{ formatAmount(normaliseAmount(draft.amount), 'LSL') }}
          </dd>
          <dt class="text-ink-gray-6">Output VAT for the period</dt>
          <dd class="tabular-nums text-ink-gray-9">
            {{ formatAmount(normaliseAmount(draft.outputVat), 'LSL') }}
          </dd>
          <dt class="text-ink-gray-6">Input VAT for the period</dt>
          <dd class="tabular-nums text-ink-gray-9">
            {{ formatAmount(normaliseAmount(draft.inputVat), 'LSL') }}
          </dd>
          <dt class="text-ink-gray-6">Reason</dt>
          <dd class="text-ink-gray-9">{{ reasonLabel(draft.reason) }}</dd>
          <dt class="text-ink-gray-6">Explanation</dt>
          <dd class="whitespace-pre-line break-words text-ink-gray-9">
            {{ draft.explanation.trim() || 'Not provided' }}
          </dd>
          <dt class="text-ink-gray-6">Contact email</dt>
          <dd class="break-words text-ink-gray-9">
            {{ draft.contactEmail.trim() || 'Not provided' }}
          </dd>
        </dl>
      </div>

      <div class="rounded-lg border border-outline-gray-2 p-5">
        <div class="mb-3 flex items-center justify-between gap-3">
          <h3 class="text-base font-semibold text-ink-gray-9">
            Supporting documents ({{ uploadedItems.length }})
          </h3>
          <Button aria-label="Change supporting documents" @click="goTo('documents')">
            Change
          </Button>
        </div>
        <ul class="space-y-1">
          <li v-for="item in uploadedItems" :key="item.id" class="break-all text-ink-gray-9">
            {{ item.name }}
            <span class="text-ink-gray-6">· {{ documentTypeLabel(item.documentType) }}</span>
          </li>
        </ul>
      </div>

      <div class="rounded-lg border border-outline-gray-2 p-5">
        <div class="mb-3 flex items-center justify-between gap-3">
          <h3 class="text-base font-semibold text-ink-gray-9">
            Purchase invoices ({{ invoiceRows.length }})
          </h3>
          <Button aria-label="Change purchase invoices" @click="goTo('invoices')">Change</Button>
        </div>
        <ul class="space-y-1">
          <li v-for="(row, index) in invoiceRows" :key="row.id" class="break-words text-ink-gray-9">
            {{ index + 1 }}. {{ row.supplierName }} · {{ row.invoiceNumber }}
            <span class="tabular-nums text-ink-gray-6">
              · VAT {{ formatAmount(normaliseAmount(row.vat), 'LSL') }}
            </span>
          </li>
        </ul>
      </div>

      <PresubmissionReview
        :review="review"
        :reminders="reminders"
        :loading="reviewing"
        :error="reviewError"
        @fix="(target) => goTo(target)"
        @retry="runReview"
      />

      <FormControl
        v-model="draft.declaration"
        type="checkbox"
        label="I declare that the information I have given is true and complete."
        :error="errors.declaration"
      />

      <div class="flex flex-wrap items-center gap-3">
        <Button
          variant="solid"
          size="lg"
          label="Send claim"
          :loading="sending"
          :disabled="sending || !tenantId"
          @click="send"
        />
        <Button size="lg" label="Back" icon-left="lucide-arrow-left" @click="goTo('invoices')" />
      </div>
    </section>

    <!-- Confirmation -->
    <section v-else-if="result" aria-labelledby="sent-title" class="space-y-6">
      <h2 id="sent-title" tabindex="-1" class="text-lg font-semibold text-ink-gray-9">
        Claim sent
      </h2>
      <Alert
        theme="green"
        title="Your VAT refund claim was received"
        :description="
          result.replayed
            ? 'This claim had already been received. No second claim was created.'
            : 'Keep the reference below for any questions about this claim.'
        "
      />
      <div class="rounded-lg border border-outline-gray-2 p-5">
        <p class="text-sm text-ink-gray-6">Reference</p>
        <p class="mb-4 text-2xl font-semibold tabular-nums text-ink-gray-9">
          {{ result.data.reference }}
        </p>
        <dl class="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-x-4 gap-y-3">
          <dt class="text-ink-gray-6">Status</dt>
          <dd>
            <Badge
              :label="result.data.status === 'submitted' ? 'Submitted' : result.data.status"
              theme="blue"
              variant="subtle"
            />
          </dd>
          <dt class="text-ink-gray-6">Tax period</dt>
          <dd class="text-ink-gray-9">{{ formatPeriod(result.data.period) }}</dd>
          <dt class="text-ink-gray-6">Amount claimed</dt>
          <dd class="tabular-nums text-ink-gray-9">
            {{ formatAmount(result.data.claimed_amount, result.data.currency) }}
          </dd>
          <dt class="text-ink-gray-6">Reason</dt>
          <dd class="text-ink-gray-9">{{ reasonLabel(result.data.claim_reason) }}</dd>
          <dt class="text-ink-gray-6">Documents</dt>
          <dd class="text-ink-gray-9">{{ result.data.evidence_count }}</dd>
        </dl>
      </div>
      <Button size="lg" label="Go to my cases" :route="{ name: 'submissions' }" />
    </section>
  </section>
</template>
