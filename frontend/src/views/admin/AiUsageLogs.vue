<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import request from '@/api/request'

interface UsageRow {
  id: number
  user_id: number | null
  username: string | null
  real_name: string | null
  scene: string | null
  scene_label: string | null
  model: string | null
  input_tokens: number
  output_tokens: number
  latency_ms: number
  created_at: string | null
}

interface Summary {
  calls: number
  input_tokens: number
  output_tokens: number
  avg_latency_ms: number
  today_calls: number
}

const rows = ref<UsageRow[]>([])
const summary = ref<Summary>({ calls: 0, input_tokens: 0, output_tokens: 0, avg_latency_ms: 0, today_calls: 0 })
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const query = reactive({ scene: '', keyword: '', dates: [] as string[] })

const SCENES = [
  { label: '生成问题', value: 'GENERATE_QUESTIONS' },
  { label: '回答评价', value: 'DEFENSE_QA' },
  { label: '进度分析', value: 'PROGRESS_ANALYSIS' },
  { label: 'PPT大纲', value: 'PPT_OUTLINE' },
  { label: '知识问答', value: 'QA' },
]

async function load() {
  loading.value = true
  try {
    const res = await request.get('/admin/ai-usage-logs', {
      params: {
        page: page.value, page_size: pageSize.value,
        scene: query.scene || undefined, keyword: query.keyword.trim() || undefined,
        from_date: query.dates?.[0], to_date: query.dates?.[1],
      },
    })
    rows.value = res.data.list
    total.value = res.data.total
    summary.value = res.data.summary
  } finally {
    loading.value = false
  }
}

function search() { page.value = 1; load() }
function reset() {
  query.scene = ''
  query.keyword = ''
  query.dates = []
  search()
}

function fmtTokens(n: number) {
  return n >= 10000 ? `${(n / 10000).toFixed(1)} 万` : String(n)
}

onMounted(load)
</script>

<template>
  <div class="page-card ai-log-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">AI 调用日志</h1>
        <p class="hint">AI 能力的调用明细与 Token 用量（roadmap 七十七章 ai_usage_log）</p>
      </div>
    </div>

    <div class="summary-cards">
      <div class="summary-card">
        <div class="s-value">{{ summary.calls }}</div>
        <div class="s-label">累计调用<span class="s-extra">今日 {{ summary.today_calls }} 次</span></div>
      </div>
      <div class="summary-card">
        <div class="s-value">{{ fmtTokens(summary.input_tokens + summary.output_tokens) }}</div>
        <div class="s-label">累计 Token<span class="s-extra">输入 {{ fmtTokens(summary.input_tokens) }} / 输出 {{ fmtTokens(summary.output_tokens) }}</span></div>
      </div>
      <div class="summary-card">
        <div class="s-value">{{ summary.avg_latency_ms }} ms</div>
        <div class="s-label">平均响应耗时</div>
      </div>
    </div>

    <div class="log-filters">
      <el-select v-model="query.scene" placeholder="全部场景" clearable @change="search">
        <el-option v-for="s in SCENES" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-input v-model="query.keyword" placeholder="账号、姓名或模型名" clearable @keyup.enter="search" />
      <el-date-picker v-model="query.dates" type="daterange" value-format="YYYY-MM-DD"
        start-placeholder="开始日期" end-placeholder="结束日期" range-separator="至" />
      <el-button type="primary" @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </div>

    <el-table v-loading="loading" :data="rows" empty-text="暂无符合条件的调用记录">
      <el-table-column label="时间" min-width="170">
        <template #default="{ row }">{{ row.created_at?.replace('T', ' ') || '未记录' }}</template>
      </el-table-column>
      <el-table-column label="用户" min-width="140">
        <template #default="{ row }">{{ row.real_name || row.username || `用户 #${row.user_id || '未知'}` }}<small v-if="row.username && row.real_name"> ({{ row.username }})</small></template>
      </el-table-column>
      <el-table-column label="场景" min-width="110">
        <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.scene_label || row.scene || '-' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="model" label="模型" min-width="130" show-overflow-tooltip />
      <el-table-column label="输入 Token" min-width="100" align="right">
        <template #default="{ row }">{{ row.input_tokens }}</template>
      </el-table-column>
      <el-table-column label="输出 Token" min-width="100" align="right">
        <template #default="{ row }">{{ row.output_tokens }}</template>
      </el-table-column>
      <el-table-column label="耗时" min-width="90" align="right">
        <template #default="{ row }">{{ row.latency_ms }} ms</template>
      </el-table-column>
    </el-table>

    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" class="log-pagination"
      :total="total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next"
      @current-change="load" @size-change="search" />
  </div>
</template>

<style scoped>
.hint { color: #718096; font-size: 13px; margin: 6px 0 0; }
.summary-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin: 20px 0; }
.summary-card { background: linear-gradient(135deg, #f4f9ff, #fff); border: 1px solid #e3eefb; border-radius: 10px; padding: 16px 18px; }
.s-value { font-size: 26px; font-weight: 700; color: #0f2b52; line-height: 1.2; }
.s-label { font-size: 13px; color: #6b7a90; margin-top: 4px; }
.s-extra { display: block; font-size: 12px; color: #93a1b4; margin-top: 2px; }
.log-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 20px; }
.log-filters .el-select { width: 140px; }
.log-filters .el-input { width: 220px; }
.log-pagination { justify-content: flex-end; margin-top: 20px; }
small { color: #8492a6; }
@media (max-width: 700px) {
  .log-filters > :is(.el-select, .el-input, .el-date-editor) { width: 100%; }
  .log-pagination { justify-content: flex-start; overflow-x: auto; }
}
</style>
