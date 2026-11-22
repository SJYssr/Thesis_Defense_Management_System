<script setup lang="ts">
import type { PracticeAssessment, PracticeSource } from '@/types/api'
const props = defineProps<{ assessment?: PracticeAssessment[]; sources?: PracticeSource[] }>()
const labels = { supported: '有依据', partial: '部分覆盖', missing: '尚未涉及', contradicted: '与材料矛盾', insufficient_evidence: '材料不足以判断' }
function locations(ids: number[]) {
  return (props.sources || []).filter(s => ids.includes(s.chunk_id)).map(s => [s.document_name, s.section].filter(Boolean).join(' · ')).join('、')
}
</script>
<template>
  <section v-if="assessment?.length" class="marking">
    <h3>本题材料要点核对</h3>
    <p>准确性满分 35 分。有依据计 1 份，部分覆盖计半份，缺少或矛盾计 0 份，再按可核对的要点数折算。材料依据不足的要点免扣分。</p>
    <ol>
      <li v-for="point in assessment" :key="point.id">
        <strong>{{ labels[point.verdict] }}</strong>
        <blockquote>{{ point.expectation }}</blockquote>
        <small>{{ locations(point.source_ids) }}</small>
        <p v-if="point.answer_quote">你的原话「{{ point.answer_quote }}」</p>
        <p>{{ point.reason }}</p>
      </li>
    </ol>
  </section>
</template>
<style scoped>
.marking { margin: 16px 0; overflow-wrap: anywhere; }
.marking ol { padding-left: 22px; }
.marking li + li { margin-top: 12px; }
.marking blockquote { border-left: 2px solid #93b2cf; margin: 5px 0; padding-left: 9px; white-space: pre-wrap; }
.marking small { color: #51667a; }
.marking p { margin: 5px 0; }
</style>
