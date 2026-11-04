<script setup lang="ts">
import { computed } from 'vue';
import { Badge } from 'frappe-ui';
import type { CaseRecords } from '../api/caseRecords';
import StatePanel from './StatePanel.vue';
import { formatTimestamp } from '../domain/dates';
import {
  citedBy,
  documentTypeLabel,
  extractionLabel,
  isSuperseded,
  shortHash,
} from '../domain/evidence';

const props = defineProps<{ caseId: string; records: CaseRecords }>();

const rows = computed(() =>
  props.records.evidence.map((item) => ({
    item,
    extraction: extractionLabel(item),
    supersededBy: isSuperseded(props.records.evidence, item.evidence_id),
    citations: citedBy(props.records.findings, item.evidence_id).length,
  })),
);
</script>

<template>
  <StatePanel
    v-if="!records.evidence.length"
    kind="empty"
    title="No evidence on file"
    message="Documents appear here once the taxpayer or an officer has supplied them."
  />
  <div v-else class="space-y-3">
    <p class="max-w-prose text-sm text-ink-gray-5">
      Originals stay in restricted storage. This list shows each document's stored fingerprint
      (SHA-256), version and extraction state, so a citation can be checked against the exact file
      it refers to.
    </p>
    <div
      role="region"
      aria-label="Evidence on file"
      tabindex="0"
      class="overflow-x-auto rounded-surface border border-outline-gray-1 bg-surface-base"
    >
      <table class="w-full min-w-[720px] border-collapse text-left">
        <thead class="border-b border-outline-gray-1 bg-surface-gray-1 text-sm text-ink-gray-6">
          <tr>
            <th scope="col" class="px-3 py-2 font-medium">Document</th>
            <th scope="col" class="px-3 py-2 font-medium">Version</th>
            <th scope="col" class="px-3 py-2 font-medium">Received</th>
            <th scope="col" class="px-3 py-2 font-medium">Extraction</th>
            <th scope="col" class="px-3 py-2 font-medium">SHA-256</th>
            <th scope="col" class="px-3 py-2 text-right font-medium">Cited by</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.item.evidence_id"
            class="border-b border-outline-gray-1 last:border-b-0"
          >
            <td class="px-3 py-2">
              <RouterLink
                :to="{
                  name: 'evidence',
                  params: { caseId, evidenceId: row.item.evidence_id },
                }"
                class="text-ink-blue-link underline-offset-2 hover:underline"
              >
                {{ documentTypeLabel(row.item.document_type) }}
              </RouterLink>
              <span class="block text-sm text-ink-gray-5">{{ row.item.media_type }}</span>
            </td>
            <td class="px-3 py-2 text-ink-gray-7">
              v{{ row.item.version }}
              <span v-if="row.supersededBy" class="block text-sm text-ink-gray-5">
                Replaced by v{{ row.supersededBy.version }}
              </span>
            </td>
            <td class="px-3 py-2 text-ink-gray-6">{{ formatTimestamp(row.item.received_at) }}</td>
            <td class="px-3 py-2">
              <Badge
                :theme="row.extraction.theme"
                :label="row.extraction.label"
                class="whitespace-nowrap"
              />
            </td>
            <td class="px-3 py-2">
              <span class="font-mono text-sm text-ink-gray-7" :title="row.item.document_sha256">
                {{ shortHash(row.item.document_sha256) }}
              </span>
            </td>
            <td class="px-3 py-2 text-right tabular-nums text-ink-gray-7">
              {{ row.citations }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
