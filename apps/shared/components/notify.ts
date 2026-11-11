/**
 * Toasts for the Vati Workspace kit (TAX-145): short confirmations of something that just
 * happened ("Evidence request sent"). Built on frappe-ui's toast. Errors that need the
 * reader to act stay on the page (InlineError); a toast is never the only place an
 * error is reported.
 */
import { toast } from 'frappe-ui';

export type ToastTone = 'success' | 'info' | 'warning' | 'error';

export function notify(tone: ToastTone, message: string): void {
  const show = {
    success: toast.success,
    info: toast.info,
    warning: toast.warning,
    error: toast.error,
  }[tone];
  show(message);
}
