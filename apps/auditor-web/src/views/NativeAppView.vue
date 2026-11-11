<script setup lang="ts">
/**
 * A Vati Workspace app whose native screens are not connected yet (TAX-146): the
 * investigation and explore screens, and Admin. Says so plainly instead of linking to a
 * generic homepage; TAX-106 replaces this with verified, case-aware destinations.
 */
import { computed } from 'vue';
import { Button } from 'frappe-ui';
import { EmptyState } from '../../../shared/components';
import PageLayout from '../components/PageLayout.vue';
import { APP_LABELS, NATIVE_APPS, type NativeApp } from '../domain/workspace';
import NotFoundView from './NotFoundView.vue';

const props = defineProps<{ app: string }>();
const known = computed(() => (NATIVE_APPS as readonly string[]).includes(props.app));
const label = computed(() => (known.value ? APP_LABELS[props.app as NativeApp] : ''));
const PURPOSE: Record<NativeApp, string> = {
  investigation: 'Work a case step by step in the investigation screens.',
  explore: 'Search evidence and explore relationships between people and companies.',
  admin: 'Manage users, roles and records.',
};
</script>

<template>
  <NotFoundView v-if="!known" />
  <PageLayout
    v-else
    :title="label"
    :description="PURPOSE[app as NativeApp]"
    :breadcrumbs="[{ label }]"
  >
    <EmptyState
      icon="lucide-plug-zap"
      :title="`${label} is not connected yet`"
      message="It will open from here once it is connected to Vati Workspace. Until then, work from your case pages: everything they show is up to date."
    >
      <template #actions>
        <Button
          variant="solid"
          theme="blue"
          label="Go to my case queue"
          :route="{ name: 'queue' }"
        />
      </template>
    </EmptyState>
  </PageLayout>
</template>
