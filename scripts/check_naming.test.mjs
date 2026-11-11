// Self-test for the TAX-150 naming check: it catches names in each kind of user-visible
// text and leaves machine values, imports, comments and exempt regions alone.
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { EXEMPT, scanAll, scanFile } from './check_naming.mjs';

function scan(name, code) {
  const dir = mkdtempSync(path.join(tmpdir(), 'naming-'));
  try {
    const file = path.join(dir, name);
    writeFileSync(file, code);
    return scanFile(file).map((h) => `${h.where}:${h.name}`);
  } finally {
    rmSync(dir, { recursive: true });
  }
}

test('flags template text, static attributes and string literals', () => {
  const hits = scan(
    'A.vue',
    `<script setup lang="ts">
const label = 'Applied in Jube';
</script>
<template>
  <p>Sent to OpenAleph</p>
  <input placeholder="Search yente" />
  <Thing :title="ok ? 'Docling' : 'x'" description="From Frappe" />
  <span>{{ 'LangGraph run' }}</span>
</template>`,
  );
  assert.deepEqual(hits.sort(), [
    'attribute description:Frappe',
    'attribute placeholder:yente',
    'script:Jube',
    'template expression:Docling',
    'template expression:LangGraph',
    'template text:OpenAleph',
  ]);
});

test('ignores imports, comments, machine values and opted-out lines', () => {
  const hits = scan(
    'b.ts',
    `import { Button } from 'frappe-ui';
// Jube is mentioned in a comment only
const source = 'jube';
const op = 'docling.submit';
// naming-allowed: header name
const header = 'X-Frappe-CSRF-Token';
`,
  );
  assert.deepEqual(hits, []);
});

test('the repository passes and every exemption has a reason', () => {
  assert.deepEqual(scanAll(), []);
  for (const reason of Object.values(EXEMPT)) assert.ok(reason.length > 10);
});
