import { describe, expect, it } from 'vitest';
import { groupKey, groupRequests } from './inbox';
import type { EvidenceRequestView } from './types';

const request = (id: string, status: EvidenceRequestView['status'], due: string | null) =>
  ({
    request_id: id,
    status,
    due_date: due,
    message_to_taxpayer: 'Synthetic',
    items: [],
    response: null,
  }) as EvidenceRequestView;

describe('evidence-request inbox (TAX-152)', () => {
  const today = '2026-11-12';

  it('groups open requests by reply-by date, then answered and closed', () => {
    const groups = groupRequests(
      [
        request('a', 'awaiting_response', '2026-11-30'),
        request('b', 'awaiting_response', '2026-11-10'),
        request('c', 'response_sent', '2026-11-01'),
        request('d', 'awaiting_response', '2026-11-19'),
        request('e', 'no_longer_needed', null),
        request('f', 'awaiting_response', '2026-11-12'),
        request('g', 'awaiting_response', null),
      ],
      today,
    );
    expect(groups.map((g) => [g.key, g.items.map((i) => i.request_id)])).toEqual([
      ['overdue', ['b']],
      ['this_week', ['f', 'd']],
      ['later', ['a']],
      ['no_date', ['g']],
      ['answered', ['c']],
      ['closed', ['e']],
    ]);
  });

  it('a request due today is not overdue; empty groups are left out', () => {
    expect(groupKey(request('x', 'awaiting_response', today), today)).toBe('this_week');
    expect(groupRequests([request('y', 'response_sent', null)], today).map((g) => g.key)).toEqual([
      'answered',
    ]);
  });
});
