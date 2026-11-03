<script setup lang="ts">
import { Breadcrumbs, PageHeader } from 'frappe-ui';
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
    <header class="mb-5">
      <h1 class="break-words text-2xl font-semibold leading-tight text-ink-gray-9">{{ title }}</h1>
      <p v-if="description" class="mt-1 max-w-3xl text-base text-ink-gray-5">{{ description }}</p>
    </header>
    <slot />
  </div>
</template>
