import { afterEach, describe, expect, it, vi } from 'vitest';

// Pure logic only; avoid loading the whole component library.
vi.mock('./frappe-call', () => ({ call: vi.fn() }));
import { errorFromResponse, PortalError, submitRefundClaim, uploadEvidence } from './api';
import { precheckDocument, uploadErrorMessage } from './documents';
import type { EvidenceView } from './types';
import { useDocumentUploads } from './uploads';
import { mapServerErrors, newIdempotencyKey, stepForField } from './validation';

const TENANT = '10000000-0000-4000-8000-000000000001';

function file(name: string, size = 10): File {
  return new File([new Uint8Array(size)], name);
}

function view(id: string): EvidenceView {
  return {
    evidence_id: id,
    document_type: 'invoice',
    media_type: 'application/pdf',
    size_bytes: 10,
    document_sha256: 'a'.repeat(64),
    received_at: '2026-11-04T10:00:00Z',
  };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('precheckDocument', () => {
  it('accepts the evidence store formats up to 25 MB', () => {
    for (const name of ['a.pdf', 'b.PNG', 'c.jpg', 'd.jpeg', 'e.csv', 'f.xlsx']) {
      expect(precheckDocument({ name, size: 100 })).toBeNull();
    }
    expect(precheckDocument({ name: 'a.pdf', size: 26_214_400 })).toBeNull();
  });

  it('refuses other types, empty files and oversized files with guidance', () => {
    expect(precheckDocument({ name: 'tool.exe', size: 10 })).toMatch(/PDF, PNG, JPEG, CSV or XLSX/);
    expect(precheckDocument({ name: 'noextension', size: 10 })).toMatch(/cannot be used/);
    expect(precheckDocument({ name: 'a.pdf', size: 0 })).toMatch(/empty/);
    expect(precheckDocument({ name: 'a.pdf', size: 26_214_401 })).toMatch(/25 MB/);
  });

  it('explains every server refusal reason in plain language', () => {
    expect(uploadErrorMessage('extension_mismatch')).toMatch(/does not match its name/);
    expect(uploadErrorMessage('malformed_document')).toMatch(/could not be read/);
    expect(uploadErrorMessage('too_large')).toMatch(/25 MB/);
    expect(uploadErrorMessage('something_new')).toMatch(/could not be uploaded/);
  });
});

describe('useDocumentUploads', () => {
  it('uploads with the tenant and keeps the evidence ID', async () => {
    const upload = vi.fn().mockResolvedValue(view('e1'));
    const uploads = useDocumentUploads(() => TENANT, upload);
    expect(uploads.add(file('invoice.pdf'), 'invoice')).toBeNull();
    expect(uploads.pending.value).toBe(true);
    await settle();
    expect(upload).toHaveBeenCalledWith(expect.any(File), 'invoice', expect.any(String), TENANT);
    expect(uploads.evidenceIds.value).toEqual(['e1']);
    expect(uploads.blocker()).toBeNull();
  });

  it('refuses a bad file before uploading anything', () => {
    const upload = vi.fn();
    const uploads = useDocumentUploads(() => TENANT, upload);
    expect(uploads.add(file('tool.exe'), 'invoice')).toMatch(/cannot be used/);
    expect(upload).not.toHaveBeenCalled();
    expect(uploads.items).toHaveLength(0);
  });

  it('retries a failed upload with the same idempotency key', async () => {
    const upload = vi
      .fn()
      .mockRejectedValueOnce(new PortalError('unavailable', 'offline'))
      .mockResolvedValueOnce(view('e2'));
    const uploads = useDocumentUploads(() => TENANT, upload);
    uploads.add(file('invoice.pdf'), 'invoice');
    await settle();
    expect(uploads.items[0].status).toBe('failed');
    expect(uploads.items[0].error).toMatch(/did not finish/);
    expect(uploads.blocker()).toMatch(/Retry or remove/);

    uploads.retry(uploads.items[0].id);
    await settle();
    expect(upload.mock.calls[1][2]).toBe(upload.mock.calls[0][2]);
    expect(uploads.evidenceIds.value).toEqual(['e2']);
  });

  it('shows the server reason for a rejected document', async () => {
    const upload = vi
      .fn()
      .mockRejectedValue(
        new PortalError('invalid', 'x', 'invalid_domain_input', 'extension_mismatch'),
      );
    const uploads = useDocumentUploads(() => TENANT, upload);
    uploads.add(file('renamed.pdf'), 'invoice');
    await settle();
    expect(uploads.items[0].error).toMatch(/does not match its name/);
  });

  it('needs at least one uploaded document and nothing pending', async () => {
    const uploads = useDocumentUploads(() => TENANT, vi.fn().mockResolvedValue(view('e3')));
    expect(uploads.blocker()).toMatch(/at least one/);
    uploads.add(file('a.pdf'), 'invoice');
    expect(uploads.blocker()).toMatch(/finish uploading/);
    await settle();
    uploads.remove(uploads.items[0].id);
    expect(uploads.blocker()).toMatch(/at least one/);
  });

  it('does not upload without a single taxpayer scope', async () => {
    const upload = vi.fn();
    const uploads = useDocumentUploads(() => null, upload);
    uploads.add(file('a.pdf'), 'invoice');
    await settle();
    expect(upload).not.toHaveBeenCalled();
    expect(uploads.items[0].status).toBe('failed');
  });
});

describe('errorFromResponse', () => {
  it('reads portal field errors from the API-standard error body', () => {
    const err = errorFromResponse(422, {
      error: {
        code: 'invalid_domain_input',
        field_errors: [{ field: 'evidence_ids', code: 'not_found', message: 'Upload again.' }],
      },
    });
    expect([err.kind, err.code, err.fieldErrors[0].field]).toEqual([
      'invalid',
      'invalid_domain_input',
      'evidence_ids',
    ]);
  });

  it('reads the upload reason from the Frappe message title', () => {
    const messages = JSON.stringify([
      JSON.stringify({ message: 'The file is larger', title: 'invalid_domain_input:too_large' }),
    ]);
    const err = errorFromResponse(422, {
      exc_type: 'EvidenceRequestError',
      _server_messages: messages,
    });
    expect([err.kind, err.code, err.reason]).toEqual([
      'invalid',
      'invalid_domain_input',
      'too_large',
    ]);
  });

  it('never treats a refusal or outage as success', () => {
    expect(errorFromResponse(403, { exc_type: 'TenantScopeDenied' }).kind).toBe('denied');
    expect(errorFromResponse(404, null).kind).toBe('not_found');
    expect(errorFromResponse(500, null).kind).toBe('unavailable');
    expect(errorFromResponse(422, { _server_messages: 'not json' }).reason).toBeNull();
  });
});

describe('live calls', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.csrf_token;
  });

  it('upload sends multipart with the CSRF token and tenant header', async () => {
    window.csrf_token = 'token-1';
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ message: view('e4') }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await uploadEvidence(file('a.pdf'), 'invoice', 'document-key-123456', TENANT);
    expect(result.evidence_id).toBe('e4');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/method/vati_tax.evidence.api.upload_evidence');
    expect(init.headers['X-Frappe-CSRF-Token']).toBe('token-1');
    expect(init.headers['X-Vati-Tenant-ID']).toBe(TENANT);
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get('idempotency_key')).toBe('document-key-123456');
  });

  it('a lost connection on send is reported as unknown, not as success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(
      submitRefundClaim({
        idempotency_key: 'claim-key-1234567890',
        period: '2026-09',
        claimed_amount: '15000.00',
        claim_reason: 'excess_input_vat',
        explanation: null,
        contact_email: null,
        evidence_ids: ['e1'],
        declaration: true,
        tenant_id: TENANT,
      }),
    ).rejects.toMatchObject({ kind: 'unavailable' });
  });

  it('rejects a send response that is not live claim data', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ message: { mode: 'fixture' } }), { status: 200 }),
        ),
    );
    await expect(
      submitRefundClaim({
        idempotency_key: 'claim-key-1234567890',
        period: '2026-09',
        claimed_amount: '1.00',
        claim_reason: 'other',
        explanation: 'x',
        contact_email: null,
        evidence_ids: ['e1'],
        declaration: true,
        tenant_id: TENANT,
      }),
    ).rejects.toMatchObject({ kind: 'unavailable' });
  });
});

describe('form helpers', () => {
  it('maps server fields onto form fields and steps', () => {
    const { errors, other } = mapServerErrors([
      { field: 'claimed_amount', message: 'Amount problem' },
      { field: 'evidence_ids', message: 'Document problem' },
      { field: 'idempotency_key', message: 'Reload' },
    ]);
    expect(errors).toEqual({ amount: 'Amount problem', documents: 'Document problem' });
    expect(other).toEqual(['Reload']);
    expect(stepForField('amount')).toBe('details');
    expect(stepForField('documents')).toBe('documents');
    expect(stepForField('declaration')).toBe('review');
  });

  it('makes idempotency keys the server accepts', () => {
    const key = newIdempotencyKey('claim');
    expect(key).toMatch(/^[A-Za-z0-9:._-]{16,255}$/);
    expect(newIdempotencyKey('claim')).not.toBe(key);
  });
});
