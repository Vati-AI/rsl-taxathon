<script setup lang="ts">
/**
 * Case workspace · Evidence (TAX-148): the documents on file beside a preview pane.
 * Choosing a document opens it in the pane at its own URL
 * (/cases/:caseId/evidence/:evidenceId), so a citation can link straight to it. Below
 * 1024px the list and the preview take turns, with a link back to the list.
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { Badge, Skeleton } from 'frappe-ui';
import type { CaseRecords } from '../../api/caseRecords';
import EvidencePreview from '../../components/EvidencePreview.vue';
import StatePanel from '../../components/StatePanel.vue';
import { errorPanel } from '../../components/errorPanel';
import type { LoadState } from '../../composables/useLoader';
import { formatTimestamp } from '../../domain/dates';
import {
  citedBy,
  documentTypeLabel,
  extractionLabel,
  isSuperseded,
  shortHash,
} from '../../domain/evidence';

defineOptions({ inheritAttrs: false });
const props = defineProps<{ caseId: string; recordState: LoadState<CaseRecords> }>();
const emit = defineEmits<{ 'reload-records': [] }>();
const route = useRoute();
const selected = computed(() =>
  typeof route.params.evidenceId === 'string' ? route.params.evidenceId : null,
);
const rows = computed(() => {
  if (props.recordState.status !== 'ready') return [];
  const records = props.recordState.data;
  return records.evidence.map((item) => ({
    item,
    extraction: extractionLabel(item),
    supersededBy: isSuperseded(records.evidence, item.evidence_id),
    citations: citedBy(records.findings, item.evidence_id).length,
  }));
});
</script>

<template>
  <div
    v-if="recordState.status === 'loading'"
    role="status"
    aria-label="Loading evidence"
    class="space-y-3"
  >
    <Skeleton class="h-6 w-48 rounded-control" />
    <Skeleton class="h-32 w-full rounded-surface" />
  </div>
  <StatePanel
    v-else-if="recordState.status === 'error'"
    v-bind="errorPanel(recordState.error)"
    @retry="emit('reload-records')"
  />
  <StatePanel
    v-else-if="!rows.length"
    kind="empty"
    title="No evidence on file"
    message="Documents appear here once the taxpayer or an officer has supplied them."
  />
  <div
    v-else
    class="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
  >
    <section aria-labelledby="evidence-list-heading" :class="selected ? 'hidden lg:block' : ''">
      <h2 id="evidence-list-heading" class="vati-sr-only">Evidence on file</h2>
      <p class="mb-2 text-sm text-ink-gray-5">
        Originals stay in restricted storage. Each document shows its version, extraction state and
        stored fingerprint (SHA-256), so a citation can be checked against the exact file.
      </p>
      <ul
        role="list"
        aria-label="Evidence on file"
        class="divide-y divide-outline-gray-1 overflow-hidden rounded-surface border border-outline-gray-1 bg-surface-base"
      >
        <li v-for="row in rows" :key="row.item.evidence_id">
          <RouterLink
            :to="{ name: 'evidence', params: { caseId, evidenceId: row.item.evidence_id } }"
            :aria-current="row.item.evidence_id === selected ? 'page' : undefined"
            :class="[
              'block px-3 py-2.5 hover:bg-surface-gray-1',
              row.item.evidence_id === selected && 'bg-vati-accent-subtle',
            ]"
          >
            <span class="flex flex-wrap items-center gap-2">
              <span class="font-medium text-ink-blue-link">
                {{ documentTypeLabel(row.item.document_type) }} v{{ row.item.version }}
              </span>
              <Badge
                :theme="row.extraction.theme"
                :label="row.extraction.label"
                class="whitespace-nowrap"
              />
            </span>
            <span class="mt-0.5 block text-sm text-ink-gray-6">
              {{ formatTimestamp(row.item.received_at) }} · {{ row.item.media_type }} ·
              <span class="font-mono" :title="row.item.document_sha256">{{
                shortHash(row.item.document_sha256)
              }}</span>
              · cited by {{ row.citations }}
              <template v-if="row.supersededBy">
                · replaced by v{{ row.supersededBy.version }}
              </template>
            </span>
          </RouterLink>
        </li>
      </ul>
    </section>
    <section
      aria-label="Evidence preview"
      :class="[
        'min-w-0 rounded-surface border border-outline-gray-1 bg-surface-base p-4',
        !selected && 'hidden lg:block',
      ]"
    >
      <RouterLink
        v-if="selected"
        :to="{ name: 'case-evidence', params: { caseId } }"
        class="mb-3 inline-flex items-center gap-1 text-sm text-ink-blue-link underline-offset-2 hover:underline lg:hidden"
      >
        <span class="lucide-arrow-left size-4" aria-hidden="true" />
        All evidence
      </RouterLink>
      <EvidencePreview
        v-if="selected && recordState.status === 'ready'"
        :case-id="caseId"
        :evidence-id="selected"
        :records="recordState.data"
      />
      <p v-else class="text-ink-gray-5">
        Choose a document to see its record, versions and the findings that cite it.
      </p>
    </section>
  </div>
</template>
