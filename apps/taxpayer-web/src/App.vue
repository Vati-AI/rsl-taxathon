<script setup lang="ts">
import {
  Alert,
  Button,
  DesktopShell,
  FrappeUIProvider,
  LoadingText,
  MobileNav,
  MobileNavItem,
  MobileShell,
  Sidebar,
  SidebarHeader,
  SidebarItem,
} from 'frappe-ui';
import { computed, ref } from 'vue';
import { RouterView } from 'vue-router';
import FixtureNotice from './components/FixtureNotice.vue';
import { logout, sessionState } from './lib/session';
import { useDesktop } from './lib/use-desktop';

const desktop = useDesktop();
const state = computed(() => sessionState.value);
const fullName = computed(() =>
  state.value.status === 'ready' ? state.value.session.full_name : '',
);
const loggingOut = ref(false);

async function onLogout() {
  loggingOut.value = true;
  await logout();
}

function retry() {
  window.location.reload();
}
</script>

<template>
  <FrappeUIProvider>
    <a href="#main" class="skip-link">Skip to main content</a>

    <div
      v-if="state.status === 'idle' || state.status === 'loading'"
      class="grid min-h-screen place-items-center"
      role="status"
    >
      <LoadingText text="Loading your portal…" />
    </div>

    <main
      v-else-if="state.status === 'error'"
      id="main"
      class="taxpayer-page mx-auto max-w-xl p-6 pt-16"
    >
      <h1 class="mb-4 text-2xl font-semibold text-ink-gray-9">Taxpayer portal</h1>
      <Alert theme="red" title="We could not open the portal" :description="state.error.message">
        <template #actions>
          <Button label="Try again" @click="retry" />
        </template>
      </Alert>
    </main>

    <DesktopShell v-else-if="desktop">
      <template #sidebar>
        <Sidebar :collapsible="false" aria-label="Taxpayer portal">
          <SidebarHeader title="Vati taxpayer portal" subtitle="Synthetic fixture" />
          <div class="flex-1 overflow-y-auto">
            <SidebarItem
              label="Submissions"
              icon="lucide-file-text"
              :route="{ name: 'submissions' }"
            />
            <SidebarItem
              label="New submission"
              icon="lucide-file-plus"
              :route="{ name: 'new-submission' }"
            />
            <SidebarItem label="Account" icon="lucide-user" :route="{ name: 'account' }" />
          </div>
          <div class="border-t border-outline-gray-1 p-3">
            <p class="mb-2 truncate text-sm text-ink-gray-6" :title="fullName">
              Signed in as {{ fullName }}
            </p>
            <Button
              label="Log out"
              icon-left="lucide-log-out"
              class="w-full"
              :loading="loggingOut"
              @click="onLogout"
            />
          </div>
        </Sidebar>
      </template>
      <div id="main" class="taxpayer-page mx-auto w-full max-w-4xl px-6 py-6">
        <FixtureNotice />
        <RouterView />
      </div>
    </DesktopShell>

    <MobileShell v-else>
      <div id="main" class="taxpayer-page px-4 py-4">
        <FixtureNotice />
        <RouterView />
      </div>
      <template #nav>
        <MobileNav aria-label="Taxpayer portal">
          <MobileNavItem
            label="Submissions"
            icon="lucide-file-text"
            :route="{ name: 'submissions' }"
          />
          <MobileNavItem label="New" icon="lucide-file-plus" :route="{ name: 'new-submission' }" />
          <MobileNavItem label="Account" icon="lucide-user" :route="{ name: 'account' }" />
        </MobileNav>
      </template>
    </MobileShell>
  </FrappeUIProvider>
</template>

<style>
.skip-link {
  position: absolute;
  left: 8px;
  top: -48px;
  z-index: 50;
  padding: 8px 12px;
  border-radius: 6px;
  background: #fff;
  color: #1d4ed8;
}
.skip-link:focus {
  top: 8px;
}
</style>
