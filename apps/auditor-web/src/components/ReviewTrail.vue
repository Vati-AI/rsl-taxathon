<script setup lang="ts">
import { computed } from 'vue';
import type { ReviewEvent } from '../api/review';
import { formatKey } from '../domain/cases';
import { formatTimestamp } from '../domain/dates';
import { EVENT_LABELS, ROLE_LABELS } from '../domain/review';

/** The append-only review trail (Vati Audit Event, ADR-013), newest first. */
const props = defineProps<{ events: ReviewEvent[]; viewerId: string }>();
const newestFirst = computed(() => [...props.events].reverse());
</script>

<template>
  <p v-if="!events.length" class="text-ink-gray-5">No review actions have been recorded yet.</p>
  <ol v-else class="space-y-3" aria-label="Review trail, newest first">
    <li
      v-for="event in newestFirst"
      :key="event.event_id"
      class="border-l-2 border-outline-gray-2 pl-3"
    >
      <p class="text-ink-gray-8">{{ EVENT_LABELS[event.action] ?? formatKey(event.action) }}</p>
      <p class="text-sm text-ink-gray-5">
        {{ event.actor_id === viewerId ? 'You' : (ROLE_LABELS[event.actor_role] ?? 'Officer') }}
        · {{ formatTimestamp(event.occurred_at) }}
        <template v-if="event.sequence !== null"> · case version {{ event.sequence }}</template>
      </p>
    </li>
  </ol>
</template>
