/**
 * Purchase-invoice schedule rules for the refund claim (TAX-044). They mirror
 * `vati_tax/submissions/invoices.py` so the taxpayer gets guidance before
 * sending; the server re-checks everything and recomputes the totals.
 *
 * Amounts are handled as whole cents (integers), never floats, so a total is
 * exact. Only arithmetic and dates are checked; no VAT rate is assumed.
 */
import { normaliseAmount, newIdempotencyKey } from './validation';

export const MAX_INVOICES = 200;

export interface InvoiceDraft {
  id: string;
  invoiceNumber: string;
  invoiceDate: string;
  supplierName: string;
  supplierTin: string;
  net: string;
  vat: string;
  gross: string;
  paid: string;
  invoiceEvidenceId: string;
  contractEvidenceId: string;
}

export type InvoiceField = Exclude<keyof InvoiceDraft, 'id'>;
export type RowErrors = Record<number, Partial<Record<InvoiceField, string>>>;

export function emptyInvoice(invoiceEvidenceId = ''): InvoiceDraft {
  return {
    id: newIdempotencyKey('row'),
    invoiceNumber: '',
    invoiceDate: '',
    supplierName: '',
    supplierTin: '',
    net: '',
    vat: '',
    gross: '',
    paid: '',
    invoiceEvidenceId,
    contractEvidenceId: '',
  };
}

/** Whole cents for a typed amount, or null when it is not a valid amount. */
export function cents(raw: string): number | null {
  const amount = normaliseAmount(raw);
  if (amount === null) return null;
  const [whole, frac] = amount.split('.');
  return Number(whole) * 100 + Number(frac);
}

export function formatCents(value: number): string {
  const whole = Math.floor(value / 100);
  const frac = String(value % 100).padStart(2, '0');
  return `${whole}.${frac}`;
}

/** Last day of a `YYYY-MM` period, as `YYYY-MM-DD`, or null. */
export function periodEnd(period: string): string | null {
  const match = /^(\d{4})-(\d{2})$/.exec(period.trim());
  if (!match) return null;
  const last = new Date(Date.UTC(Number(match[1]), Number(match[2]), 0)).getUTCDate();
  return `${match[1]}-${match[2]}-${String(last).padStart(2, '0')}`;
}

function isoToday(today: Date): string {
  return today.toISOString().slice(0, 10);
}

/** Per-row problems plus a schedule-level message; empty when the schedule is valid. */
export function validateSchedule(
  rows: InvoiceDraft[],
  options: { period: string; uploaded: Set<string>; today?: Date },
): { rows: RowErrors; general?: string } {
  const result: { rows: RowErrors; general?: string } = { rows: {} };
  if (rows.length === 0) {
    result.general = 'Add at least one purchase invoice.';
    return result;
  }
  if (rows.length > MAX_INVOICES) {
    result.general = `Add at most ${MAX_INVOICES} invoices.`;
    return result;
  }
  const end = periodEnd(options.period);
  const today = isoToday(options.today ?? new Date());
  const seen = new Map<string, number>();
  rows.forEach((row, index) => {
    const errors: Partial<Record<InvoiceField, string>> = {};
    const number = row.invoiceNumber.trim();
    const supplier = row.supplierName.trim();
    if (!number) errors.invoiceNumber = 'Enter the invoice number.';
    if (!supplier) errors.supplierName = "Enter the supplier's name.";
    const date = row.invoiceDate.trim();
    if (!date) errors.invoiceDate = 'Enter the invoice date.';
    else if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(date))) {
      errors.invoiceDate = 'Enter the date as YYYY-MM-DD.';
    } else if (date > today) errors.invoiceDate = 'The invoice date cannot be in the future.';
    else if (end && date > end) {
      errors.invoiceDate = 'The invoice date is after the end of the claim period.';
    }
    const amounts: Partial<Record<'net' | 'vat' | 'gross', number>> = {};
    for (const field of ['net', 'vat', 'gross'] as const) {
      if (row[field].trim() === '') errors[field] = 'Enter the amount.';
      else {
        const value = cents(row[field]);
        if (value === null) {
          errors[field] = 'Enter numbers only, with up to two decimal places, for example 1500.00.';
        } else amounts[field] = value;
      }
    }
    if (
      amounts.net !== undefined &&
      amounts.vat !== undefined &&
      amounts.gross !== undefined &&
      amounts.net + amounts.vat !== amounts.gross
    ) {
      errors.gross = 'The amount excluding VAT plus the VAT must equal the total.';
    }
    if (row.paid.trim() !== '' && cents(row.paid) === null) {
      errors.paid = 'Enter numbers only, with up to two decimal places, or leave it blank.';
    }
    if (!row.invoiceEvidenceId) {
      errors.invoiceEvidenceId = 'Choose the uploaded copy of this invoice.';
    } else if (!options.uploaded.has(row.invoiceEvidenceId)) {
      errors.invoiceEvidenceId = 'Choose a document you uploaded for this claim.';
    }
    if (row.contractEvidenceId && !options.uploaded.has(row.contractEvidenceId)) {
      errors.contractEvidenceId = 'Choose a document you uploaded for this claim.';
    }
    if (number && supplier && !errors.invoiceNumber) {
      const key = `${supplier.toLowerCase()}\u0000${number.toLowerCase()}`;
      const first = seen.get(key);
      if (first !== undefined) {
        errors.invoiceNumber = `This invoice is already listed in row ${first + 1}.`;
      } else seen.set(key, index);
    }
    if (Object.keys(errors).length) result.rows[index] = errors;
  });
  return result;
}

/** Totals of the valid amounts typed so far (for display; the server recomputes). */
export function scheduleTotals(rows: InvoiceDraft[]): { net: string; vat: string; gross: string } {
  const sum = (field: 'net' | 'vat' | 'gross') =>
    formatCents(rows.reduce((total, row) => total + (cents(row[field]) ?? 0), 0));
  return { net: sum('net'), vat: sum('vat'), gross: sum('gross') };
}

/** The server's row shape (`vati_tax.submissions.invoices`). */
export function toPayload(rows: InvoiceDraft[]) {
  return rows.map((row) => ({
    invoice_number: row.invoiceNumber.trim(),
    invoice_date: row.invoiceDate.trim(),
    supplier_name: row.supplierName.trim(),
    supplier_tin: row.supplierTin.trim() || null,
    net_amount: normaliseAmount(row.net) ?? row.net.trim(),
    vat_amount: normaliseAmount(row.vat) ?? row.vat.trim(),
    gross_amount: normaliseAmount(row.gross) ?? row.gross.trim(),
    amount_paid: row.paid.trim() === '' ? null : (normaliseAmount(row.paid) ?? row.paid.trim()),
    invoice_evidence_id: row.invoiceEvidenceId,
    contract_evidence_id: row.contractEvidenceId || null,
  }));
}

const SERVER_ROW_FIELDS: Record<string, InvoiceField> = {
  invoice_number: 'invoiceNumber',
  invoice_date: 'invoiceDate',
  supplier_name: 'supplierName',
  supplier_tin: 'supplierTin',
  net_amount: 'net',
  vat_amount: 'vat',
  gross_amount: 'gross',
  amount_paid: 'paid',
  invoice_evidence_id: 'invoiceEvidenceId',
  contract_evidence_id: 'contractEvidenceId',
};

/** Server field errors `invoices.N.field` (and `invoices`) placed on the schedule. */
export function mapScheduleErrors(fieldErrors: { field: string; message: string }[]): {
  rows: RowErrors;
  general?: string;
} {
  const out: { rows: RowErrors; general?: string } = { rows: {} };
  for (const error of fieldErrors) {
    const match = /^invoices\.(\d+)\.(\w+)$/.exec(error.field);
    if (match) {
      const field = SERVER_ROW_FIELDS[match[2]];
      if (field) {
        out.rows[Number(match[1])] ??= {};
        out.rows[Number(match[1])][field] ??= error.message;
      }
    } else if (error.field === 'invoices') out.general ??= error.message;
  }
  return out;
}
