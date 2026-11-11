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
import { RouterView, useRoute } from 'vue-router';
import FixtureNotice from './components/FixtureNotice.vue';
import { logout, sessionState, simulatorEnabled } from './lib/session';
import { useDesktop } from './lib/use-desktop';

const desktop = useDesktop();
const route = useRoute();
// Highlight the owning section, so a case's status page keeps "My cases" active.
const section = computed(() => {
  if (route.name === 'submissions' || route.name === 'submission') return 'submissions';
  if (route.name === 'new-submission') return 'new';
  if (route.name === 'evidence-requests' || route.name === 'evidence-request') return 'requests';
  if (route.name === 'account') return 'account';
  return null;
});
const state = computed(() => sessionState.value);
const fullName = computed(() =>
  state.value.status === 'ready' ? state.value.session.full_name : '',
);
// TAX-092: the server says where its data comes from; the notice is for fixture data only.
const fixtureMode = computed(
  () => state.value.status === 'ready' && state.value.session.mode === 'fixture',
);
// TAX-125: the Online Refunds simulator appears only where the site enables it.
const simulator = computed(() => simulatorEnabled(state.value));
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
    <a href="#main" class="vati-skip-link">Skip to main content</a>

    <main
      v-if="state.status === 'idle' || state.status === 'loading'"
      id="main"
      class="grid min-h-screen place-items-center"
      role="status"
    >
      <LoadingText text="Loading your portal…" />
    </main>

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
          <SidebarHeader
            title="Vati taxpayer portal"
            :subtitle="fixtureMode ? 'Synthetic fixture' : 'Live data'"
          />
          <div class="flex-1 overflow-y-auto">
            <SidebarItem
              label="Evidence requests"
              icon="lucide-inbox"
              :route="{ name: 'evidence-requests' }"
              :active="section === 'requests'"
            />
            <SidebarItem
              label="My cases"
              icon="lucide-file-text"
              :route="{ name: 'submissions' }"
              :active="section === 'submissions'"
            />
            <SidebarItem
              label="Account"
              icon="lucide-user"
              :route="{ name: 'account' }"
              :active="section === 'account'"
            />
            <template v-if="simulator">
              <p class="mt-4 px-2 pb-1 text-xs font-medium uppercase text-ink-gray-6">
                Fixture tools
              </p>
              <SidebarItem
                label="Online Refunds simulator"
                icon="lucide-flask-conical"
                :route="{ name: 'new-submission' }"
                :active="section === 'new'"
              />
            </template>
          </div>
          <div class="border-t border-outline-gray-1 p-3">
            <p class="mb-2 break-words text-sm text-ink-gray-6">Signed in as {{ fullName }}</p>
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
      <main id="main" class="taxpayer-page mx-auto w-full max-w-4xl px-6 py-6">
        <FixtureNotice v-if="fixtureMode" />
        <RouterView />
      </main>
    </DesktopShell>

    <MobileShell v-else>
      <main id="main" class="taxpayer-page px-4 py-4">
        <FixtureNotice v-if="fixtureMode" />
        <RouterView />
      </main>
      <template #nav>
        <MobileNav aria-label="Taxpayer portal">
          <MobileNavItem
            label="Requests"
            icon="lucide-inbox"
            :route="{ name: 'evidence-requests' }"
            :active="section === 'requests'"
          />
          <MobileNavItem
            label="My cases"
            icon="lucide-file-text"
            :route="{ name: 'submissions' }"
            :active="section === 'submissions'"
          />
          <MobileNavItem
            v-if="simulator"
            label="Simulator"
            icon="lucide-flask-conical"
            :route="{ name: 'new-submission' }"
            :active="section === 'new'"
          />
          <MobileNavItem
            label="Account"
            icon="lucide-user"
            :route="{ name: 'account' }"
            :active="section === 'account'"
          />
        </MobileNav>
      </template>
    </MobileShell>
  </FrappeUIProvider>
</template>
