<script setup lang="ts">
import { computed } from 'vue';
import { Badge } from 'frappe-ui';
import { ProvenanceValue } from '../../../shared/components';
import type { CaseRecords } from '../api/caseRecords';
import AmountText from './AmountText.vue';
import StatePanel from './StatePanel.vue';
import { formatDate } from '../domain/dates';
import {
  REVIEW_LABELS,
  documentTypeLabel,
  evidenceIndex,
  relationshipView,
} from '../domain/evidence';

const props = defineProps<{ caseId: string; records: CaseRecords }>();
const view = computed(() => relationshipView(props.records));
const empty = computed(
  () => !view.value.parties.length && !view.value.ownership.length && !view.value.payments.length,
);
const findingLink = (findingId: string) => ({
  name: 'case',
  params: { caseId: props.caseId },
  query: { tab: 'findings', finding: findingId },
});
const evidenceLink = (evidenceId: string) => ({
  name: 'evidence',
  params: { caseId: props.caseId, evidenceId },
});
const evidence = computed(() => evidenceIndex(props.records.evidence));
const onFile = computed(() => new Set(evidence.value.keys()));
const sourceLabel = (id: string) => {
  const item = evidence.value.get(id);
  return item ? `${documentTypeLabel(item.document_type)} v${item.version}` : 'Source';
};
</script>

<template>
  <StatePanel
    v-if="empty"
    kind="empty"
    title="No parties or relationships recorded"
    message="Parties, ownership and payments appear here once records have been mapped for this case."
  />
  <div v-else class="space-y-6">
    <p class="max-w-prose text-sm text-ink-gray-5">
      Built from the case's mapped records (FollowTheMoney). A relationship shows what the records
      state; it is not a judgement about anyone involved.
    </p>

    <section aria-labelledby="parties-heading" class="space-y-2">
      <h3 id="parties-heading" class="text-lg font-semibold text-ink-gray-9">
        Parties ({{ view.parties.length }})
      </h3>
      <ul class="grid gap-3 md:grid-cols-2">
        <li
          v-for="party in view.parties"
          :key="party.entity.entity_id"
          class="rounded-surface border border-outline-gray-1 bg-surface-base px-4 py-3"
        >
          <p class="break-words font-medium text-ink-gray-8">{{ party.name }}</p>
          <p class="text-sm text-ink-gray-5">
            {{ party.schema === 'Person' ? 'Person' : 'Organisation' }}
            <template v-for="detail in party.details" :key="detail"> · {{ detail }}</template>
          </p>
          <div v-if="party.candidates.length" class="mt-2 space-y-1">
            <p class="text-sm font-medium text-ink-gray-7">Possible matches in other datasets</p>
            <ul class="space-y-1">
              <li
                v-for="candidate in party.candidates"
                :key="candidate.candidate_id"
                class="flex flex-wrap items-center gap-2 text-sm text-ink-gray-7"
              >
                <Badge
                  :theme="REVIEW_LABELS[candidate.review_status].theme"
                  :label="REVIEW_LABELS[candidate.review_status].label"
                  class="whitespace-nowrap"
                />
                <span class="break-words">
                  Name screening candidate
                  <ProvenanceValue :value="candidate.candidate_id" /> in
                  <ProvenanceValue
                    :value="`${candidate.dataset} (${candidate.dataset_version})`"
                  />, similarity {{ candidate.score }}
                </span>
              </li>
            </ul>
            <p class="text-sm text-ink-gray-5">
              A similarity score is not identity. Unreviewed matches are proposals only.
            </p>
          </div>
          <p v-if="party.missingEvidence.length" class="mt-2 text-sm text-ink-gray-6">
            <span
              class="lucide-file-x mr-1 inline-block size-4 align-text-bottom"
              aria-hidden="true"
            />
            {{ party.missingEvidence.length }} cited record(s) not on file
          </p>
        </li>
      </ul>
    </section>

    <section v-if="view.ownership.length" aria-labelledby="ownership-heading" class="space-y-2">
      <h3 id="ownership-heading" class="text-lg font-semibold text-ink-gray-9">
        Ownership ({{ view.ownership.length }})
      </h3>
      <div
        role="region"
        aria-label="Ownership records"
        tabindex="0"
        class="overflow-x-auto rounded-surface border border-outline-gray-1 bg-surface-base"
      >
        <table class="w-full min-w-[720px] border-collapse text-left">
          <thead class="border-b border-outline-gray-1 bg-surface-gray-1 text-sm text-ink-gray-6">
            <tr>
              <th scope="col" class="px-3 py-2 font-medium">Owner</th>
              <th scope="col" class="px-3 py-2 font-medium">Owns</th>
              <th scope="col" class="px-3 py-2 text-right font-medium">Stake</th>
              <th scope="col" class="px-3 py-2 font-medium">Since</th>
              <th scope="col" class="px-3 py-2 font-medium">Record status</th>
              <th scope="col" class="px-3 py-2 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in view.ownership"
              :key="row.entity.entity_id"
              class="border-b border-outline-gray-1 align-top last:border-b-0"
            >
              <td class="px-3 py-2 text-ink-gray-8">{{ row.owner }}</td>
              <td class="px-3 py-2 text-ink-gray-8">{{ row.asset }}</td>
              <td class="px-3 py-2 text-right tabular-nums text-ink-gray-8">
                {{ row.percentage !== null ? `${row.percentage}%` : 'Not stated' }}
              </td>
              <td class="px-3 py-2 text-ink-gray-6">
                {{ formatDate(row.since) ?? 'Not stated' }}
              </td>
              <td class="px-3 py-2">
                <template v-if="row.disputes.length">
                  <Badge
                    theme="amber"
                    label="Uncertain: records disagree"
                    class="whitespace-nowrap"
                  />
                  <ul class="mt-1 space-y-1 text-sm">
                    <li v-for="finding in row.disputes" :key="finding.finding_id">
                      <RouterLink
                        :to="findingLink(finding.finding_id)"
                        class="text-ink-blue-link underline-offset-2 hover:underline"
                      >
                        See the unresolved finding
                      </RouterLink>
                    </li>
                  </ul>
                </template>
                <span v-else class="text-sm text-ink-gray-6">No conflict recorded</span>
              </td>
              <td class="px-3 py-2 text-sm">
                <ul class="space-y-1">
                  <li v-for="id in row.entity.evidence_ids" :key="id">
                    <RouterLink
                      v-if="onFile.has(id)"
                      :to="evidenceLink(id)"
                      class="text-ink-blue-link underline-offset-2 hover:underline"
                    >
                      {{ sourceLabel(id) }}
                    </RouterLink>
                    <span v-else class="text-ink-gray-6">Record not on file</span>
                  </li>
                </ul>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="view.ownership.some((row) => row.disputes.length)" class="text-sm text-ink-gray-5">
        Uncertain stakes are shown as recorded. Neither figure has been chosen or corrected; an
        officer resolves the conflict from the sources.
      </p>
    </section>

    <section v-if="view.payments.length" aria-labelledby="payments-heading" class="space-y-2">
      <h3 id="payments-heading" class="text-lg font-semibold text-ink-gray-9">
        Payments ({{ view.payments.length }})
      </h3>
      <div
        role="region"
        aria-label="Payment records"
        tabindex="0"
        class="overflow-x-auto rounded-surface border border-outline-gray-1 bg-surface-base"
      >
        <table class="w-full min-w-[720px] border-collapse text-left">
          <thead class="border-b border-outline-gray-1 bg-surface-gray-1 text-sm text-ink-gray-6">
            <tr>
              <th scope="col" class="px-3 py-2 font-medium">From</th>
              <th scope="col" class="px-3 py-2 font-medium">To</th>
              <th scope="col" class="px-3 py-2 text-right font-medium">Amount</th>
              <th scope="col" class="px-3 py-2 font-medium">Date</th>
              <th scope="col" class="px-3 py-2 font-medium">Description</th>
              <th scope="col" class="px-3 py-2 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in view.payments"
              :key="row.entity.entity_id"
              class="border-b border-outline-gray-1 align-top last:border-b-0"
            >
              <td class="px-3 py-2 text-ink-gray-8">{{ row.payer }}</td>
              <td class="px-3 py-2 text-ink-gray-8">{{ row.beneficiary }}</td>
              <td class="px-3 py-2 text-right">
                <AmountText :value="row.amount" :currency="row.currency" />
              </td>
              <td class="px-3 py-2 text-ink-gray-6">{{ formatDate(row.date) ?? 'Not stated' }}</td>
              <td class="px-3 py-2 text-ink-gray-7">
                <span class="break-words">{{ row.purpose ?? 'Not stated' }}</span>
              </td>
              <td class="px-3 py-2 text-sm">
                <ul class="space-y-1">
                  <li v-for="id in row.entity.evidence_ids" :key="id">
                    <RouterLink
                      v-if="onFile.has(id)"
                      :to="evidenceLink(id)"
                      class="text-ink-blue-link underline-offset-2 hover:underline"
                    >
                      {{ sourceLabel(id) }}
                    </RouterLink>
                    <span v-else class="inline-flex items-center gap-1 text-ink-gray-6">
                      <span class="lucide-file-x size-4" aria-hidden="true" />
                      Record not on file
                    </span>
                  </li>
                </ul>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
