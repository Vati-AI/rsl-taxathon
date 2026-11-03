import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { content as frappeContent } from 'frappe-ui/tailwind';
import type { Config } from 'tailwindcss';
import vatiPreset from '../shared/design/tailwind.preset';

const here = path.dirname(fileURLToPath(import.meta.url));
// Tailwind globs need forward slashes, including on Windows.
const glob = (pattern: string) => path.join(here, pattern).split(path.sep).join('/');

// The app imports only the stable `frappe-ui` entry, so skip the globs for its
// docs theme and experimental components; scanning them emits unused classes.
const usedFrappeContent = frappeContent.filter(
  (pattern) => !pattern.includes('/experimental/') && !pattern.includes('/vitepress/'),
);

export default {
  presets: [vatiPreset],
  content: [...usedFrappeContent, glob('index.html'), glob('src/**/*.{vue,ts}')],
} satisfies Config;
