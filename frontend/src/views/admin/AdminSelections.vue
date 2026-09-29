<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { selectionApi } from '@/api/business'
import StatusTag from '@/components/StatusTag.vue'

const items = ref<any[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20, status: 'PENDING', keyword: '' })

const detailVisible = ref(false)
const current = ref<any>(null)
const comment = ref('')
const reviewing = ref(false)

async function load() {
  const res = await selectionApi.adminList({ ...query, status: query.status || undefined })
  const kw = query.keyword.trim()
  const rows = kw
    ? res.data.items.filter((i: any) => i.student_name?.includes(kw) || i.student_no?.includes(kw))
    : res.data.items
  items.value = rows
  total.value = kw ? rows.length : res.data.total
}

async function openDetail(row: any) {
  current.value = row
  comment.value = ''
  detailVisible.value = true
}

async function review(approve: boolean) {
  reviewing.value = true
  try {
    if (approve) await selectionApi.adminApprove(current.value.id, comment.value || undefined)
    else await selectionApi.adminReject(current.value.id, comment.value)
    ElMessage.success(approve ? '已通过' : '已驳回')
    detailVisible.value = false
    await load()
  } catch {
    /* 已提示 */
  } finally {
    reviewing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <!-- 标题 + 待审核统计 -->
    <div class="page-header">
      <h3 class="page-title">选题审核</h3>
      <span v-if="query.status === 'PENDING'" class="total-chip">待审核 {{ total }} 条</span>
    </div>

    <!-- Tab + 搜索 -->
    <div class="tab-row">
      <el-tabs v-model="query.status" class="flex-tabs" @tab-change="query.page = 1; load()">
        <el-tab-pane label="待审核" name="PENDING" />
        <el-tab-pane label="已通过" name="APPROVED" />
        <el-tab-pane label="已驳回" name="REJECTED" />
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
      <el-table-column prop="topic_title" label="题目名称" min-width="230" show-overflow-tooltip />
      <el-table-column prop="teacher_name" label="指导教师" width="95" />
      <el-table-column prop="student_reason" label="选题理由" min-width="180" show-overflow-tooltip />
      <el-table-column label="状态" width="95" align="center">
        <template #default="{ row }"><StatusTag :status="row.status" /></template>
      </el-table-column>
      <el-table-column prop="created_at" label="申请时间" width="165">
        <template #default="{ row }">{{ (row.created_at || '-').replace('T', ' ').slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="110" fixed="right" align="center">
        <template #default="{ row }">
          <el-button v-if="row.status === 'PENDING'" size="small" type="primary" plain @click="openDetail(row)">
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
    <el-drawer v-model="detailVisible" :title="`选题审核 · ${current?.student_name || ''}`" size="560px">
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
              {{ (current.created_at || '').replace('T', ' ').slice(0, 16) }} 申请
              <StatusTag :status="current.status" />
            </div>
          </div>
        </div>

        <!-- 题目信息 -->
        <div class="field-block">
          <div class="field-label">所选题目</div>
          <div class="field-content">{{ current.topic_title }}</div>
        </div>
        <div class="field-block">
          <div class="field-label">指导教师</div>
          <div class="field-content">{{ current.teacher_name || '-' }}</div>
        </div>
        <div class="field-block">
          <div class="field-label">选题理由</div>
          <div class="field-content">{{ current.student_reason || '无' }}</div>
        </div>
        <div v-if="current.review_comment" class="field-block">
          <div class="field-label">审核意见</div>
          <div class="field-content">{{ current.review_comment }}</div>
        </div>

        <!-- 审核操作 -->
        <div v-if="current.status === 'PENDING'" class="review-footer">
          <el-input v-model="comment" type="textarea" :rows="3" placeholder="审核意见(驳回时必填)" />
          <div class="review-btns">
            <el-button type="danger" plain :loading="reviewing" @click="review(false)">驳 回</el-button>
            <el-button type="primary" :loading="reviewing" @click="review(true)">通 过</el-button>
          </div>
        </div>
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
}

.review-footer {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #ebeef5;
}

.review-btns {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 12px;
}
</style>
