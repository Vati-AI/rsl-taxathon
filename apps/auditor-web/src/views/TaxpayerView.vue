<script setup lang="ts">
import { computed } from 'vue';
import { Button, Skeleton } from 'frappe-ui';
import { getTaxpayer } from '../api/vati';
import CaseStatusBadge from '../components/CaseStatusBadge.vue';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
import { errorPanel } from '../components/errorPanel';
import { useLoader } from '../composables/useLoader';
import { taxpayerTypeLabel } from '../domain/cases';
import { formatDate } from '../domain/dates';

const props = defineProps<{ taxpayerId: string }>();
const { state, reload } = useLoader(
  (signal) => getTaxpayer(props.taxpayerId, fetch, signal),
  () => props.taxpayerId,
);
const profile = computed(() => (state.value.status === 'ready' ? state.value.data : null));
</script>

<template>
  <PageLayout
    :title="profile ? profile.display_name : 'Taxpayer'"
    description="Read-only taxpayer context from the taxpayer portal (Frappe). Identifiers are masked."
    :breadcrumbs="[
      { label: 'My case queue', route: { name: 'queue' } },
      { label: profile ? profile.display_name : 'Taxpayer' },
    ]"
  >
    <div
      v-if="state.status === 'loading'"
      role="status"
      aria-label="Loading taxpayer"
      class="space-y-3"
    >
      <Skeleton class="h-6 w-48 rounded-control" />
      <Skeleton class="h-32 w-full rounded-surface" />
    </div>
    <StatePanel
      v-else-if="state.status === 'error'"
      v-bind="errorPanel(state.error, 'taxpayer')"
      @retry="reload"
    >
      <Button label="Back to my case queue" :route="{ name: 'queue' }" />
    </StatePanel>
    <div v-else-if="profile" class="space-y-4">
      <section
        aria-labelledby="profile-heading"
        class="rounded-surface border border-outline-gray-1 bg-surface-base"
      >
        <h2
          id="profile-heading"
          class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
        >
          Taxpayer details
        </h2>
        <dl class="grid gap-x-6 gap-y-3 px-4 py-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt class="text-sm text-ink-gray-5">Type</dt>
            <dd class="text-ink-gray-8">{{ taxpayerTypeLabel(profile.taxpayer_type) }}</dd>
          </div>
          <div>
            <dt class="text-sm text-ink-gray-5">Taxpayer ID number</dt>
            <dd class="font-mono text-ink-gray-8">
              <span v-if="profile.tin_masked">
                <span class="sr-only">Masked, ending in </span>{{ profile.tin_masked }}
              </span>
              <span v-else class="font-sans italic text-ink-gray-5">Not on record</span>
            </dd>
          </div>
          <div>
            <dt class="text-sm text-ink-gray-5">Registered</dt>
            <dd class="text-ink-gray-8">
              {{ formatDate(profile.registered_on) ?? 'Not on record' }}
            </dd>
          </div>
          <div>
            <dt class="text-sm text-ink-gray-5">Sector</dt>
            <dd class="text-ink-gray-8">{{ profile.sector ?? 'Not on record' }}</dd>
          </div>
        </dl>
      </section>
      <section
        aria-labelledby="open-cases-heading"
        class="rounded-surface border border-outline-gray-1 bg-surface-base"
      >
        <h2
          id="open-cases-heading"
          class="border-b border-outline-gray-1 px-4 py-3 text-lg font-semibold text-ink-gray-9"
        >
          Open cases you can view
        </h2>
        <p v-if="!profile.open_cases.length" class="px-4 py-4 text-ink-gray-5">No open cases.</p>
        <ul v-else class="divide-y divide-outline-gray-1">
          <li
            v-for="ref in profile.open_cases"
            :key="ref.case_id"
            class="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
          >
            <RouterLink
              :to="{ name: 'case', params: { caseId: ref.case_id } }"
              class="font-medium text-ink-blue-link underline-offset-2 hover:underline"
            >
              {{ ref.display_id }}
            </RouterLink>
            <CaseStatusBadge :status="ref.status" />
          </li>
        </ul>
      </section>
    </div>
  </PageLayout>
</template>
