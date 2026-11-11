<script setup lang="ts">
import { OpenSourceNotices } from '../../../shared/components';
import PortalHeader from '../components/PortalHeader.vue';
</script>

<template>
  <section>
    <PortalHeader
      title="Open-source notices"
      description="About the Vati Taxpayer Portal: the open-source components it is built on, their versions and licences."
    />
    <OpenSourceNotices />
  </section>
</template>
