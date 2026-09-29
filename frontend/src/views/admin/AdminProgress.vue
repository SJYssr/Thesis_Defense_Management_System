<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { progressApi } from '@/api/business'
import StatusTag from '@/components/StatusTag.vue'

const items = ref<any[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20, risk_level: '', stage: '' })

async function load() {
  const res = await progressApi.adminList({
    ...query, risk_level: query.risk_level || undefined, stage: query.stage || undefined,
  })
  items.value = res.data.items
  total.value = res.data.total
}

onMounted(load)
</script>

<template>
  <div class="page-card">
    <div class="page-header">
      <h3 class="page-title">毕设进度(全局)</h3>
    </div>
    <div class="filter-bar">
      <el-select v-model="query.stage" placeholder="阶段" clearable style="width: 130px">
        <el-option label="选题" value="SELECTION" />
        <el-option label="开题" value="OPENING" />
        <el-option label="中期" value="MIDTERM" />
        <el-option label="终期" value="FINAL" />
        <el-option label="材料" value="MATERIAL" />
        <el-option label="已完成" value="COMPLETED" />
      </el-select>
      <el-select v-model="query.risk_level" placeholder="风险等级" clearable style="width: 130px">
        <el-option label="正常" value="NORMAL" />
        <el-option label="警告" value="WARNING" />
        <el-option label="高风险" value="HIGH" />
      </el-select>
      <el-button type="primary" @click="load">查询</el-button>
    </div>
    <el-table :data="items" border>
      <el-table-column prop="student_name" label="学生" width="100" />
      <el-table-column prop="student_no" label="学号" width="110" />
      <el-table-column prop="topic_title" label="题目" min-width="200" show-overflow-tooltip />
      <el-table-column prop="teacher_name" label="指导教师" width="100" />
      <el-table-column prop="current_stage_cn" label="当前阶段" width="90" />
      <el-table-column label="进度" width="170">
        <template #default="{ row }">
          <el-progress :percentage="row.progress_percent" :stroke-width="8"
                       :color="row.progress_percent >= 60 ? '#67c23a' : '#409eff'" />
        </template>
      </el-table-column>
      <el-table-column label="风险" width="85">
        <template #default="{ row }"><StatusTag :status="row.risk_level" /></template>
      </el-table-column>
      <el-table-column prop="risk_reason" label="风险说明" min-width="160" show-overflow-tooltip />
    </el-table>
    <div class="pagination-bar">
      <el-pagination v-model:current-page="query.page" :page-size="query.page_size" :total="total"
                     layout="total, prev, pager, next" @current-change="load" />
    </div>
  </div>
</template>
