import { afterEach, describe, expect, it, vi } from 'vitest';

// Pure logic only; avoid loading the whole component library.
const callMock = vi.fn();
vi.mock('./frappe-call', () => ({ call: (...args: unknown[]) => callMock(...args) }));
import { fetchEvidenceRequest, respondToEvidenceRequest } from './api';
import {
  mapResponseErrors,
  requestedTypeLabel,
  requestStatusDisplay,
  validateAnswers,
  type ItemState,
} from './responses';

const answered: ItemState = {
  uploaded: 1,
  pending: false,
  failed: false,
  cannotProvide: false,
  reason: '',
};

describe('requestStatusDisplay', () => {
  it('shows only the taxpayer-facing statuses, text plus colour', () => {
    expect(requestStatusDisplay('awaiting_response')).toEqual({
      label: 'Response needed',
      theme: 'amber',
    });
    expect(requestStatusDisplay('response_sent').label).toBe('Response sent');
    expect(requestStatusDisplay('no_longer_needed').label).toBe('No longer needed');
  });

  it('never guesses an unknown or internal status', () => {
    expect(requestStatusDisplay('recorded').label).toBe('Status unavailable');
    expect(requestStatusDisplay('under_internal_review').label).toBe('Status unavailable');
  });

  it('labels requested document types', () => {
    expect(requestedTypeLabel('bank_statement')).toBe('Bank statement');
    expect(requestedTypeLabel('unknown')).toBe('Document');
  });
});

describe('validateAnswers', () => {
  it('accepts documents or a reason for every item', () => {
    expect(
      validateAnswers({
        0: answered,
        1: { ...answered, uploaded: 0, cannotProvide: true, reason: 'Supplier closed.' },
      }),
    ).toEqual({});
  });

  it('flags unanswered, pending, failed and unexplained items', () => {
    const errors = validateAnswers({
      0: { ...answered, uploaded: 0 },
      1: { ...answered, pending: true },
      2: { ...answered, failed: true },
      3: { ...answered, uploaded: 0, cannotProvide: true, reason: '  ' },
      4: { ...answered, cannotProvide: true, reason: 'x'.repeat(1001) },
    });
    expect(errors[0]).toMatch(/Upload a document/);
    expect(errors[1]).toMatch(/finish uploading/);
    expect(errors[2]).toMatch(/Retry or remove/);
    expect(errors[3]).toMatch(/Explain why/);
    expect(errors[4]).toMatch(/1000 characters/);
  });
});

describe('mapResponseErrors', () => {
  it('places item, message and general errors', () => {
    const mapped = mapResponseErrors([
      { field: 'items.1', message: 'Item problem' },
      { field: 'message', message: 'Too long' },
      { field: 'evidence_ids', message: 'A document could not be found.' },
    ]);
    expect(mapped).toEqual({
      items: { 1: 'Item problem' },
      message: 'Too long',
      general: ['A document could not be found.'],
    });
  });
});

describe('evidence request calls', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    callMock.mockReset();
  });

  it('refuses a read that is not live data', async () => {
    callMock.mockResolvedValueOnce({ mode: 'fixture', synthetic: true, data: {} });
    await expect(fetchEvidenceRequest('r1')).rejects.toMatchObject({ kind: 'unavailable' });
  });

  it('reports a concealed request as not found', async () => {
    callMock.mockRejectedValueOnce({ exc_type: 'DoesNotExistError', status: 404 });
    await expect(fetchEvidenceRequest('r1')).rejects.toMatchObject({ kind: 'not_found' });
  });

  it('sends the answers with the key and reads field errors', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'invalid_domain_input',
            field_errors: [{ field: 'items.0', code: 'answer_required', message: 'Answer it.' }],
          },
        }),
        { status: 422 },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    await expect(
      respondToEvidenceRequest({
        request_id: 'r1',
        idempotency_key: 'response-key-1234567890',
        message: null,
        items: [{ item_index: 0, evidence_ids: [], unavailable_reason: null }],
      }),
    ).rejects.toMatchObject({ kind: 'invalid', fieldErrors: [{ field: 'items.0' }] });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/method/vati_tax.portal.api.respond_to_evidence_request');
    expect(JSON.parse(init.body as string).idempotency_key).toBe('response-key-1234567890');
  });
});
