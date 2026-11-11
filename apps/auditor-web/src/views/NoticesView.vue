<script setup lang="ts">
import { OpenSourceNotices } from '../../../shared/components';
import PageLayout from '../components/PageLayout.vue';
</script>

<template>
  <PageLayout
    title="Open-source notices"
    description="About Vati Workspace: the open-source components it is built on, their versions and licences."
    :breadcrumbs="[{ label: 'About' }, { label: 'Open-source notices' }]"
  >
    <OpenSourceNotices />
  </PageLayout>
</template>
