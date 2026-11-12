<script setup lang="ts">
/**
 * The Vati Taxpayer Portal inside the Vati Workspace shell (TAX-146), the simpler
 * variant: no app tabs and no search, a sidebar of the taxpayer's own views (a drawer
 * below 1024px), fixture mode as a slim strip in the header, and the account menu.
 */
import { Alert, Button, FrappeUIProvider, LoadingText } from 'frappe-ui';
import { computed, ref } from 'vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';
import { HeaderMenu, WorkspaceShell, type NavSection } from '../../shared/components';
import { logout, sessionState, simulatorEnabled } from './lib/session';

const route = useRoute();
const state = computed(() => sessionState.value);
const fullName = computed(() =>
  state.value.status === 'ready' ? state.value.session.full_name : '',
);
const user = computed(() => (state.value.status === 'ready' ? state.value.session.user : ''));
// TAX-092: the server says where its data comes from; the strip is for fixture data only.
const fixtureMode = computed(
  () => state.value.status === 'ready' && state.value.session.mode === 'fixture',
);
// TAX-125: the Online Refunds simulator appears only where the site enables it.
const simulator = computed(() => simulatorEnabled(state.value));
const routeName = computed(() => (typeof route.name === 'string' ? route.name : null));

const sections = computed<NavSection[]>(() => {
  if (state.value.status !== 'ready') return [];
  const out: NavSection[] = [
    {
      items: [
        { label: 'Evidence requests', to: { name: 'evidence-requests' }, icon: 'lucide-inbox', match: ['evidence-requests', 'evidence-request'] },
        { label: 'My cases', to: { name: 'submissions' }, icon: 'lucide-file-text', match: ['submissions', 'submission'] },
        { label: 'Check before you file', to: { name: 'check-before-filing' }, icon: 'lucide-list-checks', match: 'check-before-filing' },
        { label: 'Account', to: { name: 'account' }, icon: 'lucide-user', match: 'account' },
      ],
    },
  ];
  if (simulator.value)
    out.push({
      label: 'Fixture tools',
      items: [{ label: 'Online Refunds simulator', to: { name: 'new-submission' }, icon: 'lucide-flask-conical', match: 'new-submission' }],
    });
  return out;
}); // prettier-ignore

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
    <WorkspaceShell
      product="Vati Taxpayer Portal"
      label="Taxpayer portal"
      :home="{ name: 'evidence-requests' }"
      :sections="sections"
      :current="routeName"
      :fixture="fixtureMode"
      :search="false"
      :nav-loading="state.status === 'idle' || state.status === 'loading'"
      storage-key="vati.taxpayer.sidebar"
    >
      <template v-if="state.status === 'ready'" #account>
        <HeaderMenu v-slot="{ close }" :label="fullName" icon="lucide-circle-user" show-label>
          <p class="font-semibold">{{ fullName }}</p>
          <p class="break-words text-sm text-vati-muted">{{ user }}</p>
          <Button
            class="mt-3 w-full"
            label="Log out"
            icon-left="lucide-log-out"
            :loading="loggingOut"
            @click="onLogout"
          />
          <RouterLink
            :to="{ name: 'notices' }"
            class="mt-3 inline-block text-sm font-medium text-vati-accent underline"
            @click="close()"
          >
            About · Open-source notices
          </RouterLink>
        </HeaderMenu>
      </template>

      <div class="taxpayer-page w-full max-w-6xl px-4 py-4 sm:px-6 sm:py-6">
        <div
          v-if="state.status === 'idle' || state.status === 'loading'"
          class="grid min-h-[50vh] place-items-center"
          role="status"
        >
          <LoadingText text="Loading your portal…" />
        </div>

        <div v-else-if="state.status === 'error'" class="max-w-xl pt-10">
          <h1 class="mb-4 text-2xl font-semibold text-ink-gray-9">Taxpayer portal</h1>
          <Alert
            theme="red"
            title="We could not open the portal"
            :description="state.error.message"
          >
            <template #actions>
              <Button label="Try again" @click="retry" />
            </template>
          </Alert>
        </div>

        <RouterView v-else />

        <footer
          class="mt-10 border-t border-vati-border pt-4 text-sm text-vati-muted"
          aria-label="Portal information"
        >
          <span>Vati Taxpayer Portal</span>
          <span aria-hidden="true"> · </span>
          <RouterLink :to="{ name: 'notices' }" class="text-vati-accent underline">
            Open-source notices
          </RouterLink>
        </footer>
      </div>
    </WorkspaceShell>
  </FrappeUIProvider>
</template>
