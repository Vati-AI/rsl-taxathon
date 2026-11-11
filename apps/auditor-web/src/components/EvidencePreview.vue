<script setup lang="ts">
/**
 * Evidence preview pane (TAX-148): one stored document's record, versions and the
 * findings that cite it, beside the evidence list in the case workspace. Document
 * content is never shown; citations point into the stored original by fingerprint.
 * (Formerly the TAX-041 evidence page.)
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { Alert, Badge } from 'frappe-ui';
import { ProvenanceValue } from '../../../shared/components';
import type { CaseRecords } from '../api/caseRecords';
import { serviceLabel } from '../domain/cases';
import { formatTimestamp } from '../domain/dates';
import {
  categoryLabel,
  citedBy,
  documentTypeLabel,
  extractionLabel,
  formatBytes,
  isSuperseded,
  kindInfo,
  locatorLabel,
  sourceLabel,
  versionHistory,
} from '../domain/evidence';
import StatePanel from './StatePanel.vue';

const props = defineProps<{ caseId: string; evidenceId: string; records: CaseRecords }>();
const route = useRoute();

const evidence = computed(
  () => props.records.evidence.find((e) => e.evidence_id === props.evidenceId) ?? null,
);
const title = computed(() =>
  evidence.value
    ? `${documentTypeLabel(evidence.value.document_type)} v${evidence.value.version}`
    : 'Evidence',
);
const extraction = computed(() => (evidence.value ? extractionLabel(evidence.value) : null));
const newer = computed(() =>
  evidence.value ? isSuperseded(props.records.evidence, evidence.value.evidence_id) : null,
);
const history = computed(() => versionHistory(props.records.evidence, props.evidenceId));
const citations = computed(() => citedBy(props.records.findings, props.evidenceId));

const single = (value: unknown) => (typeof value === 'string' && value ? value : null);
// The location an officer arrived to check, from the citation they followed.
const requested = computed(() => {
  const page = single(route.query.page);
  const record = single(route.query.record);
  const pageNumber = page && /^[1-9][0-9]*$/.test(page) ? Number(page) : null;
  if (pageNumber === null && !record) return null;
  return { page: pageNumber, record };
});
const fromFinding = computed(() => single(route.query.finding));
const isRequested = (locator: { page: number | null; record: string | null }) =>
  requested.value !== null &&
  locator.page === requested.value.page &&
  locator.record === requested.value.record;
const findingLink = (finding: string) => ({
  name: 'case-findings',
  params: { caseId: props.caseId },
  query: { finding },
});
const frappeFile = computed(
  () => evidence.value?.service_links.find((link) => link.service === 'frappe') ?? null,
);
</script>

<template>
  <StatePanel
    v-if="!evidence"
    kind="not_found"
    title="Evidence not found"
    message="This case has no evidence with that reference. It may have been cited without being supplied."
  />
  <article v-else aria-labelledby="evidence-title" data-kit="evidence-preview">
    <h2 id="evidence-title" class="mb-3 text-xl font-semibold text-ink-gray-9">{{ title }}</h2>
    <div class="space-y-4">
      <section
        v-if="requested"
        aria-labelledby="cited-heading"
        class="rounded-surface border border-outline-gray-3 bg-surface-gray-1 px-4 py-3"
      >
        <h3 id="cited-heading" class="font-medium text-ink-gray-9">
          Cited location: {{ locatorLabel(requested) }}
        </h3>
        <p class="text-sm text-ink-gray-6">
          The finding you came from cites this location in the document below. Open the original in
          the restricted evidence store to read it there.
        </p>
        <RouterLink
          v-if="fromFinding"
          :to="findingLink(fromFinding)"
          class="mt-1 inline-flex items-center gap-1 text-ink-blue-link underline-offset-2 hover:underline"
        >
          <span class="lucide-arrow-left size-4" aria-hidden="true" />
          Back to the finding
        </RouterLink>
      </section>

      <Alert
        v-if="newer"
        theme="amber"
        title="A newer version of this document exists"
        :description="`Version ${newer.version} replaced this one. Older versions are kept unchanged so earlier citations stay checkable.`"
      />
      <Alert
        v-if="evidence.extraction.status === 'failed'"
        theme="red"
        title="Text could not be extracted from this document"
        :description="`The original is stored, but nothing has been read from it (${evidence.extraction.error_code ?? 'no error code'}). This is a processing problem, not an empty document.`"
      />

      <div class="grid grid-cols-[minmax(0,1fr)] gap-4 2xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section
          aria-labelledby="document-heading"
          class="rounded-surface border border-outline-gray-1 bg-surface-base"
        >
          <h3
            id="document-heading"
            class="border-b border-outline-gray-1 px-4 py-3 text-base font-semibold text-ink-gray-9"
          >
            Document record
          </h3>
          <dl class="grid gap-x-6 gap-y-3 px-4 py-4 sm:grid-cols-2">
            <div>
              <dt class="text-sm text-ink-gray-5">Document type</dt>
              <dd class="text-ink-gray-8">{{ documentTypeLabel(evidence.document_type) }}</dd>
            </div>
            <div>
              <dt class="text-sm text-ink-gray-5">Version</dt>
              <dd class="text-ink-gray-8">{{ evidence.version }}</dd>
            </div>
            <div>
              <dt class="text-sm text-ink-gray-5">Received</dt>
              <dd class="text-ink-gray-8">{{ formatTimestamp(evidence.received_at) }}</dd>
            </div>
            <div>
              <dt class="text-sm text-ink-gray-5">Supplied through</dt>
              <dd class="text-ink-gray-8">{{ sourceLabel(evidence.source) }}</dd>
            </div>
            <div>
              <dt class="text-sm text-ink-gray-5">File</dt>
              <dd class="text-ink-gray-8">
                {{ evidence.media_type }}, {{ formatBytes(evidence.size_bytes) }}
              </dd>
            </div>
            <div>
              <dt class="text-sm text-ink-gray-5">Extraction</dt>
              <dd class="flex flex-wrap items-center gap-2 text-ink-gray-8">
                <Badge
                  v-if="extraction"
                  :theme="extraction.theme"
                  :label="extraction.label"
                  class="whitespace-nowrap"
                />
                <span v-if="evidence.extraction.extractor_version" class="text-sm text-ink-gray-5">
                  Document reading,
                  <ProvenanceValue :value="evidence.extraction.extractor_version" />
                </span>
              </dd>
            </div>
            <div class="sm:col-span-2">
              <dt class="text-sm text-ink-gray-5">SHA-256 of the stored original</dt>
              <dd class="break-all font-mono text-sm text-ink-gray-8">
                {{ evidence.document_sha256 }}
              </dd>
            </div>
            <div class="sm:col-span-2">
              <dt class="text-sm text-ink-gray-5">Stored file reference</dt>
              <dd class="break-all text-ink-gray-8">
                <ProvenanceValue v-if="frappeFile?.external_id" :value="frappeFile.external_id" />
                <template v-else>Not stored yet</template>
                <span v-if="frappeFile" class="block text-sm text-ink-gray-5">
                  {{ serviceLabel(frappeFile.service) }}, restricted file
                </span>
              </dd>
            </div>
          </dl>
          <p class="border-t border-outline-gray-1 px-4 py-3 text-sm text-ink-gray-5">
            Document content is never shown in this workspace. Citations point to a page or record
            in the stored original, identified by the fingerprint above.
          </p>
        </section>

        <section
          aria-labelledby="versions-heading"
          class="rounded-surface border border-outline-gray-1 bg-surface-base"
        >
          <h3
            id="versions-heading"
            class="border-b border-outline-gray-1 px-4 py-3 text-base font-semibold text-ink-gray-9"
          >
            Versions
          </h3>
          <ol class="space-y-2 px-4 py-4">
            <li v-for="version in history" :key="version.evidence_id">
              <span v-if="version.evidence_id === evidence.evidence_id" class="text-ink-gray-8">
                v{{ version.version }} · this version
              </span>
              <RouterLink
                v-else
                :to="{
                  name: 'evidence',
                  params: { caseId, evidenceId: version.evidence_id },
                }"
                class="text-ink-blue-link underline-offset-2 hover:underline"
              >
                v{{ version.version }}
              </RouterLink>
              <span class="block text-sm text-ink-gray-5">
                Received {{ formatTimestamp(version.received_at) }} ·
                {{ extractionLabel(version).label }}
              </span>
            </li>
          </ol>
        </section>
      </div>

      <section aria-labelledby="citations-heading" class="space-y-2">
        <h3 id="citations-heading" class="text-base font-semibold text-ink-gray-9">
          Findings citing this document ({{ citations.length }})
        </h3>
        <p v-if="!citations.length" class="text-ink-gray-5">No finding cites this document.</p>
        <ul v-else class="space-y-2">
          <li
            v-for="entry in citations"
            :key="entry.finding.finding_id"
            class="rounded-surface border border-outline-gray-1 bg-surface-base px-4 py-3"
            :class="entry.finding.finding_id === fromFinding ? 'ring-2 ring-outline-gray-3' : ''"
          >
            <div class="flex flex-wrap items-center gap-2">
              <Badge
                :theme="kindInfo(entry.finding.kind).theme"
                :label="kindInfo(entry.finding.kind).label"
                class="whitespace-nowrap"
              />
              <span class="text-sm text-ink-gray-6">{{
                categoryLabel(entry.finding.category)
              }}</span>
            </div>
            <p class="mt-2 max-w-prose break-words text-ink-gray-8">
              {{ entry.finding.explanation }}
            </p>
            <p class="mt-1 text-sm text-ink-gray-6">
              Cites
              <template v-for="(locator, index) in entry.locators" :key="index">
                <span v-if="index">, </span>
                <strong v-if="isRequested(locator)" class="font-medium text-ink-gray-9">
                  {{ locatorLabel(locator) }} (the location you followed)
                </strong>
                <span v-else>{{ locatorLabel(locator) }}</span>
              </template>
            </p>
            <RouterLink
              :to="findingLink(entry.finding.finding_id)"
              class="mt-1 inline-flex text-sm text-ink-blue-link underline-offset-2 hover:underline"
            >
              Open the finding
            </RouterLink>
          </li>
        </ul>
      </section>
    </div>
  </article>
</template>
