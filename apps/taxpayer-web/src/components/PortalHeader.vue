<script setup lang="ts">
/** Page title row shared by every portal page (desktop and mobile shells). */
defineProps<{ title: string; description?: string }>();
</script>

<template>
  <header class="mb-6 flex flex-wrap items-start justify-between gap-4">
    <div class="min-w-0">
      <slot name="breadcrumbs" />
      <h1 id="page-title" class="text-2xl font-semibold text-ink-gray-9">{{ title }}</h1>
      <p v-if="description" class="mt-1 text-base text-ink-gray-6">{{ description }}</p>
    </div>
    <div class="flex shrink-0 gap-2">
      <slot name="actions" />
    </div>
  </header>
</template>
