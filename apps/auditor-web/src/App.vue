<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  Button,
  DesktopShell,
  FrappeUIProvider,
  Sidebar,
  SidebarHeader,
  SidebarItem,
  SidebarSection,
} from 'frappe-ui';
import FixtureBanner from './components/FixtureBanner.vue';
import StatePanel from './components/StatePanel.vue';

const route = useRoute();
const router = useRouter();
const section = computed(() => route.meta.section);

// A page's code chunk can fail to load (network drop, redeploy). Show a
// recoverable error instead of leaving the content area silently blank.
const pageLoadFailed = ref(false);
let failedHref = '';
router.onError((_error, to) => {
  pageLoadFailed.value = true;
  // The failed navigation is cancelled, so reload into the page that was requested.
  failedHref = router.resolve(to).href;
});
router.afterEach(() => {
  pageLoadFailed.value = false;
});
function reloadPage() {
  window.location.assign(failedHref || window.location.href);
}
</script>

<template>
  <FrappeUIProvider>
    <a class="vati-skip-link" href="#main-content">Skip to main content</a>
    <DesktopShell>
      <template #sidebar>
        <Sidebar aria-label="Auditor workspace">
          <SidebarHeader title="Vati AI" subtitle="Auditor workspace" />
          <SidebarSection>
            <SidebarItem
              label="Overview"
              icon="lucide-layout-dashboard"
              :route="{ name: 'overview' }"
              :active="section === 'overview'"
            />
            <SidebarItem
              label="Case queue"
              icon="lucide-list-checks"
              :route="{ name: 'queue' }"
              :active="section === 'cases'"
            />
          </SidebarSection>
          <SidebarSection label="Design">
            <SidebarItem
              label="Component showcase"
              icon="lucide-component"
              :route="{ name: 'showcase' }"
              :active="section === 'showcase'"
            />
          </SidebarSection>
        </Sidebar>
      </template>
      <main id="main-content" tabindex="-1" class="min-h-full bg-vati-canvas outline-none">
        <FixtureBanner />
        <div v-if="pageLoadFailed" class="w-full max-w-6xl px-4 py-5 sm:px-6">
          <StatePanel
            kind="error"
            title="This page could not be loaded"
            message="Part of the application failed to download. Check your connection and reload."
          >
            <Button variant="solid" theme="blue" label="Reload page" @click="reloadPage" />
          </StatePanel>
        </div>
        <RouterView v-else />
      </main>
    </DesktopShell>
  </FrappeUIProvider>
</template>
