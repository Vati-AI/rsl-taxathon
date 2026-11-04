<script setup lang="ts">
import { computed, nextTick, onMounted } from 'vue';
import { Badge } from 'frappe-ui';
import type { CaseRecords } from '../api/caseRecords';
import AmountText from './AmountText.vue';
import CitationLink from './CitationLink.vue';
import StatePanel from './StatePanel.vue';
import { actionLabel, categoryLabel, evidenceIndex, groupFindings } from '../domain/evidence';

const props = defineProps<{ caseId: string; records: CaseRecords; focusId?: string }>();
const groups = computed(() => groupFindings(props.records.findings));
const evidence = computed(() => evidenceIndex(props.records.evidence));

// Returning from a cited source puts focus back on the finding it was opened from.
onMounted(async () => {
  if (!props.focusId) return;
  await nextTick();
  const target = document.getElementById(`finding-${props.focusId}`);
  target?.focus();
  target?.scrollIntoView?.({ block: 'center' });
});
</script>

<template>
  <StatePanel
    v-if="!records.findings.length"
    kind="empty"
    title="No findings yet"
    message="Findings appear here after the case has been investigated. Nothing is shown rather than guessed."
  />
  <div v-else class="space-y-6">
    <p class="max-w-prose text-sm text-ink-gray-5">
      Findings organise what the records show. They are review material for an officer, not
      assessments, decisions or conclusions about the taxpayer.
    </p>
    <section
      v-for="group in groups"
      :key="group.kind"
      :aria-labelledby="`group-${group.kind}`"
      class="space-y-3"
    >
      <div>
        <h3 :id="`group-${group.kind}`" class="text-lg font-semibold text-ink-gray-9">
          {{ group.label }} ({{ group.findings.length }})
        </h3>
        <p class="text-sm text-ink-gray-5">{{ group.description }}</p>
      </div>
      <article
        v-for="finding in group.findings"
        :id="`finding-${finding.finding_id}`"
        :key="finding.finding_id"
        tabindex="-1"
        :aria-label="`${group.label}: ${categoryLabel(finding.category)}`"
        class="rounded-surface border border-outline-gray-1 bg-surface-base focus:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-4"
        :class="finding.finding_id === focusId ? 'ring-2 ring-outline-gray-3' : ''"
      >
        <header class="flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-4 py-3">
          <Badge :theme="group.theme" :label="group.label" class="whitespace-nowrap" />
          <span class="text-sm text-ink-gray-6">{{ categoryLabel(finding.category) }}</span>
        </header>
        <div class="space-y-3 px-4 py-4">
          <p class="max-w-prose break-words text-ink-gray-8">{{ finding.explanation }}</p>
          <dl class="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <dt class="text-sm text-ink-gray-5">Unresolved amount</dt>
              <dd class="text-ink-gray-8">
                <AmountText
                  :value="finding.unresolved_amount?.amount ?? null"
                  :currency="finding.unresolved_amount?.currency ?? ''"
                />
              </dd>
            </div>
            <div>
              <dt class="text-sm text-ink-gray-5">Producer confidence</dt>
              <dd class="text-ink-gray-8">
                {{ finding.confidence[0].toUpperCase() + finding.confidence.slice(1) }}
                <span class="block text-sm text-ink-gray-5">Not legal authority</span>
              </dd>
            </div>
            <div>
              <dt class="text-sm text-ink-gray-5">Legal route</dt>
              <dd class="text-ink-gray-8">
                {{
                  finding.legal_route.status === 'verified_by_reviewer'
                    ? (finding.legal_route.citation ?? 'Verified by reviewer')
                    : finding.legal_route.status === 'no_verified_route'
                      ? 'No verified route'
                      : 'Not verified'
                }}
              </dd>
            </div>
            <div>
              <dt class="text-sm text-ink-gray-5">Proposed next step</dt>
              <dd class="text-ink-gray-8">
                {{ actionLabel(finding.proposed_action.type) }}
                <span class="block text-sm text-ink-gray-5">Needs officer approval</span>
              </dd>
            </div>
          </dl>
          <div>
            <h4 class="text-sm font-medium text-ink-gray-7">Cited sources</h4>
            <p v-if="!finding.evidence_refs.length" class="text-sm italic text-ink-gray-5">
              No source cited
            </p>
            <ul v-else class="mt-1 space-y-1">
              <li
                v-for="(citation, index) in finding.evidence_refs"
                :key="`${citation.evidence_id}-${index}`"
              >
                <CitationLink
                  :case-id="caseId"
                  :citation="citation"
                  :evidence="evidence"
                  :finding-id="finding.finding_id"
                />
              </li>
            </ul>
          </div>
          <div v-if="finding.alternative_explanations.length">
            <h4 class="text-sm font-medium text-ink-gray-7">Other possible explanations</h4>
            <ul class="mt-1 list-disc space-y-1 pl-5 text-ink-gray-7">
              <li v-for="text in finding.alternative_explanations" :key="text">{{ text }}</li>
            </ul>
          </div>
        </div>
      </article>
    </section>
  </div>
</template>
