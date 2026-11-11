<script setup lang="ts">
/**
 * About · Open-source notices (TAX-150): the components Vati Workspace is built on, with
 * pinned version and licence, and where users meet each one under its Vati name. It is
 * one of the few places allowed to name components (DESIGN_STANDARD.md), so the region
 * carries data-component-names-allowed for the TAX-144 browser naming check.
 */
import {
  HOSTED_NOTICES,
  LIBRARY_NOTICES,
  SERVICE_NOTICES,
  type Notice,
} from '../notices/components';

const groups: { id: string; title: string; intro: string; rows: Notice[] }[] = [
  {
    id: 'notices-services',
    title: 'Services',
    intro: 'Open-source services that run inside Vati Workspace, unmodified (no fork).',
    rows: SERVICE_NOTICES,
  },
  {
    id: 'notices-libraries',
    title: 'Libraries',
    intro: 'Open-source libraries compiled into the Vati Workspace apps and assistant.',
    rows: LIBRARY_NOTICES,
  },
];
</script>

<template>
  <div data-component-names-allowed class="flex flex-col gap-8">
    <p class="max-w-3xl text-vati-muted">
      Vati Workspace uses the components below under their own licences. Each licence link opens the
      upstream text. Names inside the workspace follow the Vati register, shown here as “Shown in
      Vati as”.
    </p>
    <section v-for="group in groups" :key="group.id" :aria-labelledby="group.id">
      <h2 :id="group.id" class="text-lg font-semibold text-vati-text">{{ group.title }}</h2>
      <p class="mb-3 text-sm text-vati-muted">{{ group.intro }}</p>
      <div
        role="region"
        :aria-label="`${group.title}: open-source notices`"
        tabindex="0"
        class="relative overflow-x-auto rounded-card border border-vati-border"
      >
        <table class="w-full min-w-[40rem] text-left text-sm">
          <thead class="bg-vati-surface-subtle text-vati-muted">
            <tr>
              <th scope="col" class="px-3 py-2 font-medium">Component</th>
              <th scope="col" class="px-3 py-2 font-medium">Shown in Vati as</th>
              <th scope="col" class="px-3 py-2 font-medium">Version</th>
              <th scope="col" class="px-3 py-2 font-medium">Licence</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in group.rows" :key="row.component" class="border-t border-vati-border">
              <th scope="row" class="px-3 py-2 font-medium text-vati-text">
                <a :href="row.source" class="text-vati-accent underline" rel="noopener noreferrer">
                  {{ row.component }}
                </a>
              </th>
              <td class="px-3 py-2 text-vati-text">{{ row.shownAs }}</td>
              <td class="vati-tabular px-3 py-2 text-vati-text">{{ row.version }}</td>
              <td class="px-3 py-2">
                <a
                  :href="row.licenceUrl"
                  class="text-vati-accent underline"
                  rel="noopener noreferrer"
                  :aria-label="`${row.licence} licence for ${row.component}`"
                >
                  {{ row.licence }}
                </a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <section aria-labelledby="notices-hosted">
      <h2 id="notices-hosted" class="text-lg font-semibold text-vati-text">Hosted services</h2>
      <ul class="mt-2 flex flex-col gap-1 text-sm">
        <li v-for="h in HOSTED_NOTICES" :key="h.service">
          <span class="font-medium text-vati-text">{{ h.service }}</span>
          <span class="text-vati-muted"> — shown as {{ h.shownAs }}. {{ h.note }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>
