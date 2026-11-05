<script setup lang="ts">
/**
 * Purchase-invoice schedule for a refund claim (TAX-044). One fieldset per
 * invoice, each linked to an uploaded document. Totals are shown from the
 * valid amounts typed so far; the server recomputes them.
 */
import { Button, FormControl } from 'frappe-ui';
import { computed } from 'vue';
import { formatAmount } from '../lib/format';
import {
  emptyInvoice,
  MAX_INVOICES,
  scheduleTotals,
  type InvoiceDraft,
  type RowErrors,
} from '../lib/schedule';

const rows = defineModel<InvoiceDraft[]>('rows', { required: true });
const props = defineProps<{
  documents: { evidenceId: string; name: string }[];
  errors: RowErrors;
}>();

const documentOptions = computed(() => [
  { label: 'Choose a document', value: '' },
  ...props.documents.map((d) => ({ label: d.name, value: d.evidenceId })),
]);
const contractOptions = computed(() => [
  { label: 'None', value: '' },
  ...props.documents.map((d) => ({ label: d.name, value: d.evidenceId })),
]);
const totals = computed(() => scheduleTotals(rows.value));

function add() {
  if (rows.value.length < MAX_INVOICES) rows.value.push(emptyInvoice());
}

function remove(index: number) {
  rows.value.splice(index, 1);
}
</script>

<template>
  <div class="space-y-5">
    <fieldset
      v-for="(row, index) in rows"
      :key="row.id"
      class="space-y-4 rounded-lg border border-outline-gray-2 p-5"
    >
      <legend
        :id="`invoice-${index}-title`"
        tabindex="-1"
        class="px-1 text-base font-semibold text-ink-gray-9"
      >
        Invoice {{ index + 1 }}
      </legend>
      <div class="grid gap-4 sm:grid-cols-2">
        <FormControl
          v-model="row.invoiceNumber"
          type="text"
          size="lg"
          variant="outline"
          label="Invoice number"
          required
          :error="errors[index]?.invoiceNumber"
        />
        <FormControl
          v-model="row.invoiceDate"
          type="text"
          size="lg"
          variant="outline"
          label="Invoice date"
          description="As YYYY-MM-DD."
          placeholder="2026-09-12"
          autocomplete="off"
          required
          :error="errors[index]?.invoiceDate"
        />
        <FormControl
          v-model="row.supplierName"
          type="text"
          size="lg"
          variant="outline"
          label="Supplier"
          required
          :error="errors[index]?.supplierName"
        />
        <FormControl
          v-model="row.supplierTin"
          type="text"
          size="lg"
          variant="outline"
          label="Supplier TIN (optional)"
          :error="errors[index]?.supplierTin"
        />
        <FormControl
          v-model="row.net"
          type="text"
          size="lg"
          variant="outline"
          inputmode="decimal"
          label="Amount excluding VAT (LSL)"
          required
          :error="errors[index]?.net"
        />
        <FormControl
          v-model="row.vat"
          type="text"
          size="lg"
          variant="outline"
          inputmode="decimal"
          label="VAT (LSL)"
          required
          :error="errors[index]?.vat"
        />
        <FormControl
          v-model="row.gross"
          type="text"
          size="lg"
          variant="outline"
          inputmode="decimal"
          label="Total including VAT (LSL)"
          description="Must equal the amount excluding VAT plus the VAT."
          required
          :error="errors[index]?.gross"
        />
        <FormControl
          v-model="row.paid"
          type="text"
          size="lg"
          variant="outline"
          inputmode="decimal"
          label="Amount paid (LSL, optional)"
          description="Leave blank if you have not paid it yet."
          :error="errors[index]?.paid"
        />
        <FormControl
          v-model="row.invoiceEvidenceId"
          type="select"
          size="lg"
          variant="outline"
          label="Invoice document"
          description="The uploaded copy of this invoice."
          required
          :options="documentOptions"
          :error="errors[index]?.invoiceEvidenceId"
        />
        <FormControl
          v-model="row.contractEvidenceId"
          type="select"
          size="lg"
          variant="outline"
          label="Contract or purchase order (optional)"
          :options="contractOptions"
          :error="errors[index]?.contractEvidenceId"
        />
      </div>
      <!-- Visible text in the slot: frappe-ui's `label` prop would replace the aria-label. -->
      <Button
        v-if="rows.length > 1"
        :aria-label="`Remove invoice ${index + 1}`"
        icon-left="lucide-trash-2"
        @click="remove(index)"
      >
        Remove
      </Button>
    </fieldset>

    <Button
      icon-left="lucide-plus"
      :disabled="rows.length >= MAX_INVOICES"
      label="Add another invoice"
      @click="add"
    />

    <dl
      aria-label="Invoice totals"
      class="grid grid-cols-[minmax(0,14rem)_minmax(0,1fr)] gap-x-4 gap-y-2 rounded-lg bg-surface-gray-1 p-4"
    >
      <dt class="text-ink-gray-7">Excluding VAT</dt>
      <dd class="tabular-nums text-ink-gray-9">{{ formatAmount(totals.net, 'LSL') }}</dd>
      <dt class="text-ink-gray-7">VAT on these invoices</dt>
      <dd class="tabular-nums font-medium text-ink-gray-9">
        {{ formatAmount(totals.vat, 'LSL') }}
      </dd>
      <dt class="text-ink-gray-7">Including VAT</dt>
      <dd class="tabular-nums text-ink-gray-9">{{ formatAmount(totals.gross, 'LSL') }}</dd>
    </dl>
  </div>
</template>
