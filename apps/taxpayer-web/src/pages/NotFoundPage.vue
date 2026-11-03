<script setup lang="ts">
import { Button } from 'frappe-ui';
import PortalHeader from '../components/PortalHeader.vue';
</script>

<template>
  <section class="max-w-2xl">
    <PortalHeader
      title="Page not found"
      description="This portal page does not exist or has moved."
    />
    <Button label="Go to your submissions" :route="{ name: 'submissions' }" />
  </section>
</template>
