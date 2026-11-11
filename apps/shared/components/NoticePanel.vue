<script setup lang="ts">
/**
 * Notice and recommendations panel (TAX-145): one panel with a short bold headline and
 * one line of guidance, optionally dismissable, with a numbered "Recommendations (n)"
 * group that collapses. Warnings use amber; red is for errors only.
 */
import { computed, ref, useId } from 'vue';
import { TONE_CLASSES, TONE_ICONS, type Tone } from './tones';

const props = withDefaults(
  defineProps<{
    title: string;
    message?: string;
    tone?: Exclude<Tone, 'neutral'>;
    dismissable?: boolean;
    /** Short recommendations, shown in a collapsible numbered list. */
    recommendations?: string[];
    /** Start with the recommendations open. */
    open?: boolean;
  }>(),
  {
    message: undefined,
    tone: 'warning',
    dismissable: false,
    recommendations: () => [],
    open: false,
  },
);
const emit = defineEmits<{ dismiss: [] }>();

const dismissed = ref(false);
const expanded = ref(props.open);
const classes = computed(() => TONE_CLASSES[props.tone]);
const listId = `vati-notice-${useId()}`;

function dismiss() {
  dismissed.value = true;
  emit('dismiss');
}
</script>

<template>
  <section
    v-if="!dismissed"
    data-kit="notice"
    :data-tone="tone"
    :role="tone === 'danger' ? 'alert' : 'status'"
    :class="['rounded-surface border px-4 py-3', classes.surface]"
  >
    <div class="flex items-start gap-3">
      <span
        aria-hidden="true"
        :class="[TONE_ICONS[tone], 'mt-0.5 size-4 shrink-0', classes.text]"
      />
      <div class="min-w-0 flex-1">
        <p :class="['font-semibold', classes.text]">{{ title }}</p>
        <p v-if="message" :class="classes.text">{{ message }}</p>
        <template v-if="recommendations.length">
          <button
            type="button"
            :aria-expanded="expanded"
            :aria-controls="listId"
            :class="[
              'mt-2 inline-flex items-center gap-1 rounded-control font-medium underline',
              classes.text,
            ]"
            @click="expanded = !expanded"
          >
            <span
              aria-hidden="true"
              :class="[expanded ? 'lucide-chevron-down' : 'lucide-chevron-right', 'size-4']"
            />
            Recommendations ({{ recommendations.length }})
          </button>
          <ol v-show="expanded" :id="listId" :class="['mt-1 list-decimal pl-6', classes.text]">
            <li v-for="item in recommendations" :key="item">{{ item }}</li>
          </ol>
        </template>
      </div>
      <button
        v-if="dismissable"
        type="button"
        aria-label="Dismiss notice"
        :class="['-mr-1 grid size-7 shrink-0 place-items-center rounded-control', classes.text]"
        @click="dismiss"
      >
        <span aria-hidden="true" class="lucide-x size-4" />
      </button>
    </div>
  </section>
</template>
