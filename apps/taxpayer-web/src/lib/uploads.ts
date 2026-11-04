import { computed, reactive } from 'vue';
import { PortalError, uploadEvidence } from './api';
import { MAX_DOCUMENTS, precheckDocument, uploadErrorMessage } from './documents';
import type { EvidenceView } from './types';
import { newIdempotencyKey } from './validation';

export type UploadStatus = 'uploading' | 'uploaded' | 'failed';

export interface UploadItem {
  id: string;
  file: File;
  name: string;
  size: number;
  documentType: string;
  /** Kept across retries so a repeated upload is replayed, never stored twice. */
  key: string;
  status: UploadStatus;
  evidenceId: string | null;
  error: string | null;
}

type Uploader = (
  file: File,
  documentType: string,
  key: string,
  tenantId: string,
) => Promise<EvidenceView>;

function failureMessage(error: unknown): string {
  if (!(error instanceof PortalError)) return uploadErrorMessage(null);
  if (error.kind === 'invalid') return uploadErrorMessage(error.reason);
  if (error.kind === 'unavailable') {
    return 'The upload did not finish. Check your connection and retry.';
  }
  if (error.kind === 'unauthenticated') return error.message;
  return 'You cannot upload documents for this taxpayer.';
}

/**
 * Supporting documents for one claim. Each file is checked in the browser,
 * then uploaded as private evidence (TAX-032) under its own idempotency key.
 * Removing a document only leaves it out of this claim; stored evidence is
 * immutable and stays private to the taxpayer.
 */
export function useDocumentUploads(
  tenantId: () => string | null,
  upload: Uploader = uploadEvidence,
) {
  const items = reactive<UploadItem[]>([]);

  async function send(item: UploadItem): Promise<void> {
    const tenant = tenantId();
    item.status = 'uploading';
    item.error = null;
    if (!tenant) {
      item.status = 'failed';
      item.error = 'You cannot upload documents for this taxpayer.';
      return;
    }
    try {
      const view = await upload(item.file, item.documentType, item.key, tenant);
      item.evidenceId = view.evidence_id;
      item.status = 'uploaded';
    } catch (error) {
      item.status = 'failed';
      item.error = failureMessage(error);
      // A key reused with different content is refused; a fresh key lets a retry proceed.
      if (error instanceof PortalError && error.code === 'idempotency_conflict') {
        item.key = newIdempotencyKey('document');
      }
    }
  }

  /** Check and start uploading `file`; returns a reason when it is refused before upload. */
  function add(file: File, documentType: string): string | null {
    if (items.length >= MAX_DOCUMENTS) return `Attach at most ${MAX_DOCUMENTS} documents.`;
    const problem = precheckDocument(file);
    if (problem) return problem;
    items.push({
      id: newIdempotencyKey('item'),
      file,
      name: file.name,
      size: file.size,
      documentType,
      key: newIdempotencyKey('document'),
      status: 'uploading',
      evidenceId: null,
      error: null,
    });
    void send(items[items.length - 1]);
    return null;
  }

  function retry(id: string): void {
    const item = items.find((i) => i.id === id);
    if (item && item.status === 'failed') void send(item);
  }

  function remove(id: string): void {
    const index = items.findIndex((i) => i.id === id);
    if (index >= 0) items.splice(index, 1);
  }

  const evidenceIds = computed(() =>
    items.filter((i) => i.status === 'uploaded' && i.evidenceId).map((i) => i.evidenceId!),
  );
  const pending = computed(() => items.some((i) => i.status === 'uploading'));
  const failed = computed(() => items.some((i) => i.status === 'failed'));

  /** Why the documents step cannot be completed yet, or null. */
  function blocker(): string | null {
    if (pending.value) return 'Wait for your documents to finish uploading.';
    if (failed.value) return 'Retry or remove the documents that could not be uploaded.';
    if (evidenceIds.value.length === 0) {
      return 'Upload at least one supporting document, such as the invoices behind the claim.';
    }
    return null;
  }

  return { items, add, retry, remove, evidenceIds, pending, failed, blocker };
}

export type DocumentUploads = ReturnType<typeof useDocumentUploads>;
