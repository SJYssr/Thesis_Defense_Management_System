<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { defenseApi } from '@/api/defense'

const sessions = ref<any[]>([])
const loaded = ref(false)

const SEAT_CN: Record<string, string> = {
  SCHEDULED: '已排期', ATTENDED: '已到场', ABSENT: '缺席', POSTPONED: '已延期', RETAKE: '重答辩',
}

const STEP_CN = ['材料审核', '已排期', '已答辩', '评审中', '结论已发布']
const currentStep = computed(() => (s: any) => {
  if (s.result?.status === 'PUBLISHED') return 4
  if (s.result) return 3
  if (s.assignment.seat_status === 'ATTENDED') return 2
  if (s.assignment.seat_status === 'POSTPONED') return 1
  return 1
})

async function load() {
  const res = await defenseApi.mine()
  sessions.value = res.data || []
  loaded.value = true
}

async function ack(s: any) {
  await defenseApi.acknowledge(s.assignment.id)
  ElMessage.success('已确认')
  load()
}

onMounted(load)
</script>

<template>
  <header class="student-heading"><h1>我的答辩</h1><p>正式答辩场次、评委名单与发布后的结论</p></header>

  <section class="page-card ai-tip">
    AI 模拟答辩为<b>练习功能，不计入正式成绩</b>。正式结论以下方场次发布为准。
    <el-button size="small" text type="primary" @click="$router.push('/student/ai-defense')">进入练习</el-button>
  </section>

  <section v-if="loaded && !sessions.length" class="page-card">
    <el-empty description="尚未安排正式答辩">
      <template #image="{ }"><span /></template>
      <p>材料审核通过后，由学院统一安排答辩场次。安排发布后将在此显示时间、地点与评委。</p>
    </el-empty>
  </section>

  <div v-for="s in sessions" :key="s.id" class="page-card session-card">
    <div class="progress-bar">
      <div v-for="(step, i) in STEP_CN" :key="step" class="step" :class="{ active: i <= currentStep(s), passed: i < currentStep(s) }">
        <span class="dot">{{ i < currentStep(s) ? '✓' : i + 1 }}</span>
        <span class="label">{{ step }}</span>
      </div>
    </div>

    <div class="session-head">
      <div>
        <el-tag size="small">{{ s.stage_cn }}</el-tag>
        <strong class="title">{{ s.title || '答辩场次' }}</strong>
        <el-tag size="small" :type="s.status === 'PUBLISHED' ? 'success' : 'info'">{{ s.status_cn }}</el-tag>
      </div>
      <div class="when">
        {{ s.starts_at?.replace('T', ' ').slice(0, 16) }} ~ {{ s.ends_at?.slice(11, 16) }}
      </div>
    </div>

    <div class="info-grid">
      <div><span class="k">地点</span>{{ s.is_online ? '线上' : s.location || '待定' }}
        <el-link v-if="s.online_link" type="primary" :href="s.online_link" target="_blank">会议链接</el-link>
      </div>
      <div><span class="k">席位状态</span><el-tag size="small" :type="s.assignment.seat_status === 'SCHEDULED' ? 'warning' : 'success'">{{ SEAT_CN[s.assignment.seat_status] }}</el-tag></div>
      <div v-if="s.assignment.remark"><span class="k">备注</span>{{ s.assignment.remark }}</div>
    </div>

    <div class="examiners" v-if="s.examiners?.length">
      <span class="k">评委</span>
      <el-tag v-for="e in s.examiners" :key="e.teacher_id" size="small" effect="plain" class="ex-tag">
        {{ e.teacher_name }}（{{ e.role_cn }}）
      </el-tag>
    </div>

    <div v-if="s.changes?.length" class="changes">
      <strong>变更记录</strong>
      <div v-for="(c, i) in s.changes" :key="i" class="change-row">
        调整为 {{ c.after_starts_at?.replace('T', ' ').slice(0, 16) }}
        <span v-if="c.after_location">· 地点 {{ c.after_location }}</span>
        · 原因：{{ c.reason || '未说明' }}
      </div>
    </div>

    <div class="result-card" :class="{ published: s.result?.status === 'PUBLISHED' }">
      <template v-if="s.result?.status === 'PUBLISHED'">
        <strong>正式结论</strong>
        <span class="score">{{ s.result.score }} 分</span>
        <el-tag :type="s.result.decision === 'PASS' ? 'success' : 'danger'">
          {{ s.result.decision === 'PASS' ? '通过' : s.result.decision === 'FAIL' ? '不通过' : s.result.decision }}
        </el-tag>
        <span class="muted">发布于 {{ s.result.published_at?.replace('T', ' ').slice(0, 16) }}</span>
      </template>
      <template v-else-if="s.result">
        <strong>成绩复核中</strong><span class="muted">评审结论正在复核，复核完成后将重新发布。</span>
      </template>
      <template v-else>
        <strong>评审中</strong><span class="muted">结论尚未发布，发布后将在此显示成绩与决定。</span>
      </template>
    </div>

    <div class="card-foot" v-if="s.assignment.seat_status === 'SCHEDULED' && !s.assignment.acknowledged_at">
      <el-button type="primary" size="small" @click="ack(s)">确认已读</el-button>
    </div>
    <div class="card-foot acknowledged" v-else-if="s.assignment.acknowledged_at">
      已于 {{ s.assignment.acknowledged_at.replace('T', ' ').slice(0, 16) }} 确认
    </div>
  </div>
</template>

<style scoped>
.ai-tip { padding: 12px 18px; font-size: 13px; color: #606266; margin-bottom: 16px; border-left: 4px solid #e6a23c; }
.session-card { margin-bottom: 18px; }
.progress-bar { display: flex; gap: 4px; margin-bottom: 18px; }
.step { flex: 1; text-align: center; font-size: 12px; color: #a8abb2; }
.step .dot { display: inline-flex; width: 22px; height: 22px; border-radius: 50%; background: #eef0f3; color: #909399; align-items: center; justify-content: center; font-size: 11px; margin-bottom: 4px; }
.step.active { color: #1878fd; }
.step.active .dot { background: #1878fd; color: #fff; }
.step.passed .dot { background: #d9ecff; color: #1878fd; }
.session-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.session-head .title { margin: 0 10px; font-size: 15px; }
.when { font-size: 13px; color: #303133; }
.info-grid { display: flex; gap: 28px; flex-wrap: wrap; margin: 14px 0; font-size: 13px; }
.info-grid .k, .examiners .k { color: #909399; margin-right: 8px; }
.examiners { font-size: 13px; margin-bottom: 12px; }
.ex-tag { margin-right: 8px; }
.changes { background: #fdf6ec; border-radius: 6px; padding: 10px 14px; font-size: 12px; margin-bottom: 12px; }
.changes strong { display: block; margin-bottom: 4px; color: #b88230; }
.change-row { padding: 2px 0; color: #7a5b1f; }
.result-card { display: flex; align-items: center; gap: 12px; padding: 14px 16px; background: #f4f6f9; border-radius: 8px; font-size: 13px; }
.result-card.published { background: #f0f9eb; }
.result-card .score { font-size: 22px; color: #67c23a; font-weight: 600; }
.result-card .muted { color: #909399; }
.card-foot { margin-top: 12px; display: flex; justify-content: flex-end; font-size: 12px; color: #909399; }
@media (max-width: 640px) {
  .info-grid { gap: 10px; flex-direction: column; }
  .progress-bar { flex-wrap: wrap; }
  .step { min-width: 30%; margin-bottom: 6px; }
}
</style>
