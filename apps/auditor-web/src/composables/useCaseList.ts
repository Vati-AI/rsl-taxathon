import { shallowRef } from 'vue';
import { ApiError } from '../api/errors';
import { listAssignedCases, type Case } from '../api/vati';
import type { LoadState } from './useLoader';

/**
 * The officer's assigned cases (GET /api/v1/cases), loaded once and shared by the
 * sidebar view counts, the overview and the queue table (TAX-147), so a count in the
 * sidebar is always the number of rows the same view shows. Pages refresh it when they
 * open; a refresh keeps the rows on screen while it runs.
 */
const state = shallowRef<LoadState<Case[]>>({ status: 'loading' });
let controller: AbortController | null = null;

async function run(silent: boolean): Promise<void> {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  const keep = silent && state.value.status === 'ready';
  if (!keep) state.value = { status: 'loading' };
  try {
    const data = await listAssignedCases(fetch, current.signal);
    if (!current.signal.aborted) state.value = { status: 'ready', data };
  } catch (error) {
    if (current.signal.aborted || keep) return;
    state.value = {
      status: 'error',
      error:
        error instanceof ApiError
          ? error
          : new ApiError('unavailable', 'Something went wrong while loading your cases.'),
    };
  }
}

export function useCaseList() {
  return {
    state,
    /** Load from scratch, showing the loading state. */
    reload: () => run(false),
    /** Load again, keeping what is shown until the new list arrives. */
    refresh: () => run(true),
  };
}

/** Forget the list (another acting officer in fixture mode, or a test). */
export function resetCaseList(): void {
  controller?.abort();
  controller = null;
  state.value = { status: 'loading' };
}
