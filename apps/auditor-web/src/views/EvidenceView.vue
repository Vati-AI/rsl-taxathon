<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { Alert, Badge, Button, Skeleton } from 'frappe-ui';
import { ProvenanceValue } from '../../../shared/components';
import { getCaseRecords } from '../api/caseRecords';
import { getCase } from '../api/vati';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
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

const props = defineProps<{ caseId: string; evidenceId: string }>();
const route = useRoute();

const { state, reload } = useLoader(
  async (signal) => {
    const [item, records] = await Promise.all([
      getCase(props.caseId, fetch, signal),
      getCaseRecords(props.caseId, fetch, signal),
    ]);
    return { item, records };
  },
  () => props.caseId,
);

const loaded = computed(() => (state.value.status === 'ready' ? state.value.data : null));
const evidence = computed(
  () => loaded.value?.records.evidence.find((e) => e.evidence_id === props.evidenceId) ?? null,
);
const title = computed(() =>
  evidence.value
    ? `${documentTypeLabel(evidence.value.document_type)} v${evidence.value.version}`
    : 'Evidence',
);
const extraction = computed(() => (evidence.value ? extractionLabel(evidence.value) : null));
const newer = computed(() =>
  loaded.value && evidence.value
    ? isSuperseded(loaded.value.records.evidence, evidence.value.evidence_id)
    : null,
);
const history = computed(() =>
  loaded.value ? versionHistory(loaded.value.records.evidence, props.evidenceId) : [],
);
const citations = computed(() =>
  loaded.value ? citedBy(loaded.value.records.findings, props.evidenceId) : [],
);

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

const caseLink = (query: Record<string, string> = {}) => ({
  name: 'case',
  params: { caseId: props.caseId },
  query,
});
const frappeFile = computed(
  () => evidence.value?.service_links.find((link) => link.service === 'frappe') ?? null,
);
</script>

<template>
  <PageLayout
    :title="title"
    :breadcrumbs="[
      // frappe-ui keys crumbs by label, so every label must stay unique, also while loading.
      { label: 'My case queue', route: { name: 'queue' } },
      { label: loaded ? loaded.item.display_id : 'Case', route: caseLink() },
      { label: 'Case evidence', route: caseLink({ tab: 'evidence' }) },
      { label: title },
    ]"
  >
    <div
      v-if="state.status === 'loading'"
      role="status"
      aria-label="Loading evidence"
      class="space-y-3"
    >
      <Skeleton class="h-6 w-48 rounded-control" />
      <Skeleton class="h-40 w-full rounded-surface" />
    </div>
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error)"
      @retry="reload"
    >
      <Button label="Back to my case queue" :route="{ name: 'queue' }" />
    </StatePanel>
    <StatePanel
      v-else-if="!evidence"
      kind="not_found"
      title="Evidence not found"
      message="This case has no evidence with that reference. It may have been cited without being supplied."
    >
      <Button label="Back to the case evidence" :route="caseLink({ tab: 'evidence' })" />
    </StatePanel>
    <div v-else class="space-y-4">
      <section
        v-if="requested"
        aria-labelledby="cited-heading"
        class="rounded-surface border border-outline-gray-3 bg-surface-gray-1 px-4 py-3"
      >
        <h2 id="cited-heading" class="font-medium text-ink-gray-9">
          Cited location: {{ locatorLabel(requested) }}
        </h2>
        <p class="text-sm text-ink-gray-6">
          The finding you came from cites this location in the document below. Open the original in
          the restricted evidence store to read it there.
        </p>
        <RouterLink
          v-if="fromFinding"
          :to="caseLink({ tab: 'findings', finding: fromFinding })"
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

      <div class="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section
          aria-labelledby="document-heading"
          class="rounded-surface border border-outline-gray-1 bg-surface-base"
        >
          <h2
            id="document-heading"
            class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
          >
            Document record
          </h2>
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
          <h2
            id="versions-heading"
            class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
          >
            Versions
          </h2>
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
        <h2 id="citations-heading" class="text-lg font-semibold text-ink-gray-9">
          Findings citing this document ({{ citations.length }})
        </h2>
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
              :to="caseLink({ tab: 'findings', finding: entry.finding.finding_id })"
              class="mt-1 inline-flex text-sm text-ink-blue-link underline-offset-2 hover:underline"
            >
              Open finding in the case
            </RouterLink>
          </li>
        </ul>
      </section>
    </div>
  </PageLayout>
</template>
