<script setup lang="ts">
/**
 * The auditor app inside the Vati Workspace shell (TAX-146). Tabs and sidebar come from
 * the server's workspace session (GET /api/v1/session), so they follow the officer's
 * access scope; pages still rely on the server enforcing every call on its own.
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Button, FrappeUIProvider } from 'frappe-ui';
import { HeaderMenu, InlineError, WorkspaceShell, type CommandItem } from '../../shared/components';
import StatePanel from './components/StatePanel.vue';
import { getWorkspaceSession, type WorkspaceSession } from './api/session';
import { getTaxpayer, type TaxpayerProfile } from './api/vati';
import { resetCaseList, useCaseList } from './composables/useCaseList';
import { viewCounts } from './domain/queue';
import {
  backToCases,
  commandItems,
  commandTarget,
  sidebarKey,
  workspaceApps,
  workspaceSections,
} from './domain/workspace';
import { FIXTURE_ROLES, fixtureRole, setFixtureRole, type FixtureRole } from './session';

const route = useRoute();
const router = useRouter();

// --- workspace session ------------------------------------------------------------------
const session = ref<WorkspaceSession | null>(null);
const sessionState = ref<'loading' | 'ready' | 'error'>('loading');
// TAX-147: the same case list the queue table shows, so view counts match its rows.
const caseList = useCaseList();
const cases = computed(() =>
  caseList.state.value.status === 'ready' ? caseList.state.value.data : [],
);
const taxpayers = ref<TaxpayerProfile[]>([]);
let taxpayersLoaded = false;

async function loadSession() {
  sessionState.value = 'loading';
  resetCaseList();
  taxpayers.value = [];
  taxpayersLoaded = false;
  try {
    session.value = await getWorkspaceSession();
    sessionState.value = 'ready';
  } catch {
    session.value = null;
    sessionState.value = 'error';
    return;
  }
  // The queue page reports a failed list itself; the counts are then just omitted.
  if (session.value.views.includes('case_queue')) await caseList.refresh();
}
onMounted(loadSession);
watch(fixtureRole, loadSession);

/** Taxpayer profiles for the palette (masked TINs), fetched the first time it opens. */
async function loadTaxpayers() {
  if (taxpayersLoaded || !cases.value.length) return;
  taxpayersLoaded = true;
  const ids = [...new Set(cases.value.map((c) => c.taxpayer_id))];
  const results = await Promise.allSettled(ids.map((id) => getTaxpayer(id)));
  taxpayers.value = results.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []));
}

const routeName = computed(() => (typeof route.name === 'string' ? route.name : null));
const nativeApp = computed(() => (typeof route.params.app === 'string' ? route.params.app : null));
const apps = computed(() =>
  session.value ? workspaceApps(session.value, routeName.value, nativeApp.value) : [],
);
const sections = computed(() =>
  session.value
    ? workspaceSections(
        session.value,
        caseList.state.value.status === 'ready' ? viewCounts(cases.value) : {},
      )
    : [],
);
const back = computed(() =>
  session.value ? backToCases(session.value, routeName.value) : undefined,
);
const commands = computed<CommandItem[]>(() =>
  session.value ? commandItems(session.value, cases.value, taxpayers.value) : [],
);
const roleLabel = computed(() => {
  const role = session.value?.roles[0];
  return role ? role.charAt(0).toUpperCase() + role.slice(1) : '';
});

function choose(item: CommandItem) {
  const target = commandTarget(item);
  if (target) void router.push(target);
}

const ROLE_LABELS: Record<FixtureRole, string> = {
  auditor: 'Auditor',
  specialist: 'Specialist',
  supervisor: 'Supervisor',
};

// --- page chunks that fail to load ------------------------------------------------------
// A page's code chunk can fail to load (network drop, redeploy). Show a recoverable error
// instead of leaving the content area silently blank.
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
    <WorkspaceShell
      label="Auditor workspace"
      :home="{ name: 'overview' }"
      :apps="apps"
      :sections="sections"
      :current="sidebarKey(routeName, route.query.view)"
      :back="back"
      :fixture="session?.synthetic ?? false"
      :commands="commands"
      :nav-loading="sessionState === 'loading'"
      storage-key="vati.sidebar"
      @select="choose"
      @palette-open="loadTaxpayers"
    >
      <template #fixture>
        <!-- Fixture-only: picks the synthetic officer the fixture server acts as (session.ts). -->
        <label class="flex items-center gap-1 text-xs text-vati-muted">
          <span class="vati-sr-only">Acting as (fixture)</span>
          <select
            class="h-7 rounded-control border border-vati-border bg-vati-surface py-0 pl-1.5 pr-6 text-xs text-vati-text"
            :value="fixtureRole"
            @change="setFixtureRole(($event.target as HTMLSelectElement).value as FixtureRole)"
          >
            <option v-for="role in FIXTURE_ROLES" :key="role" :value="role">
              {{ ROLE_LABELS[role] }}
            </option>
          </select>
        </label>
      </template>

      <template #actions>
        <!-- Below 640px these leave the header: notifications are not connected yet and the
             keyboard shortcuts do not apply on phones. -->
        <div class="hidden items-center gap-1 sm:flex">
          <HeaderMenu label="Notifications" icon="lucide-bell">
            <p class="font-semibold">Notifications</p>
            <p class="mt-1 text-vati-muted">
              Notifications are not connected yet. Case updates show on your queue and case pages.
            </p>
          </HeaderMenu>
          <HeaderMenu v-slot="{ close }" label="Help" icon="lucide-circle-help">
            <p class="font-semibold">Help</p>
            <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt><kbd class="rounded border border-vati-border px-1">Ctrl K</kbd></dt>
              <dd class="text-vati-muted">Search cases, TINs and pages</dd>
              <dt><kbd class="rounded border border-vati-border px-1">Esc</kbd></dt>
              <dd class="text-vati-muted">Close a menu, drawer or dialog</dd>
            </dl>
            <RouterLink
              :to="{ name: 'showcase' }"
              class="mt-3 inline-block text-sm font-medium text-vati-accent underline"
              @click="close()"
            >
              Design reference (component showcase)
            </RouterLink>
          </HeaderMenu>
        </div>
      </template>

      <template #account>
        <HeaderMenu
          v-slot="{ close }"
          :label="session?.display_name ?? 'Account'"
          icon="lucide-circle-user"
          show-label
        >
          <template v-if="session">
            <p class="font-semibold">{{ session.display_name }}</p>
            <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt class="text-vati-muted">Role</dt>
              <dd>{{ roleLabel }}</dd>
              <dt class="text-vati-muted">Tenant</dt>
              <dd class="break-words">{{ session.tenant_name }}</dd>
            </dl>
          </template>
          <p v-else class="text-vati-muted">Your account details could not be loaded.</p>
          <p class="mt-3 border-t border-vati-border pt-2 text-sm text-vati-muted">
            Sign out is available once Vati Workspace sign-in is connected. This build has no
            sign-in session.
          </p>
          <RouterLink
            :to="{ name: 'notices' }"
            class="mt-2 inline-block text-sm font-medium text-vati-accent underline"
            @click="close()"
          >
            About · Open-source notices
          </RouterLink>
        </HeaderMenu>
      </template>

      <template #notice>
        <div v-if="sessionState === 'error'" class="px-4 pt-4 sm:px-6">
          <InlineError
            title="Your workspace navigation could not be loaded"
            message="Pages still open from their links, and each checks your access."
            @retry="loadSession"
          />
        </div>
      </template>

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
    </WorkspaceShell>
  </FrappeUIProvider>
</template>

<style>
/* The shell fills the window; pages scroll inside its main region. */
html,
body,
#app {
  height: 100%;
}
</style>
