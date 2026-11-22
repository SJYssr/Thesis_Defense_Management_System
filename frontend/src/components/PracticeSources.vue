<script setup lang="ts">
import type { PracticeSource } from '@/types/api'

defineProps<{ sources?: PracticeSource[]; label?: string }>()
</script>

<template>
  <details v-if="sources?.length" class="sources">
    <summary>{{ label || '查看材料依据' }}（{{ sources.length }}）</summary>
    <article v-for="(s, i) in sources" :key="s.chunk_id || i">
      <strong>{{ s.document_name || '材料片段' }}</strong>
      <small v-if="s.section">{{ s.section }}</small>
      <pre v-if="s.excerpt">{{ s.excerpt }}</pre>
    </article>
  </details>
</template>

<style scoped>
.sources { margin: 12px 0; font-size: 13px; color: #40546d; }
summary { color: #235eaa; cursor: pointer; width: fit-content; }
article { padding: 10px 12px; border-left: 2px solid #95b8e7; margin-top: 8px; background: #f5f8fd; overflow-wrap: anywhere; }
small { display: block; margin-top: 4px; }
pre { margin: 6px 0 0; white-space: pre-wrap; font: inherit; line-height: 1.6; }
</style>
