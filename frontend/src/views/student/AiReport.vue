<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { aiApi } from '@/api/common'
import MarkdownViewer from '@/components/MarkdownViewer.vue'
const route = useRoute()
const sessions = ref<any[]>([])
const selected = ref<number>()
const report = ref<any>(null)
const loading = ref(false)
async function loadReport() { if (!selected.value) return; loading.value = true; try { report.value = (await aiApi.report(selected.value)).data } finally { loading.value = false } }
onMounted(async () => { sessions.value = (await aiApi.sessions()).data.filter((s:any) => s.status === 'FINISHED'); selected.value = route.query.session ? Number(route.query.session) : sessions.value[0]?.id; await loadReport() })
</script>
<template>
  <header class="student-heading report-heading"><div><h1>AI 模拟答辩报告</h1><p>基于答辩表现整理能力评估与复习建议</p></div><el-select v-if="sessions.length" v-model="selected" style="width:230px" @change="loadReport"><el-option v-for="s in sessions" :key="s.id" :value="s.id" :label="`答辩 #${s.id} · ${(s.finished_at || '').slice(0,10)}`" /></el-select></header>
  <div v-loading="loading" v-if="report">
    <div class="report-top"><section class="page-card score-card"><h3 class="block-title">综合得分</h3><strong>{{ report.overall_score }}</strong><span>{{ report.overall_score >= 80 ? '良好' : report.overall_score >= 60 ? '合格' : '继续加油' }}</span></section><section class="page-card"><h3 class="block-title">能力维度</h3><div v-for="(value,key) in report.dimension_scores" :key="key" class="dimension"><span>{{ key }}</span><el-progress :percentage="Number(value)" :stroke-width="12" color="#1878fd" /></div><p class="report-summary">{{ report.summary }}</p></section></div>
    <div class="report-bottom"><section class="page-card"><h3 class="block-title">薄弱点与建议</h3><div v-for="(text,i) in report.weaknesses" :key="i" class="advice"><strong>{{ text }}</strong><p>{{ report.suggestions?.[i] || '结合论文中的实现细节继续复习。' }}</p></div><h3 class="block-title">优势表现</h3><div v-for="(text,i) in report.strengths" :key="i" class="muted-row">{{ text }}</div></section><section class="page-card"><h3 class="block-title">高频追问题库</h3><div v-for="(text,i) in report.risky_questions" :key="i" class="muted-row">{{ Number(i)+1 }}. {{ text }}</div><el-button type="primary" class="report-action" @click="$router.push('/student/ai-defense')">继续模拟答辩</el-button><el-button @click="$router.push('/student/ai-ppt')">生成 PPT 大纲</el-button></section></div>
    <section class="page-card review-turns"><h3 class="block-title">逐题回顾</h3><el-collapse><el-collapse-item v-for="t in report.turns" :key="t.turn_no" :title="`第 ${t.turn_no} 题 · ${t.question}`"><p>{{ t.student_answer }}</p><MarkdownViewer :content="t.ai_feedback || ''" /></el-collapse-item></el-collapse></section>
  </div>
  <section v-else class="page-card"><el-empty description="完成一次模拟答辩后生成报告"><el-button type="primary" @click="$router.push('/student/ai-defense')">开始模拟答辩</el-button></el-empty></section>
</template>
<style scoped>
.report-heading { display:flex; justify-content:space-between; gap:20px; }.report-top { display:grid; grid-template-columns:280px minmax(0,1fr); gap:22px; margin-bottom:22px; }.score-card strong { font-size:64px; color:#1878fd; display:block; text-align:center; margin-top:25px; }.score-card > span { display:block; text-align:center; color:#16b782; margin-bottom:25px; }.dimension { display:flex; align-items:center; gap:24px; margin:14px 0; }.dimension > span { width:85px; font-size:12px; color:#8a93a5; }.dimension .el-progress { flex:1; }.report-summary { color:#8a93a5; font-size:12px; line-height:1.8; }.report-bottom { display:grid; grid-template-columns:1fr 1fr; gap:22px; }.advice { background:#f8f9fc; padding:16px; margin-bottom:14px; font-size:13px; }.advice p { color:#949baa; font-size:12px; line-height:1.8; margin-bottom:0; }.report-action { margin-top:24px; }.review-turns { margin-top:22px; }
@media(max-width:800px) { .report-top,.report-bottom { grid-template-columns:1fr; } }
</style>
