#!/usr/bin/env node
/**
 * Product-naming check (TAX-150, docs/DESIGN_STANDARD.md "Product identity").
 *
 * Users only ever see Vati Workspace names. This scans the text both apps can show — Vue
 * template text, static attributes, and string literals in templates and scripts — for
 * the open-source component names in BANNED_NAMES (the same list the TAX-144 browser
 * check uses) and fails if any is found. It runs in `npm run check`; the browser check
 * (tests/frontend/layout.spec.ts) covers the rendered pages.
 *
 * Not user-visible, so not scanned:
 * - comments, import paths and module names (`frappe-ui`);
 * - machine values: a literal that is one lower-case token such as `jube` or `docling.submit`
 *   (API fields, source systems and provenance keep component names on purpose);
 * - unit tests and test fixtures;
 * - the files in EXEMPT, which exist to name the components (open-source notices).
 *
 * A line can opt out with a `naming-allowed: <reason>` comment on it or the line above.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse as parseSfc } from '@vue/compiler-sfc';
import ts from 'typescript';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const BANNED_NAMES = ['Jube', 'OpenAleph', 'Frappe', 'Docling', 'yente', 'LangGraph', 'Groq'];
const PATTERN = new RegExp(`\\b(${BANNED_NAMES.join('|')})\\b`, 'i');
const ROOTS = ['apps/auditor-web/src', 'apps/taxpayer-web/src', 'apps/shared'];
const PAGES = [
  'apps/auditor-web/index.html',
  'apps/taxpayer-web/index.html',
  'backend/vati_tax/vati_tax/www/taxpayer.html',
];
/** Files whose purpose is to name the components; every entry needs a reason. */
export const EXEMPT = {
  'apps/shared/notices/components.ts': 'Open-source notices data (licence attribution)',
};
const SKIP = [/\.test\.ts$/, /\/testing\//, /\/e2e\//];
/** Attributes that never reach the user as text. */
const SILENT_ATTRS = new Set([
  'class', 'style', 'id', 'key', 'ref', 'name', 'to', 'href', 'src', 'type', 'rel', 'target',
  'for', 'autocomplete', 'role', 'is', 'icon', 'theme', 'variant', 'size', 'data-kit',
  'storage-key', 'lang', 'charset', 'content', 'method', 'action', 'accept',
]); // prettier-ignore
const MACHINE = /^[a-z0-9_.:/-]*$/;

function files(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name !== 'node_modules' && name !== 'dist') out.push(...files(full));
    } else if (/\.(vue|ts)$/.test(name)) out.push(full);
  }
  return out;
}

function allowedByComment(lines, line) {
  return [line - 1, line - 2].some((i) => i >= 0 && /naming-allowed:/.test(lines[i] ?? ''));
}

function scanText(hits, file, lines, text, line, where) {
  const m = PATTERN.exec(text);
  if (!m || allowedByComment(lines, line)) return;
  hits.push({ file, line, where, name: m[1], text: text.replace(/\s+/g, ' ').trim().slice(0, 100) });
}

/** String literals in TypeScript code that could reach the screen. */
function scanScript(hits, file, lines, code, lineOffset, { expression = false } = {}) {
  const source = ts.createSourceFile(
    'x.ts',
    expression ? `(${code})` : code,
    ts.ScriptTarget.Latest,
    true,
  );
  const visit = (node) => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) return;
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword
    )
      return;
    let text = null;
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) text = node.text;
    else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node))
      text = node.text;
    if (text !== null && !MACHINE.test(text)) {
      const line = lineOffset + source.getLineAndCharacterOfPosition(node.getStart()).line;
      scanText(hits, file, lines, text, line, expression ? 'template expression' : 'script');
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}

function scanTemplate(hits, file, lines, node) {
  const line = node.loc?.start.line ?? 1;
  if (node.type === 2) scanText(hits, file, lines, node.content, line, 'template text');
  if (node.type === 5 && node.content?.content)
    scanScript(hits, file, lines, node.content.content, line - 1, { expression: true });
  for (const prop of node.props ?? []) {
    if (prop.type === 6 && prop.value && !SILENT_ATTRS.has(prop.name) && !prop.name.startsWith('data-'))
      scanText(hits, file, lines, prop.value.content, prop.loc.start.line, `attribute ${prop.name}`);
    if (prop.type === 7 && prop.exp?.content && !['class', 'style', 'to', 'href'].includes(prop.arg?.content))
      scanScript(hits, file, lines, prop.exp.content, prop.loc.start.line - 1, { expression: true });
  }
  for (const child of node.children ?? []) scanTemplate(hits, file, lines, child);
}

export function scanFile(full) {
  const file = path.relative(ROOT, full);
  const code = readFileSync(full, 'utf8');
  const lines = code.split('\n');
  const hits = [];
  if (full.endsWith('.vue')) {
    const { descriptor } = parseSfc(code, { filename: file });
    if (descriptor.template?.ast) scanTemplate(hits, file, lines, descriptor.template.ast);
    for (const block of [descriptor.script, descriptor.scriptSetup]) {
      if (block) scanScript(hits, file, lines, block.content, block.loc.start.line - 1);
    }
  } else if (full.endsWith('.html')) {
    const text = code
      .replace(/<!--[\s\S]*?-->/g, '')
      .replace(/<script[\s\S]*?<\/script>/g, '')
      .replace(/<link[^>]*>/g, '');
    text.split('\n').forEach((l, i) => scanText(hits, file, lines, l.replace(/<[^>]+>/g, ' '), i + 1, 'page'));
  } else {
    scanScript(hits, file, lines, code, 0);
  }
  return hits;
}

export function scanAll() {
  const targets = [
    ...ROOTS.flatMap((r) => files(path.join(ROOT, r))),
    ...PAGES.map((p) => path.join(ROOT, p)),
  ].filter((f) => {
    const rel = path.relative(ROOT, f);
    return !(rel in EXEMPT) && !SKIP.some((s) => s.test(rel));
  });
  return targets.flatMap(scanFile);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const hits = scanAll();
  for (const h of hits) console.log(`${h.file}:${h.line}  ${h.name} in ${h.where}: ${h.text}`);
  if (hits.length) {
    console.error(
      `\n${hits.length} open-source component name(s) in user-visible text. Use the ` +
        'docs/DESIGN_STANDARD.md register (Vati Workspace · Investigation, Explore, Admin; ' +
        'Document reading; Name screening; Vati assistant; Vati records).',
    );
    process.exit(1);
  }
  console.log('Naming check passed: no component names in user-visible text.');
}
