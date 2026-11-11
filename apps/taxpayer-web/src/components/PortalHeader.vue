<script setup lang="ts">
/**
 * Page title row shared by every portal page (desktop and mobile shells). Since TAX-145
 * it renders the shared Vati Workspace page header (apps/shared/components).
 */
import { PageHeader } from '../../../shared/components';

defineProps<{ title: string; description?: string }>();
</script>

<template>
  <PageHeader :title="title" :description="description">
    <template v-if="$slots.breadcrumbs" #breadcrumbs><slot name="breadcrumbs" /></template>
    <template v-if="$slots.actions" #actions><slot name="actions" /></template>
  </PageHeader>
</template>
