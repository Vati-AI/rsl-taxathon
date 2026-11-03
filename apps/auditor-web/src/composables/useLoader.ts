import { onBeforeUnmount, shallowRef, watch, type WatchSource } from 'vue';
import { ApiError } from '../api/fixtures';

export type LoadState<T> =
  { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error'; error: ApiError };

/**
 * Load data with explicit loading/ready/error states. Stale responses are
 * aborted when the source changes or the component unmounts, so a slow earlier
 * request can never overwrite a newer one.
 */
export function useLoader<T>(load: (signal: AbortSignal) => Promise<T>, source?: WatchSource) {
  const state = shallowRef<LoadState<T>>({ status: 'loading' });
  let controller: AbortController | null = null;

  async function reload() {
    controller?.abort();
    const current = new AbortController();
    controller = current;
    state.value = { status: 'loading' };
    try {
      const data = await load(current.signal);
      if (!current.signal.aborted) state.value = { status: 'ready', data };
    } catch (error) {
      if (current.signal.aborted) return;
      state.value = {
        status: 'error',
        error:
          error instanceof ApiError
            ? error
            : new ApiError('unavailable', 'Something went wrong while loading this page.'),
      };
    }
  }

  if (source) watch(source, reload);
  onBeforeUnmount(() => controller?.abort());
  void reload();
  return { state, reload };
}
