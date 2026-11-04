<script setup lang="ts">
import { FIXTURE_ROLES, fixtureRole, setFixtureRole, type FixtureRole } from '../session';

const LABELS: Record<FixtureRole, string> = {
  auditor: 'Auditor',
  specialist: 'Specialist',
  supervisor: 'Supervisor',
};
</script>

<template>
  <div
    class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-outline-gray-1 bg-vati-fixture-bg px-4 py-2 text-sm text-vati-fixture-text sm:px-6"
  >
    <p class="flex min-w-0 items-center gap-2">
      <span class="lucide-flask-conical size-4 shrink-0" aria-hidden="true" />
      <span>
        <strong>Fixture mode.</strong> Synthetic data only — no live taxpayer records, services or
        decisions.
      </span>
    </p>
    <!-- Fixture-only: picks the synthetic officer the fixture server acts as (session.ts). -->
    <label class="flex items-center gap-2">
      <span>Acting as (fixture)</span>
      <select
        class="rounded-control border border-outline-gray-2 bg-surface-base px-2 py-1 text-ink-gray-8"
        :value="fixtureRole"
        @change="setFixtureRole(($event.target as HTMLSelectElement).value as FixtureRole)"
      >
        <option v-for="role in FIXTURE_ROLES" :key="role" :value="role">{{ LABELS[role] }}</option>
      </select>
    </label>
  </div>
</template>
