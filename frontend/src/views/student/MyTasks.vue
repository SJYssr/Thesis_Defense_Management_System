<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { taskApi } from '@/api/defense'

const router = useRouter()
const year = ref('')
const tasks = ref<any[]>([])
const loaded = ref(false)

const STAGE_ROUTE: Record<string, string> = {
  SELECTION: '/student/topics',
  OPENING: '/student/opening',
  MIDTERM: '/student/midterm',
  FINAL: '/student/final',
  MATERIAL: '/student/material',
}

function statusType(s: string) {
  if (s === 'APPROVED') return 'success'
  if (s === 'SUBMITTED' || s === 'PENDING') return 'warning'
  if (s === 'REJECTED' || s === 'RESUBMIT_REQUIRED') return 'danger'
  return 'info'
}

async function load() {
  const res = await taskApi.mine()
  year.value = res.data.academic_year || ''
  tasks.value = res.data.tasks || []
  loaded.value = true
}

onMounted(load)
</script>

<template>
  <header class="student-heading"><h1>我的任务</h1><p>按学年发布的阶段任务、截止时间与所需材料</p></header>

  <section v-if="loaded && !tasks.length" class="page-card">
    <el-empty description="当前学年暂无已发布任务">
      <template #description>
        <p>当前学年{{ year ? `(${year})` : '' }}暂无已发布任务。<br />请先完成选题，任务发布后将在此显示截止时间与所需材料。</p>
      </template>
      <el-button type="primary" @click="router.push('/student/topics')">前往题目中心</el-button>
    </el-empty>
  </section>

  <div v-if="tasks.length" class="task-list">
    <section v-for="t in tasks" :key="t.id" class="page-card task-card" :class="{ overdue: t.overdue, done: t.done }">
      <div class="task-head">
        <div class="task-title">
          <el-tag size="small" type="info">{{ t.stage_cn }}</el-tag>
          <strong>{{ t.title }}</strong>
          <el-tag v-if="t.done" size="small" type="success">已完成</el-tag>
          <el-tag v-else-if="t.overdue" size="small" type="danger">已逾期</el-tag>
          <el-tag v-else-if="t.not_opened" size="small" type="info">未开始</el-tag>
        </div>
        <div class="task-due">
          <template v-if="t.due_at">截止：<b>{{ t.due_at.replace('T', ' ').slice(0, 16) }}</b></template>
          <span v-else>无截止时间</span>
        </div>
      </div>
      <p v-if="t.description" class="task-desc">{{ t.description }}</p>
      <div class="task-meta">
        <div class="req-list">
          所需材料：
          <el-tag v-for="r in t.requirements" :key="r" size="small" effect="plain" class="req-tag">{{ r }}</el-tag>
          <span v-if="!t.requirements.length" class="muted">以任务说明为准</span>
        </div>
        <div class="material-state">
          <span>提交状态：<el-tag size="small" :type="statusType(t.material_status) as any">{{ t.material_status_cn }}</el-tag></span>
          <span v-if="t.submitted_at">提交于 {{ t.submitted_at.replace('T', ' ').slice(0, 16) }}</span>
        </div>
      </div>
      <div v-if="t.review_comment" class="review-note">
        <strong>教师反馈：</strong>{{ t.review_comment }}
        <span v-if="t.reviewed_at">（{{ t.reviewed_at.replace('T', ' ').slice(0, 16) }}）</span>
      </div>
      <div class="task-foot">
        <span v-if="t.overdue && t.late_policy === 'DENY'" class="late-hint">该任务逾期不允许补交</span>
        <el-button size="small" type="primary" plain :disabled="t.not_opened && !t.done"
                   @click="router.push(STAGE_ROUTE[t.stage] || '/student/progress')">
          {{ t.done ? '查看材料' : '进入对应页面' }}
        </el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.task-list { display: flex; flex-direction: column; gap: 16px; }
.task-card { border-left: 4px solid #1878fd; }
.task-card.overdue { border-left-color: #f56c6c; }
.task-card.done { border-left-color: #67c23a; }
.task-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
.task-title { display: flex; align-items: center; gap: 10px; font-size: 15px; }
.task-due { font-size: 13px; color: #606266; }
.task-desc { font-size: 13px; color: #606266; margin: 10px 0; }
.task-meta { display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; font-size: 13px; }
.req-tag { margin-right: 6px; }
.material-state { display: flex; gap: 16px; align-items: center; }
.review-note { margin-top: 10px; padding: 10px 12px; background: #f4f8ff; border-radius: 6px; font-size: 13px; }
.task-foot { margin-top: 12px; display: flex; justify-content: flex-end; align-items: center; gap: 12px; }
.late-hint { color: #f56c6c; font-size: 12px; }
.muted { color: #909399; }
@media (max-width: 640px) {
  .task-head, .task-meta { flex-direction: column; align-items: flex-start; gap: 8px; }
}
</style>
