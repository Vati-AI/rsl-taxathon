<script setup lang="ts">
/**
 * A verbatim identifier or version string from a connected service (a record ID, a
 * screening candidate ID, an extractor version), shown for traceability (TAX-150).
 * Labels around it use the Vati register; the value itself is provenance and is never
 * rewritten, so it may contain a component name. It is marked for the TAX-144 browser
 * naming check, the way Admin diagnostics and the open-source notices are.
 */
defineProps<{ value: string }>();
</script>

<template>
  <span data-component-names-allowed data-kit="provenance-value" class="break-all font-mono">{{
    value
  }}</span>
</template>
