<script setup lang="ts">
import { computed } from 'vue';
import { Badge } from 'frappe-ui';
import type { Finding } from '../api/review';
import { formatKey } from '../domain/cases';
import { formatTimestamp } from '../domain/dates';
import { FINDING_KIND_LABELS, PRODUCER_LABELS } from '../domain/review';
import AmountText from './AmountText.vue';

const props = defineProps<{ finding: Finding; headingLevel?: 'h3' | 'h4' }>();

// Missing or unverified evidence is reported apart from a discrepancy (AGENTS.md).
const KIND_THEMES: Record<Finding['kind'], 'gray' | 'blue' | 'amber' | 'red'> = {
  observed_fact: 'gray',
  inference: 'blue',
  missing_evidence: 'amber',
  unverified_evidence: 'amber',
  contradictory_evidence: 'red',
};
const ROUTES: Record<Finding['legal_route']['status'], string> = {
  unverified: 'Legal route not yet verified',
  verified_by_reviewer: 'Legal route verified by a reviewer',
  no_verified_route: 'No verified legal route',
};
const sources = computed(() =>
  props.finding.evidence_refs.map((ref) =>
    ref.locator.page !== null ? `page ${ref.locator.page}` : `record ${ref.locator.record ?? '?'}`,
  ),
);
</script>

<template>
  <article class="space-y-2">
    <div class="flex flex-wrap items-center gap-2">
      <Badge
        :theme="KIND_THEMES[finding.kind]"
        :label="FINDING_KIND_LABELS[finding.kind] ?? formatKey(finding.kind)"
      />
      <span class="text-sm text-ink-gray-5">
        {{ formatKey(finding.category) }} · {{ PRODUCER_LABELS[finding.producer] }} ·
        {{ finding.confidence }} confidence (not legal authority)
      </span>
    </div>
    <component :is="headingLevel ?? 'h3'" class="break-words text-ink-gray-8">
      {{ finding.explanation }}
    </component>
    <dl class="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_minmax(0,1fr)]">
      <dt class="text-ink-gray-5">Unresolved amount</dt>
      <dd class="text-ink-gray-7">
        <AmountText
          :value="finding.unresolved_amount?.amount ?? null"
          :currency="finding.unresolved_amount?.currency ?? ''"
        />
      </dd>
      <dt class="text-ink-gray-5">Evidence cited</dt>
      <dd class="text-ink-gray-7">
        {{ sources.length ? sources.join(', ') : 'None yet' }}
      </dd>
      <dt class="text-ink-gray-5">Legal route</dt>
      <dd class="text-ink-gray-7">{{ ROUTES[finding.legal_route.status] }}</dd>
      <dt class="text-ink-gray-5">Recorded</dt>
      <dd class="text-ink-gray-7">{{ formatTimestamp(finding.created_at) }}</dd>
    </dl>
    <div v-if="finding.alternative_explanations.length" class="text-sm">
      <p class="text-ink-gray-5">Other possible explanations</p>
      <ul class="list-disc space-y-1 pl-5 text-ink-gray-7">
        <li v-for="text in finding.alternative_explanations" :key="text" class="break-words">
          {{ text }}
        </li>
      </ul>
    </div>
  </article>
</template>
