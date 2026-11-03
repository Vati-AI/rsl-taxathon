<script setup lang="ts">
import { Button } from 'frappe-ui';
import { computed, ref } from 'vue';
import PortalHeader from '../components/PortalHeader.vue';
import { logout, sessionState } from '../lib/session';

const session = computed(() =>
  sessionState.value.status === 'ready' ? sessionState.value.session : null,
);
const loggingOut = ref(false);

async function onLogout() {
  loggingOut.value = true;
  await logout();
}
</script>

<template>
  <section class="max-w-2xl">
    <PortalHeader title="Account" />
    <dl
      v-if="session"
      class="mb-6 grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)] gap-x-4 gap-y-3 rounded-lg border border-outline-gray-2 p-5"
    >
      <dt class="text-ink-gray-6">Name</dt>
      <dd class="break-words text-ink-gray-9">{{ session.full_name }}</dd>
      <dt class="text-ink-gray-6">Login</dt>
      <dd class="break-words text-ink-gray-9">{{ session.user }}</dd>
    </dl>
    <Button
      size="lg"
      label="Log out"
      icon-left="lucide-log-out"
      :loading="loggingOut"
      @click="onLogout"
    />
  </section>
</template>
