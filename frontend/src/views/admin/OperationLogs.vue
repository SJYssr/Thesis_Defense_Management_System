<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { operationLogApi } from '@/api/common'

interface LogRow {
  id: number
  user_id: number | null
  username: string | null
  real_name: string | null
  module: string | null
  action: string | null
  detail: string | null
  result: string | null
  created_at: string | null
}

const rows = ref<LogRow[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const query = reactive({ module: '', keyword: '', dates: [] as string[] })

async function load() {
  loading.value = true
  try {
    const res = await operationLogApi.list({
      page: page.value, page_size: pageSize.value,
      module: query.module || undefined, keyword: query.keyword.trim() || undefined,
      from_date: query.dates?.[0], to_date: query.dates?.[1],
    })
    rows.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function search() { page.value = 1; load() }
function reset() {
  query.module = ''
  query.keyword = ''
  query.dates = []
  search()
}

onMounted(load)
</script>

<template>
  <div class="page-card audit-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">操作日志</h1>
        <p class="hint">查看答辩、成绩和成果归档等关键业务操作</p>
      </div>
    </div>
    <div class="audit-filters">
      <el-select v-model="query.module" placeholder="全部模块" clearable aria-label="模块" @change="search">
        <el-option label="正式答辩" value="DEFENSE" />
        <el-option label="成果归档" value="ARCHIVE" />
        <el-option label="题目管理" value="TOPIC" />
      </el-select>
      <el-input v-model="query.keyword" placeholder="账号、姓名或操作详情" clearable @keyup.enter="search" />
      <el-date-picker v-model="query.dates" type="daterange" value-format="YYYY-MM-DD"
        start-placeholder="开始日期" end-placeholder="结束日期" range-separator="至" />
      <el-button type="primary" @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </div>
    <el-table v-loading="loading" :data="rows" empty-text="暂无符合条件的操作记录">
      <el-table-column label="时间" min-width="170">
        <template #default="{ row }">{{ row.created_at?.replace('T', ' ') || '未记录' }}</template>
      </el-table-column>
      <el-table-column label="操作者" min-width="130">
        <template #default="{ row }">{{ row.real_name || row.username || `用户 #${row.user_id || '未知'}` }}<small v-if="row.username && row.real_name"> ({{ row.username }})</small></template>
      </el-table-column>
      <el-table-column prop="module" label="模块" min-width="100" />
      <el-table-column prop="action" label="操作" min-width="160" />
      <el-table-column prop="detail" label="详情" min-width="280" show-overflow-tooltip />
      <el-table-column label="结果" width="90">
        <template #default="{ row }"><el-tag size="small" :type="row.result === 'FAIL' ? 'danger' : 'success'">{{ row.result === 'FAIL' ? '失败' : '成功' }}</el-tag></template>
      </el-table-column>
    </el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" class="audit-pagination"
      :total="total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next"
      @current-change="load" @size-change="search" />
  </div>
</template>

<style scoped>
.hint { color: #718096; font-size: 13px; margin: 6px 0 0; }
.audit-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 20px 0; }
.audit-filters .el-select { width: 150px; }
.audit-filters .el-input { width: 220px; }
.audit-pagination { justify-content: flex-end; margin-top: 20px; }
small { color: #8492a6; }
@media (max-width: 700px) {
  .audit-filters > :is(.el-select, .el-input, .el-date-editor) { width: 100%; }
  .audit-pagination { justify-content: flex-start; overflow-x: auto; }
}
</style>
