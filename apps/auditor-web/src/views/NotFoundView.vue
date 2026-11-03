<script setup lang="ts">
import { Button } from 'frappe-ui';
import PageLayout from '../components/PageLayout.vue';
import StatePanel from '../components/StatePanel.vue';
</script>

<template>
  <PageLayout title="Page not found" :breadcrumbs="[{ label: 'Page not found' }]">
    <StatePanel
      kind="not_found"
      title="This page does not exist"
      message="The link may be out of date. Use the navigation to continue."
    >
      <Button variant="solid" theme="blue" label="Go to overview" :route="{ name: 'overview' }" />
    </StatePanel>
  </PageLayout>
</template>
