<script setup lang="ts">
/**
 * One evidence request (TAX-045), in the inbox's reading pane (TAX-152). The response is a
 * short sequence of steps: one per requested item, then "Review and send". Every step
 * stays mounted, so moving between steps or meeting an error keeps what was entered. The taxpayer answers every requested item,
 * either with documents (uploaded as private evidence, TAX-032) or with a
 * reason the document cannot be provided, then sends the response once. One
 * idempotency key covers the response, so sending again after a lost
 * connection cannot store it twice. Only taxpayer-facing fields are shown.
 */
import { Alert, Badge, Button, FormControl, LoadingText } from 'frappe-ui';
import { computed, nextTick, reactive, ref, shallowRef, watch } from 'vue';
import DocumentUploads from '../components/DocumentUploads.vue';
import { fetchEvidenceRequest, PortalError, respondToEvidenceRequest } from '../lib/api';
import { formatDate } from '../lib/format';
import {
  mapResponseErrors,
  MAX_MESSAGE,
  requestedTypeLabel,
  requestStatusDisplay,
  validateAnswers,
  type ItemErrors,
} from '../lib/responses';
import { sessionState } from '../lib/session';
import type { EvidenceRequestView } from '../lib/types';
import { useDocumentUploads, type DocumentUploads as Uploads } from '../lib/uploads';
import { newIdempotencyKey } from '../lib/validation';

defineOptions({ inheritAttrs: false });
const props = defineProps<{ id: string }>();
const emit = defineEmits<{ changed: [] }>();
/** Step index: one per requested item, then the review-and-send step. */
const step = ref(0);

const request = ref<EvidenceRequestView | null>(null);
const loading = ref(true);
const loadError = ref<PortalError | null>(null);
const sending = ref(false);
const replayed = ref(false);
const itemErrors = ref<ItemErrors>({});
const messageError = ref<string | undefined>();
const problem = ref<{ title: string; description: string; theme: 'red' | 'amber' } | null>(null);
const message = ref('');
const answers = reactive<Record<number, { cannotProvide: boolean; reason: string }>>({});
const uploads = shallowRef<Record<number, Uploads>>({});
let responseKey = newIdempotencyKey('response');

const tenantId = computed(() => {
  const state = sessionState.value;
  const scope = state.status === 'ready' ? (state.session.taxpayers ?? []) : [];
  return scope.length === 1 ? scope[0].tenant_id : null;
});

function prepare(view: EvidenceRequestView) {
  const next: Record<number, Uploads> = {};
  for (const item of view.items) {
    next[item.index] = useDocumentUploads(() => tenantId.value);
    answers[item.index] = { cannotProvide: false, reason: '' };
  }
  uploads.value = next;
}

async function load() {
  loading.value = true;
  loadError.value = null;
  problem.value = null;
  try {
    request.value = await fetchEvidenceRequest(props.id);
    prepare(request.value);
    step.value = 0;
    responseKey = newIdempotencyKey('response');
  } catch (err) {
    request.value = null;
    loadError.value =
      err instanceof PortalError ? err : new PortalError('unavailable', String(err));
  } finally {
    loading.value = false;
  }
}

watch(() => props.id, load, { immediate: true });

const status = computed(() => (request.value ? requestStatusDisplay(request.value.status) : null));
const reviewStep = computed(() => request.value?.items.length ?? 0);
const answered = (index: number) =>
  Boolean(uploads.value[index]?.evidenceIds.value.length) ||
  (answers[index]?.cannotProvide && answers[index].reason.trim().length > 0);

async function goTo(next: number) {
  step.value = next;
  await nextTick();
  document
    .getElementById(next === reviewStep.value ? 'review-title' : `item-${next}-title`)
    ?.focus();
}
const errorCount = computed(() => Object.keys(itemErrors.value).length);

async function focusFirstProblem() {
  await nextTick();
  const first = Object.keys(itemErrors.value)
    .map(Number)
    .sort((a, b) => a - b)[0];
  if (first !== undefined) {
    step.value = first;
    await nextTick();
    document.getElementById(`item-${first}-title`)?.focus();
  } else if (messageError.value) {
    step.value = reviewStep.value;
    await nextTick();
    document.getElementById('response-message')?.focus();
  }
}

async function send() {
  if (!request.value) return;
  problem.value = null;
  messageError.value =
    message.value.trim().length > MAX_MESSAGE
      ? `Keep the message under ${MAX_MESSAGE} characters.`
      : undefined;
  const state = Object.fromEntries(
    request.value.items.map((item) => {
      const panel = uploads.value[item.index];
      return [
        item.index,
        {
          uploaded: panel.evidenceIds.value.length,
          pending: panel.pending.value,
          failed: panel.failed.value,
          cannotProvide: answers[item.index].cannotProvide,
          reason: answers[item.index].reason,
        },
      ];
    }),
  );
  itemErrors.value = validateAnswers(state);
  if (errorCount.value > 0 || messageError.value) return focusFirstProblem();

  sending.value = true;
  try {
    const result = await respondToEvidenceRequest({
      request_id: request.value.request_id,
      idempotency_key: responseKey,
      message: message.value.trim() || null,
      items: request.value.items.map((item) => ({
        item_index: item.index,
        evidence_ids: uploads.value[item.index].evidenceIds.value,
        unavailable_reason: answers[item.index].cannotProvide
          ? answers[item.index].reason.trim()
          : null,
      })),
    });
    request.value = result.data;
    replayed.value = Boolean(result.replayed);
    emit('changed');
    await nextTick();
    document.getElementById('sent-title')?.focus();
  } catch (error) {
    const err =
      error instanceof PortalError ? error : new PortalError('unavailable', String(error));
    if (err.kind === 'invalid' && err.code === 'idempotency_conflict') {
      problem.value = {
        theme: 'amber',
        title: 'This response was already sent with different answers',
        description: 'Nothing new was sent. Reload the page to see what was received.',
      };
    } else if (err.kind === 'invalid' && err.fieldErrors.length > 0) {
      const mapped = mapResponseErrors(err.fieldErrors);
      itemErrors.value = mapped.items;
      messageError.value = mapped.message;
      problem.value = {
        theme: 'red',
        title: 'The response was not accepted',
        description: mapped.general[0] ?? 'Check the highlighted items and send again.',
      };
      await focusFirstProblem();
    } else if (err.kind === 'unavailable') {
      problem.value = {
        theme: 'red',
        title: 'We could not confirm that your response was received',
        description:
          'Send it again. The portal recognises a repeated send, so it will not store a second response.',
      };
    } else {
      problem.value = {
        theme: 'red',
        title: 'The response was not sent',
        description: err.message,
      };
    }
  } finally {
    sending.value = false;
  }
}
</script>

<template>
  <section aria-labelledby="request-title" data-kit="request-detail">
    <div v-if="loading" role="status" class="py-8">
      <LoadingText text="Loading the evidence request…" />
    </div>

    <template v-else-if="loadError">
      <h2 id="request-title" class="mb-3 text-lg font-semibold text-vati-text">
        {{ loadError.kind === 'not_found' ? 'Request not found' : 'Evidence request' }}
      </h2>
      <Alert
        :theme="loadError.kind === 'denied' ? 'amber' : 'red'"
        :title="
          loadError.kind === 'not_found'
            ? 'We could not find that evidence request'
            : loadError.kind === 'denied'
              ? 'Access denied'
              : 'The request could not be loaded'
        "
        :description="loadError.message"
      />
      <Button
        class="mt-4"
        label="Back to evidence requests"
        :route="{ name: 'evidence-requests' }"
      />
    </template>

    <template v-else-if="request && status">
      <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="request-title" class="text-lg font-semibold text-vati-text">
            {{ request.items.length }} {{ request.items.length === 1 ? 'document' : 'documents' }}
            requested
          </h2>
          <p v-if="request.due_date" class="text-vati-muted">
            Reply by {{ formatDate(request.due_date) }}
          </p>
        </div>
        <Badge size="lg" :theme="status.theme" :label="status.label" />
      </div>

      <div class="mb-5 rounded-surface border border-vati-border bg-vati-surface p-4">
        <h3 class="mb-1 font-semibold text-vati-text">Message from the officer</h3>
        <p class="whitespace-pre-line break-words text-vati-text">
          {{ request.message_to_taxpayer }}
        </p>
      </div>

      <!-- Withdrawn: nothing to do -->
      <Alert
        v-if="request.status === 'no_longer_needed'"
        theme="gray"
        title="This request is no longer needed"
        description="You do not need to send anything for it."
      />

      <!-- Answered: read-only summary -->
      <section
        v-else-if="request.status === 'response_sent' && request.response"
        aria-labelledby="sent-title"
        class="space-y-3"
      >
        <h3 id="sent-title" tabindex="-1" class="text-lg font-semibold text-vati-text">
          Your response
        </h3>
        <Alert
          theme="green"
          title="Response sent"
          :description="
            replayed
              ? 'This response had already been received. Nothing was sent twice.'
              : `Sent ${formatDate(request.response.responded_at)}. The officer will review it.`
          "
        />
        <ol class="space-y-2">
          <li
            v-for="answer in request.response.items"
            :key="answer.index"
            class="rounded-surface border border-vati-border p-3"
          >
            <p class="font-medium text-vati-text">
              Item {{ answer.index + 1 }}:
              {{ requestedTypeLabel(request.items[answer.index]?.document_type ?? '') }}
            </p>
            <ul v-if="answer.documents.length" class="mt-1 space-y-0.5">
              <li v-for="doc in answer.documents" :key="doc.evidence_id" class="break-all">
                {{ doc.name }}
              </li>
            </ul>
            <p v-if="answer.unavailable_reason" class="mt-1 break-words text-vati-muted">
              Not provided: {{ answer.unavailable_reason }}
            </p>
          </li>
        </ol>
        <p v-if="request.response.message" class="break-words text-vati-muted">
          Your message: {{ request.response.message }}
        </p>
      </section>

      <!-- Open: one step per item, then review and send -->
      <form v-else novalidate class="space-y-4" @submit.prevent="send">
        <ol aria-label="Response steps" class="flex flex-wrap gap-2" data-kit="response-steps">
          <li v-for="item in request.items" :key="item.index">
            <button
              type="button"
              :aria-current="step === item.index ? 'step' : undefined"
              :class="[
                'inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-sm',
                step === item.index
                  ? 'border-vati-accent bg-vati-accent-subtle font-medium text-vati-accent'
                  : 'border-vati-border text-vati-text hover:bg-vati-surface-subtle',
              ]"
              @click="goTo(item.index)"
            >
              <span
                v-if="answered(item.index)"
                aria-hidden="true"
                class="lucide-check size-3.5 text-vati-success-text"
              />
              <span
                v-else-if="itemErrors[item.index]"
                aria-hidden="true"
                class="lucide-circle-alert size-3.5 text-vati-danger-text"
              />
              Item {{ item.index + 1 }}
              <span class="vati-sr-only">
                {{
                  answered(item.index)
                    ? '(answered)'
                    : itemErrors[item.index]
                      ? '(needs an answer)'
                      : ''
                }}
              </span>
            </button>
          </li>
          <li>
            <button
              type="button"
              :aria-current="step === reviewStep ? 'step' : undefined"
              :class="[
                'inline-flex h-8 items-center rounded-full border px-3 text-sm',
                step === reviewStep
                  ? 'border-vati-accent bg-vati-accent-subtle font-medium text-vati-accent'
                  : 'border-vati-border text-vati-text hover:bg-vati-surface-subtle',
              ]"
              @click="goTo(reviewStep)"
            >
              Review and send
            </button>
          </li>
        </ol>

        <Alert
          v-if="errorCount > 0"
          theme="red"
          role="alert"
          :title="
            errorCount === 1 ? '1 item needs an answer' : `${errorCount} items need an answer`
          "
          description="Your other answers have been kept."
        />

        <section
          v-for="item in request.items"
          v-show="step === item.index"
          :key="item.index"
          :aria-labelledby="`item-${item.index}-title`"
          class="space-y-4 rounded-surface border border-vati-border bg-vati-surface p-4"
        >
          <div>
            <p class="text-sm text-vati-muted">Step {{ item.index + 1 }} of {{ reviewStep + 1 }}</p>
            <h3
              :id="`item-${item.index}-title`"
              tabindex="-1"
              :aria-describedby="itemErrors[item.index] ? `item-${item.index}-error` : undefined"
              class="text-base font-semibold text-vati-text"
            >
              Item {{ item.index + 1 }}: {{ requestedTypeLabel(item.document_type) }}
            </h3>
            <p class="mt-1 whitespace-pre-line break-words text-vati-text">
              {{ item.description }}
            </p>
            <p class="mt-1 text-sm text-vati-muted">
              Upload the document, or tell us why you cannot provide it.
            </p>
          </div>
          <p
            v-if="itemErrors[item.index]"
            :id="`item-${item.index}-error`"
            class="text-sm text-ink-red-7"
          >
            {{ itemErrors[item.index] }}
          </p>
          <DocumentUploads
            v-if="uploads[item.index] && !answers[item.index].cannotProvide"
            :uploads="uploads[item.index]"
            :input-id="`item-${item.index}-file`"
            :file-label="`Choose a file for item ${item.index + 1}`"
            :list-label="`Documents for item ${item.index + 1}`"
            :default-type="item.document_type"
            context="response"
          />
          <FormControl
            v-model="answers[item.index].cannotProvide"
            type="checkbox"
            :label="`I cannot provide this document (item ${item.index + 1})`"
          />
          <FormControl
            v-if="answers[item.index].cannotProvide"
            v-model="answers[item.index].reason"
            type="textarea"
            size="lg"
            variant="outline"
            :label="`Why can't you provide it? (item ${item.index + 1})`"
            description="For example: the supplier has closed, or the document was never issued."
            :rows="3"
            required
          />
          <div class="flex flex-wrap gap-2 pt-1">
            <Button
              v-if="item.index > 0"
              label="Previous"
              icon-left="lucide-arrow-left"
              @click="goTo(item.index - 1)"
            />
            <Button
              variant="solid"
              :label="
                item.index + 1 < reviewStep
                  ? `Next: item ${item.index + 2}`
                  : 'Next: review and send'
              "
              icon-right="lucide-arrow-right"
              @click="goTo(item.index + 1)"
            />
          </div>
        </section>

        <section
          v-show="step === reviewStep"
          aria-labelledby="review-title"
          class="space-y-4 rounded-surface border border-vati-border bg-vati-surface p-4"
        >
          <div>
            <p class="text-sm text-vati-muted">Step {{ reviewStep + 1 }} of {{ reviewStep + 1 }}</p>
            <h3 id="review-title" tabindex="-1" class="text-base font-semibold text-vati-text">
              Review and send
            </h3>
          </div>
          <ul class="space-y-1 text-sm">
            <li v-for="item in request.items" :key="item.index" class="flex items-center gap-2">
              <span
                aria-hidden="true"
                :class="[
                  answered(item.index)
                    ? 'lucide-check text-vati-success-text'
                    : 'lucide-circle-dashed text-vati-muted',
                  'size-4',
                ]"
              />
              Item {{ item.index + 1 }}: {{ requestedTypeLabel(item.document_type) }} —
              {{
                uploads[item.index]?.evidenceIds.value.length
                  ? `${uploads[item.index].evidenceIds.value.length} document(s) uploaded`
                  : answers[item.index]?.cannotProvide
                    ? 'cannot provide, reason given'
                    : 'not answered yet'
              }}
            </li>
          </ul>
          <Alert
            v-if="problem"
            :theme="problem.theme"
            role="alert"
            :title="problem.title"
            :description="problem.description"
          />
          <FormControl
            id="response-message"
            v-model="message"
            type="textarea"
            size="lg"
            variant="outline"
            label="Message to the officer (optional)"
            description="Anything that helps explain your documents. Do not include passwords or bank PINs."
            :rows="3"
            :error="messageError"
          />
          <div class="flex flex-wrap items-center gap-2">
            <Button
              v-if="reviewStep > 0"
              label="Previous"
              icon-left="lucide-arrow-left"
              @click="goTo(reviewStep - 1)"
            />
            <Button
              type="submit"
              variant="solid"
              size="lg"
              label="Send response"
              :loading="sending"
              :disabled="sending || !tenantId"
            />
          </div>
        </section>
      </form>
    </template>
  </section>
</template>
