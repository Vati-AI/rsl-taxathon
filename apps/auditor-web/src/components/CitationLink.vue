<script setup lang="ts">
import { computed } from 'vue';
import type { Evidence, EvidenceRef } from '../api/caseRecords';
import { citationStatus, documentTypeLabel, locatorLabel } from '../domain/evidence';

// One cited source. Only a citation whose hash matches the stored original links to it;
// the others say plainly why they cannot be followed.
const props = defineProps<{
  caseId: string;
  citation: EvidenceRef;
  evidence: Map<string, Evidence>;
  findingId?: string;
}>();

const status = computed(() => citationStatus(props.citation, props.evidence));
const item = computed(() => props.evidence.get(props.citation.evidence_id));
const where = computed(() => locatorLabel(props.citation.locator));
const query = computed(() => {
  const out: Record<string, string> = {};
  if (props.citation.locator.page !== null) out.page = String(props.citation.locator.page);
  if (props.citation.locator.record !== null) out.record = props.citation.locator.record;
  if (props.findingId) out.finding = props.findingId;
  return out;
});
</script>

<template>
  <RouterLink
    v-if="status === 'ok' && item"
    :to="{
      name: 'evidence',
      params: { caseId, evidenceId: item.evidence_id },
      query,
    }"
    class="inline-flex items-center gap-1 text-ink-blue-link underline-offset-2 hover:underline"
  >
    <span class="lucide-file-text size-4 shrink-0" aria-hidden="true" />
    <span>{{ documentTypeLabel(item.document_type) }} v{{ item.version }} · {{ where }}</span>
  </RouterLink>
  <span v-else-if="status === 'not_on_file'" class="inline-flex items-center gap-1 text-ink-gray-6">
    <span class="lucide-file-x size-4 shrink-0" aria-hidden="true" />
    <span>Cited record not on file · {{ where }}</span>
  </span>
  <span v-else class="inline-flex items-center gap-1 text-ink-red-7">
    <span class="lucide-shield-alert size-4 shrink-0" aria-hidden="true" />
    <span>Citation does not match the stored document · {{ where }}</span>
  </span>
</template>
