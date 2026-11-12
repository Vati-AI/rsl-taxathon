<script setup lang="ts">
/** The reading pane before a request is chosen (TAX-152). */
defineOptions({ inheritAttrs: false });
</script>

<template>
  <div
    class="rounded-surface border border-dashed border-vati-border px-5 py-10 text-center text-vati-muted"
  >
    <span aria-hidden="true" class="lucide-mail-open mx-auto mb-2 block size-6" />
    Choose a request to read it and send your answer.
  </div>
</template>
