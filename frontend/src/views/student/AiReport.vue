<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { aiApi } from '@/api/common'
import { syncObservations } from '@/api/practiceObservations'
import { useUserStore } from '@/stores/user'
import MarkdownViewer from '@/components/MarkdownViewer.vue'
import PracticeAssessment from '@/components/PracticeAssessment.vue'
import PracticeSources from '@/components/PracticeSources.vue'
const route = useRoute()
const userStore = useUserStore()
const observationsPending = ref(false)
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
let requestId = 0
const loadFailed = ref(false)
async function loadReport() {
  if (!selected.value) return
  const id = ++requestId
  report.value = null
  loading.value = true
  loadFailed.value = false
  try {
    const sessionId = selected.value
    const owner = userStore.user?.id
    const saved = owner ? await syncObservations(owner, sessionId) : true
    if (id !== requestId) return
    observationsPending.value = !saved
    const { data } = await aiApi.report(sessionId)
    if (id === requestId) report.value = data
  } catch {
    if (id === requestId) loadFailed.value = true
  } finally { if (id === requestId) loading.value = false }
}
onMounted(async () => {
  sessions.value = (await aiApi.sessions()).data.filter((s: any) => s.status === 'FINISHED')
  const requested = Number(route.query.session)
  selected.value = sessions.value.find(s => s.id === requested)?.id || sessions.value[0]?.id
  await loadReport()
})
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
        <h3 v-if="report.weaknesses?.length" class="block-title">需要补强</h3>
        <p v-for="(text, i) in report.weaknesses" :key="i" class="advice">{{ text }}</p>
        <h3 v-if="report.suggestions?.length" class="block-title">复习建议</h3>
        <p v-for="(text, i) in report.suggestions" :key="i" class="muted-row">{{ text }}</p>
        <h3 v-if="report.strengths?.length" class="block-title">优势表现</h3>
        <p v-for="(text, i) in report.strengths" :key="i" class="muted-row">{{ text }}</p>
      </section>
      <section class="page-card">
        <h3 class="block-title">建议再练的问题</h3>
        <div v-for="(text, i) in report.risky_questions" :key="i" class="muted-row">{{ Number(i) + 1 }}. {{ text }}</div>
        <el-button type="primary" class="report-action" @click="$router.push('/student/ai-defense')">继续模拟答辩</el-button>
        <el-button @click="$router.push('/student/ai-ppt')">生成 PPT 大纲</el-button>
      </section>
    </div>
    <section v-if="report.observation_counts || observationsPending" class="page-card review-turns">
      <h3 class="block-title">练习环境回顾</h3>
      <p v-if="observationsPending" role="status">本机还有未同步的提醒。<el-button link type="primary" @click="loadReport">重试同步</el-button></p>
      <template v-if="report.observation_counts">
        <dl class="observation-counts">
          <div><dt>持续未检测到人脸</dt><dd>{{ report.observation_counts.no_face }} 次</dd></div>
          <div><dt>持续检测到多张人脸</dt><dd>{{ report.observation_counts.multiple_faces }} 次</dd></div>
          <div><dt>答题页转入后台</dt><dd>{{ report.observation_counts.tab_hidden }} 次</dd></div>
        </dl>
        <p class="report-summary">仅统计你启用观察期间的提示，光线、遮挡也会影响识别。此记录仅供本人复盘，不参与评分。</p>
      </template>
    </section>
    <section class="page-card review-turns">
      <h3 class="block-title">逐轮回顾</h3>
      <el-collapse>
        <el-collapse-item v-for="t in report.turns" :key="t.turn_no" :title="`第 ${t.turn_no} 轮 · ${t.is_follow_up ? '追问 · ' : ''}${t.question}`">
          <b>你的回答</b><p class="answer">{{ t.student_answer }}</p>
          <b>本轮反馈<span v-if="t.total_score != null"> · {{ t.total_score }} 分</span></b>
          <PracticeAssessment :assessment="t.assessment" :sources="t.sources" />
          <MarkdownViewer :content="t.ai_feedback || ''" />
          <PracticeSources :sources="t.sources" />
        </el-collapse-item>
      </el-collapse>
    </section>
  </div>
  <section v-else-if="loading || loadFailed" class="page-card" role="status">
    {{ loading ? '正在读取练习报告…' : '报告读取失败。' }}
    <el-button v-if="loadFailed" @click="loadReport">重试</el-button>
  </section>
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
.review-turns { margin-top: 22px; overflow-wrap: anywhere; }
.observation-counts { display: flex; flex-wrap: wrap; gap: 12px 32px; margin: 12px 0; }
.observation-counts dt { font-size: 13px; color: #586478; }
.observation-counts dd { margin: 4px 0 0; font-weight: 600; }
.review-turns .answer { white-space: pre-wrap; }
.report-heading { flex-wrap: wrap; }
@media (max-width: 800px) {
  .report-top, .report-bottom { grid-template-columns: 1fr; }
  .dimension { flex-wrap: wrap; row-gap: 6px; gap: 8px; }
  .dimension > span { width: auto; flex-shrink: 0; }
  .dimension .el-progress { min-width: 120px; }
  .dimension .el-tag { flex-basis: 100%; width: fit-content; }
}
</style>
