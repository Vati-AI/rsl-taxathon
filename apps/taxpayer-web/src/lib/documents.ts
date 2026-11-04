/**
 * Supporting-document rules for the refund claim (TAX-043). Limits match the
 * TAX-032 evidence store (`vati_tax/evidence/rules.py`): PDF, PNG, JPEG, CSV or
 * XLSX, 1 byte to 25 MiB. The browser checks first so the taxpayer gets an
 * answer without waiting; the server checks the actual file content again.
 */

export const DOCUMENT_TYPES = [
  { value: 'invoice', label: 'Tax invoice' },
  { value: 'import_declaration', label: 'Import declaration' },
  { value: 'bank_statement', label: 'Bank statement' },
  { value: 'contract', label: 'Contract' },
  { value: 'financial_statement', label: 'Financial statement' },
  { value: 'other', label: 'Other document' },
] as const;

export const ACCEPTED_EXTENSIONS = ['pdf', 'png', 'jpg', 'jpeg', 'csv', 'xlsx'] as const;
export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.map((e) => `.${e}`).join(',');
export const MAX_DOCUMENT_BYTES = 26_214_400;
export const MAX_DOCUMENTS = 50;

const TYPE_LIST = 'PDF, PNG, JPEG, CSV or XLSX';
export const FORMAT_HINT = `${TYPE_LIST}, up to 25 MB each.`;
const WRONG_TYPE = `This file type cannot be used. Upload a ${TYPE_LIST} file.`;

export function documentTypeLabel(value: string): string {
  return DOCUMENT_TYPES.find((t) => t.value === value)?.label ?? 'Document';
}

/** A browser-side reason to refuse `file` before uploading, or null if it may be sent. */
export function precheckDocument(file: { name: string; size: number }): string | null {
  const extension = /\.([A-Za-z0-9]+)$/.exec(file.name)?.[1]?.toLowerCase();
  if (!extension || !(ACCEPTED_EXTENSIONS as readonly string[]).includes(extension)) {
    return WRONG_TYPE;
  }
  if (file.size === 0) return 'This file is empty. Choose the saved document again.';
  if (file.size > MAX_DOCUMENT_BYTES) {
    return 'This file is larger than 25 MB. Upload a smaller copy, or split the document.';
  }
  return null;
}

/** Plain-language text for a server upload refusal (`code:reason` from TAX-032). */
export function uploadErrorMessage(reason: string | null): string {
  switch (reason) {
    case 'too_large':
      return 'This file is larger than 25 MB. Upload a smaller copy, or split the document.';
    case 'empty_document':
      return 'This file is empty. Choose the saved document again.';
    case 'disallowed_type':
      return WRONG_TYPE;
    case 'extension_mismatch':
    case 'media_type_mismatch':
      return 'The file content does not match its name (for example an image renamed to .pdf). Upload the original file.';
    case 'malformed_document':
      return 'This file could not be read. It may be damaged; save it again and upload the new copy.';
    case 'stored_bytes_changed':
      return 'This image could not be stored unchanged. Upload it as a PDF instead.';
    case 'missing_filename':
    case 'invalid_filename':
      return 'The file name cannot be used. Rename the file and try again.';
    default:
      return 'This file could not be uploaded. Check it and try again.';
  }
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
