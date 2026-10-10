<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { aiApi } from '@/api/common'
import MarkdownViewer from '@/components/MarkdownViewer.vue'
const route = useRoute()
const DIM_CN: Record<string, string> = { accuracy: '知识准确性', logic: '逻辑完整性', clarity: '表达清晰度', depth: '技术深度' }
const sessions = ref<any[]>([])
const selected = ref<number>()
const report = ref<any>(null)
const loading = ref(false)
const dimensions = computed(() => Object.entries(report.value?.dimension_scores || {}).map(([key, value]) => ({ key, label: DIM_CN[key] || key, value: Number(value) })))
const weakest = computed(() => {
  const dims = dimensions.value
  return dims.length ? dims.reduce((min, d) => (d.value < min.value ? d : min)).key : null
})
async function loadReport() { if (!selected.value) return; loading.value = true; try { report.value = (await aiApi.report(selected.value)).data } finally { loading.value = false } }
onMounted(async () => { sessions.value = (await aiApi.sessions()).data.filter((s: any) => s.status === 'FINISHED'); selected.value = route.query.session ? Number(route.query.session) : sessions.value[0]?.id; await loadReport() })
</script>
<template>
  <header class="student-heading report-heading"><div><h1>AI 模拟答辩报告</h1><p>基于答辩表现整理能力评估与复习建议（练习结果，不计入正式成绩）</p></div><el-select v-if="sessions.length" v-model="selected" style="width:230px" @change="loadReport"><el-option v-for="s in sessions" :key="s.id" :value="s.id" :label="`答辩 #${s.id} · ${(s.finished_at || '').slice(0, 10)}`" /></el-select></header>
  <div v-loading="loading" v-if="report">
    <div class="report-top">
      <section class="page-card score-card"><h3 class="block-title">综合得分</h3><strong>{{ report.overall_score }}</strong><span>{{ report.overall_score >= 80 ? '良好' : report.overall_score >= 60 ? '合格' : '继续加油' }}</span></section>
      <section class="page-card">
        <h3 class="block-title">能力维度</h3>
        <div v-for="d in dimensions" :key="d.key" class="dimension">
          <span>{{ d.label }}</span>
          <el-progress :percentage="d.value" :stroke-width="12" color="#1878fd" />
          <em>{{ d.value }}</em>
          <el-tag v-if="d.key === weakest" size="small" type="warning">建议继续训练</el-tag>
        </div>
        <p class="report-summary">{{ report.summary }}</p>
      </section>
    </div>
    <div class="report-bottom">
      <section class="page-card">
        <h3 class="block-title">薄弱点与建议</h3>
        <div v-for="(text, i) in report.weaknesses" :key="i" class="advice"><strong>{{ Number(i) + 1 }}. {{ text }}</strong><p>{{ report.suggestions?.[i] || '结合论文中的实现细节继续复习。' }}</p></div>
        <h3 class="block-title">优势表现</h3><div v-for="(text, i) in report.strengths" :key="i" class="muted-row">{{ text }}</div>
      </section>
      <section class="page-card">
        <h3 class="block-title">高频追问题库</h3>
        <div v-for="(text, i) in report.risky_questions" :key="i" class="muted-row">{{ Number(i) + 1 }}. {{ text }}</div>
        <el-button type="primary" class="report-action" @click="$router.push('/student/ai-defense')">继续模拟答辩</el-button>
        <el-button @click="$router.push('/student/ai-ppt')">生成 PPT 大纲</el-button>
      </section>
    </div>
    <section class="page-card review-turns"><h3 class="block-title">逐题回顾</h3><el-collapse><el-collapse-item v-for="t in report.turns" :key="t.turn_no" :title="`第 ${t.turn_no} 题 · ${t.question}`"><p>{{ t.student_answer }}</p><MarkdownViewer :content="t.ai_feedback || ''" /></el-collapse-item></el-collapse></section>
  </div>
  <section v-else class="page-card"><el-empty description="完成一次模拟答辩后生成报告"><el-button type="primary" @click="$router.push('/student/ai-defense')">开始模拟答辩</el-button></el-empty></section>
</template>
<style scoped>
.report-heading { display: flex; justify-content: space-between; gap: 20px; }
.report-top { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 22px; margin-bottom: 22px; }
.score-card strong { font-size: 64px; color: #1878fd; display: block; text-align: center; margin-top: 25px; }
.score-card > span { display: block; text-align: center; color: #16b782; margin-bottom: 25px; }
.dimension { display: flex; align-items: center; gap: 16px; margin: 14px 0; }
.dimension > span { width: 85px; font-size: 12px; color: #8a93a5; flex-shrink: 0; }
.dimension .el-progress { flex: 1; }
.dimension em { font-style: normal; font-size: 13px; color: #586478; width: 26px; text-align: right; }
.report-summary { color: #8a93a5; font-size: 12px; line-height: 1.8; }
.report-bottom { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.advice { background: #f8f9fc; padding: 16px; margin-bottom: 14px; font-size: 13px; }
.advice p { color: #949baa; font-size: 12px; line-height: 1.8; margin-bottom: 0; }
.report-action { margin-top: 24px; }
.review-turns { margin-top: 22px; }
@media (max-width: 800px) {
  .report-top, .report-bottom { grid-template-columns: 1fr; }
  .dimension { flex-wrap: wrap; row-gap: 6px; gap: 8px; }
  .dimension > span { width: auto; flex-shrink: 0; }
  .dimension .el-progress { min-width: 120px; }
  .dimension .el-tag { flex-basis: 100%; width: fit-content; }
}
</style>
