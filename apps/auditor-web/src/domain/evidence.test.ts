import { describe, expect, it } from 'vitest';
import { entId, evId, makeEntity, makeEvidence, makeFinding, sha } from '../testing/makeRecords';
import {
  citationStatus,
  citedBy,
  evidenceIndex,
  extractionLabel,
  groupFindings,
  kindInfo,
  locatorLabel,
  relationshipView,
  shortHash,
  sortedEvidence,
  versionHistory,
} from './evidence';

const ref = (n: number, pair: string, page: number | null = 1, record: string | null = null) => ({
  evidence_id: evId(n),
  document_sha256: sha(pair),
  locator: { page, record },
});

describe('citations', () => {
  const index = evidenceIndex([makeEvidence(1, { document_sha256: sha('2a') })]);

  it('trusts only citations whose hash matches the stored original', () => {
    expect(citationStatus(ref(1, '2a'), index)).toBe('ok');
    expect(citationStatus(ref(1, 'ff'), index)).toBe('hash_mismatch');
    expect(citationStatus(ref(9, '2a'), index)).toBe('not_on_file');
  });

  it('labels page and record locators', () => {
    expect(locatorLabel({ page: 3, record: null })).toBe('Page 3');
    expect(locatorLabel({ page: null, record: 'row 4' })).toBe('Record row 4');
    expect(locatorLabel({ page: 2, record: 'txn 1' })).toBe('Page 2, Record txn 1');
    expect(locatorLabel({ page: null, record: null })).toBe('Whole document');
  });

  it('lists the findings that cite a document with each cited location', () => {
    const findings = [
      makeFinding(1, { evidence_refs: [ref(1, '2a', 1), ref(1, '2a', 4), ref(2, '2b')] }),
      makeFinding(2, { evidence_refs: [ref(2, '2b')] }),
    ];
    const cited = citedBy(findings, evId(1));
    expect(cited).toHaveLength(1);
    expect(cited[0].locators.map((l) => l.page)).toEqual([1, 4]);
  });

  it('shortens hashes for tables only', () => {
    expect(shortHash(sha('2a'))).toBe('2a2a2a2a…2a2a');
  });
});

describe('versions and extraction', () => {
  const v1 = makeEvidence(24, {
    extraction: { status: 'failed', extractor_version: null, error_code: 'unreadable_scan' },
  });
  const v2 = makeEvidence(23, { version: 2, supersedes_evidence_id: evId(24) });
  const other = makeEvidence(21);

  it('builds the version history from either end', () => {
    expect(versionHistory([v2, other, v1], evId(24)).map((e) => e.version)).toEqual([1, 2]);
    expect(versionHistory([v2, other, v1], evId(23)).map((e) => e.version)).toEqual([1, 2]);
    expect(versionHistory([v2], evId(99))).toEqual([]);
  });

  it('lists current versions before superseded ones', () => {
    expect(sortedEvidence([v1, v2, other])[2].evidence_id).toBe(evId(24));
  });

  it('describes failed extraction as a processing problem, never an empty document', () => {
    expect(extractionLabel(v1)).toEqual({ label: 'Extraction failed', theme: 'red' });
    const partial = makeEvidence(5, {
      extraction: { status: 'completed', extractor_version: 'x', error_code: 'partial_extraction' },
    });
    expect(extractionLabel(partial).label).toBe('Partly extracted');
  });
});

describe('findings', () => {
  it('keeps discrepancies and missing evidence as separate groups, discrepancies first', () => {
    const groups = groupFindings([
      makeFinding(1, { kind: 'observed_fact' }),
      makeFinding(2, { kind: 'missing_evidence' }),
      makeFinding(3, { kind: 'contradictory_evidence' }),
    ]);
    expect(groups.map((g) => g.kind)).toEqual([
      'contradictory_evidence',
      'missing_evidence',
      'observed_fact',
    ]);
    expect(groups[0].label).toBe('Records disagree');
    expect(groups[1].label).toBe('Evidence missing');
  });

  it('uses neutral wording that never states a conclusion about the taxpayer', () => {
    const words = ['fraud', 'guilt', 'evasion', 'abuse', 'suspicious', 'offence'];
    for (const kind of [
      'observed_fact',
      'inference',
      'missing_evidence',
      'unverified_evidence',
      'contradictory_evidence',
    ] as const) {
      const text = `${kindInfo(kind).label} ${kindInfo(kind).description}`.toLowerCase();
      for (const word of words) expect(text).not.toContain(word);
    }
  });
});

describe('relationship view', () => {
  const evidence = [makeEvidence(25), makeEvidence(26), makeEvidence(27)];
  const entities = [
    makeEntity(21, 'Company', { name: ['Synthetic Border Retail'], jurisdiction: ['ls'] }, [
      evId(25),
    ]),
    makeEntity(23, 'Company', { name: ['Synthetic Group Holdings'] }, [evId(25)]),
    makeEntity(22, 'Ownership', { owner: [entId(23)], asset: [entId(21)], percentage: ['60'] }, [
      evId(25),
    ]),
    makeEntity(26, 'Ownership', { owner: [entId(23)], asset: [entId(21)], percentage: ['55'] }, [
      evId(26),
    ]),
    makeEntity(31, 'Ownership', { owner: [entId(99)], asset: [entId(21)], percentage: ['10'] }, [
      evId(27),
    ]),
    makeEntity(
      30,
      'Payment',
      { payer: [entId(21)], beneficiary: [entId(23)], amount: ['15000.00'], currency: ['LSL'] },
      [evId(27), evId(29)],
    ),
  ];
  const conflict = makeFinding(22, {
    kind: 'contradictory_evidence',
    evidence_refs: [ref(25, '2a', null, 'row 4'), ref(26, '2a', 3)],
  });
  const missing = makeFinding(23, { kind: 'missing_evidence', evidence_refs: [ref(27, '2a')] });
  const view = relationshipView({ evidence, entities, findings: [conflict, missing] });

  it('names owners, assets and payment parties from case entities', () => {
    expect(view.parties.map((p) => p.name)).toEqual([
      'Synthetic Border Retail',
      'Synthetic Group Holdings',
    ]);
    expect(view.ownership[0]).toMatchObject({
      owner: 'Synthetic Group Holdings',
      asset: 'Synthetic Border Retail',
      percentage: '60',
    });
    expect(view.ownership[2].owner).toBe('Party not in this case');
    expect(view.payments[0]).toMatchObject({
      payer: 'Synthetic Border Retail',
      amount: '15000.00',
    });
  });

  it('marks ownership uncertain only when an unresolved finding cites its records', () => {
    expect(view.ownership[0].disputes.map((f) => f.finding_id)).toEqual([conflict.finding_id]);
    expect(view.ownership[1].disputes.map((f) => f.finding_id)).toEqual([conflict.finding_id]);
    // A missing-evidence finding is a gap, not a disagreement about ownership.
    expect(view.ownership[2].disputes).toEqual([]);
  });

  it('keeps both disputed percentages exactly as recorded', () => {
    expect(view.ownership.slice(0, 2).map((row) => row.percentage)).toEqual(['60', '55']);
  });

  it('reports cited records that are not on file', () => {
    expect(view.payments[0].missingEvidence).toEqual([evId(29)]);
    expect(view.ownership[0].missingEvidence).toEqual([]);
  });
});
