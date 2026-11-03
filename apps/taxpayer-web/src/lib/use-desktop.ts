import { onBeforeUnmount, ref } from 'vue';

/** True at desktop widths. frappe-ui ships separate desktop and mobile shells. */
export function useDesktop(query = '(min-width: 768px)') {
  const media = window.matchMedia(query);
  const desktop = ref(media.matches);
  const update = (event: MediaQueryListEvent) => (desktop.value = event.matches);
  media.addEventListener('change', update);
  onBeforeUnmount(() => media.removeEventListener('change', update));
  return desktop;
}
