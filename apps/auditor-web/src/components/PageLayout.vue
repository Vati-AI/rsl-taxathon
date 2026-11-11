<script setup lang="ts">
import { Breadcrumbs, PageHeader } from 'frappe-ui';
import { PageHeader as VatiPageHeader } from '../../../shared/components';
import type { RouteLocationRaw } from 'vue-router';

defineProps<{
  title: string;
  description?: string;
  breadcrumbs: { label: string; route?: RouteLocationRaw }[];
  /** Officer lists use the available width so wide tables only scroll on small screens. */
  wide?: boolean;
}>();
</script>

<template>
  <PageHeader>
    <nav aria-label="Breadcrumb" class="min-w-0 flex-1">
      <Breadcrumbs :items="breadcrumbs" />
    </nav>
    <div class="flex shrink-0 items-center gap-2">
      <slot name="actions" />
    </div>
  </PageHeader>
  <div :class="['w-full px-4 py-5 sm:px-6', wide ? 'max-w-[1600px]' : 'max-w-6xl']">
    <!-- TAX-145: the shared Vati Workspace page header (apps/shared/components). -->
    <VatiPageHeader :title="title" :description="description">
      <template v-if="$slots.status" #status><slot name="status" /></template>
    </VatiPageHeader>
    <slot />
  </div>
</template>
