<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { stageApi } from '@/api/business'
import StatusTag from '@/components/StatusTag.vue'
import ReviewHistory from '@/components/ReviewHistory.vue'

const route = useRoute()
const stage = computed(() => (route.meta.stage as string) || 'opening')
const isAdmin = computed(() => route.meta.reviewer === 'admin')

const META: Record<string, { title: string; adminTitle: string; fields: { key: string; label: string }[] }> = {
  opening: {
    title: '开题报告审核',
    adminTitle: '开题报告管理',
    fields: [
      { key: 'title', label: '报告题目' },
      { key: 'research_background', label: '研究背景' },
      { key: 'research_significance', label: '研究意义' },
      { key: 'research_content', label: '研究内容' },
      { key: 'research_method', label: '研究方法' },
      { key: 'technical_route', label: '技术路线' },
      { key: 'schedule_plan', label: '进度安排' },
      { key: 'references', label: '参考文献' },
    ],
  },
  midterm: {
    title: '中期报告审核',
    adminTitle: '中期答辩管理',
    fields: [
      { key: 'progress_description', label: '总体进度说明' },
      { key: 'completed_content', label: '已完成内容' },
      { key: 'remaining_content', label: '剩余工作' },
      { key: 'existing_problems', label: '存在问题' },
      { key: 'next_plan', label: '下一步计划' },
    ],
  },
  final: {
    title: '终期答辩审核',
    adminTitle: '终期答辩管理',
    fields: [
      { key: 'summary', label: '工作总结' },
      { key: 'innovation', label: '创新点' },
      { key: 'technical_solution', label: '技术方案' },
      { key: 'test_result', label: '测试结果' },
    ],
  },
}
const meta = computed(() => META[stage.value])
const pageTitle = computed(() => (isAdmin.value ? meta.value.adminTitle : meta.value.title))
const STAGE_CN: Record<string, string> = { opening: '开题报告', midterm: '中期报告', final: '终期答辩报告' }

const items = ref<any[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20, status: 'SUBMITTED', keyword: '' })

const detailVisible = ref(false)
const current = ref<any>(null)
const comment = ref('')
const score = ref<number | undefined>(undefined)
const reviewing = ref(false)

async function load() {
  const params: Record<string, unknown> = { ...query, status: query.status || undefined }
  if (isAdmin.value && query.keyword) {
    params.keyword = query.keyword
  }
  const res = isAdmin.value
    ? await stageApi.adminList(stage.value, params)
    : await stageApi.teacherList(stage.value, params)
  // 前端兜底过滤学号/姓名(教师端无后端 keyword 支持)
  const kw = query.keyword.trim()
  const rows = kw
    ? res.data.items.filter((i: any) => i.student_name?.includes(kw) || i.student_no?.includes(kw))
    : res.data.items
  items.value = rows
  total.value = kw ? rows.length : res.data.total
}

async function openDetail(row: any) {
  const res = isAdmin.value
    ? await stageApi.adminDetail(stage.value, row.id)
    : await stageApi.teacherDetail(stage.value, row.id)
  current.value = res.data
  comment.value = ''
  score.value = undefined
  detailVisible.value = true
}

async function review(approve: boolean) {
  reviewing.value = true
  try {
    if (isAdmin.value) {
      if (approve) await stageApi.adminApprove(stage.value, current.value.id, comment.value || undefined)
      else await stageApi.adminReject(stage.value, current.value.id, comment.value || undefined)
    } else {
      if (approve) await stageApi.approve(stage.value, current.value.id, comment.value || undefined, score.value)
      else await stageApi.reject(stage.value, current.value.id, comment.value || undefined)
    }
    ElMessage.success(approve ? '已通过' : '已驳回')
    detailVisible.value = false
    await load()
  } catch {
    /* 已提示 */
  } finally {
    reviewing.value = false
  }
}

const canReview = computed(() => current.value?.status === 'SUBMITTED')

onMounted(load)
</script>

<template>
  <div class="page-card">
    <!-- 标题 + 待审核统计 -->
    <div class="page-header">
      <h3 class="page-title">{{ pageTitle }}</h3>
      <span v-if="query.status === 'SUBMITTED'" class="total-chip">待审核 {{ total }} 条</span>
    </div>

    <!-- Tab + 搜索 -->
    <div class="tab-row">
      <el-tabs v-model="query.status" class="flex-tabs" @tab-change="query.page = 1; load()">
        <el-tab-pane label="待审核" name="SUBMITTED" />
        <el-tab-pane label="已通过" name="APPROVED" />
        <el-tab-pane label="已驳回" name="RESUBMIT_REQUIRED" />
        <el-tab-pane label="全部" name="" />
      </el-tabs>
      <el-input v-model="query.keyword" placeholder="请输入学生姓名 / 学号" clearable style="width: 220px"
                @keyup.enter="load" @clear="load" />
    </div>

    <!-- 表格 -->
    <el-table :data="items" border>
      <el-table-column label="学生" width="130">
        <template #default="{ row }">
          <div class="student-cell">
            <span class="student-name">{{ row.student_name }}</span>
            <span class="student-no">{{ row.student_no }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="题目名称" min-width="240">
        <template #default="{ row }">
          <span class="topic-name">{{ row.content?.title || `${row.student_name}的${STAGE_CN[stage]}` }}</span>
        </template>
      </el-table-column>
      <el-table-column label="指导教师" width="90">
        <template #default="{ row }">{{ row.teacher_name || '-' }}</template>
      </el-table-column>
      <el-table-column label="版本" width="70" align="center">
        <template #default="{ row }">v{{ row.current_version }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }"><StatusTag :status="row.status" /></template>
      </el-table-column>
      <el-table-column prop="submitted_at" label="提交时间" width="165">
        <template #default="{ row }">{{ (row.submitted_at || '-').replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right" align="center">
        <template #default="{ row }">
          <el-button v-if="row.status === 'SUBMITTED'" size="small" type="primary" plain @click="openDetail(row)">
            审核
          </el-button>
          <el-button v-else size="small" link type="primary" @click="openDetail(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pagination-bar">
      <el-pagination v-model:current-page="query.page" :page-size="query.page_size" :total="total"
                     background layout="total, prev, pager, next" @current-change="load" />
    </div>

    <!-- 审核详情抽屉 -->
    <el-drawer v-model="detailVisible" :title="`${pageTitle} · ${current?.student_name || ''}`" size="620px">
      <div v-if="current" class="drawer-body">
        <!-- 学生信息头 -->
        <div class="stu-head">
          <el-avatar :size="44" style="background: #1a76fd; flex-shrink: 0">
            {{ (current.student_name || '?').slice(0, 1) }}
          </el-avatar>
          <div>
            <div class="stu-name">
              {{ current.student_name }}
              <span class="stu-no">{{ current.student_no }}</span>
            </div>
            <div class="stu-meta">
              v{{ current.current_version }} ·
              {{ (current.submitted_at || '').replace('T', ' ').slice(0, 16) }} 提交
              <StatusTag :status="current.status" />
            </div>
          </div>
        </div>

        <!-- 报告字段块 -->
        <div v-for="f in meta.fields" :key="f.key" class="field-block">
          <div class="field-label">{{ f.label }}</div>
          <div class="field-content">{{ current.content?.[f.key] || '无' }}</div>
        </div>

        <!-- 审核历史 -->
        <h4 class="section-title">审核历史</h4>
        <ReviewHistory :records="current?.review_history || []" />

        <!-- 底部审核操作 -->
        <div v-if="canReview" class="review-footer">
          <div v-if="stage === 'final'" class="score-row">
            <span>终期评分:</span>
            <el-input-number v-model="score" :min="0" :max="100" size="small" />
          </div>
          <el-input v-model="comment" type="textarea" :rows="3" placeholder="审核意见(驳回时必填)" />
          <div class="review-btns">
            <el-button type="danger" plain :loading="reviewing" @click="review(false)">驳 回</el-button>
            <el-button type="primary" :loading="reviewing" @click="review(true)">通 过</el-button>
          </div>
        </div>
        <el-alert v-else type="info" :closable="false" :title="current.status === 'APPROVED' ? '该材料已审核通过' : '该材料不在待审核状态'" />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.total-chip {
  color: #8a97ad;
  font-size: 13px;
  background: #f0f4fa;
  padding: 4px 12px;
  border-radius: 12px;
}

.tab-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.flex-tabs {
  flex: 1;
}

.flex-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}

.student-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}

.student-name {
  font-weight: 600;
  color: #1f2d3d;
}

.student-no {
  font-size: 12px;
  color: #a8b3c5;
}

.topic-name {
  color: #1f2d3d;
}

/* 抽屉内样式 */
.drawer-body {
  padding: 0 4px;
}

.stu-head {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #f5f8fc;
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 18px;
}

.stu-name {
  font-size: 16px;
  font-weight: 700;
  color: #1f2d3d;
}

.stu-no {
  font-size: 12px;
  color: #a8b3c5;
  font-weight: 400;
  margin-left: 6px;
}

.stu-meta {
  font-size: 12px;
  color: #8a97ad;
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.field-block {
  margin-bottom: 14px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  color: #5e6d82;
  margin-bottom: 6px;
}

.field-content {
  background: #f8fafc;
  border-radius: 8px;
  padding: 12px 14px;
  white-space: pre-wrap;
  font-size: 14px;
  color: #303133;
  line-height: 1.7;
  min-height: 20px;
}

.section-title {
  margin: 20px 0 12px;
  font-size: 15px;
  color: #1f2d3d;
}

.review-footer {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #ebeef5;
}

.score-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #5e6d82;
}

.review-btns {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 12px;
}
</style>
