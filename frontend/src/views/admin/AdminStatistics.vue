<template>
  <div class="stats-page">
    <!-- 筛选区 -->
    <!-- P8: 一个页面里混着两种口径(见各卡头角标), 页面级不再声明单一学年 -->
    <div class="filter-bar"><strong>数据统计</strong><span class="filter-label">实时业务数据 · 角标区分当前学年 / 全部学年</span></div>

    <!-- 统计卡 -->
    <div class="stat-cards">
      <div v-for="c in statCards" :key="c.label" class="stat-card">
        <div class="stat-icon" :style="{ background: c.bg, color: c.color }">
          <svg viewBox="0 0 24 24" fill="currentColor" v-html="c.icon" />
        </div>
        <div class="stat-info">
          <div class="stat-num">{{ c.value }}</div>
          <div class="stat-label">{{ c.label }}<span v-if="c.scope" class="scope-tag">{{ c.scope }}</span></div>

        </div>
        <div v-if="c.ring !== undefined" class="stat-ring" :style="{ '--pct': c.ring, '--ring-color': c.ringColor }">
          <span>{{ c.ring }}%</span>
        </div>
      </div>
    </div>

    <!-- 图表网格 第一行 -->
    <el-row :gutter="12" class="chart-row">
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />各学院学生人数统计</div>
            <span class="head-select">{{ scopeTags.colleges }}</span>
          </div>
          <div ref="collegeChartRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />各阶段通过率统计</div>
            <span class="head-select">{{ scopeTags.passRates }}</span>
          </div>
          <div class="chart-body">
            <div class="stage-legend">
              <span v-for="s in stagePass" :key="s.name" class="lg-item">
                <i :style="{ background: s.color }" />{{ s.name }}
              </span>
            </div>
            <div ref="passChartRef" class="chart-box lg-chart" />
          </div>
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />题目类型分布</div>
            <span class="head-select">{{ scopeTags.topicTypes }}</span>
          </div>
          <div class="donut-wrap">
            <div class="donut-chart">
              <div ref="typeChartRef" class="chart-box donut-box" />
              <div class="donut-center">
                <b>{{ stats.cards.topic_count }}</b><span>题目总数</span>
              </div>
            </div>
            <div class="donut-legend">
              <div v-for="t in topicTypes" :key="t.name" class="dl-item">
                <i :style="{ background: t.color }" />
                <span class="dl-name">{{ t.name }}</span>
                <span class="dl-val">{{ t.value }}（{{ pct(t.value, stats.cards.topic_count) }}%）</span>
              </div>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- 图表网格 第二行 -->
    <el-row :gutter="12" class="chart-row">
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />各专业选题情况</div>
            <span class="head-select">{{ scopeTags.majors }}</span>
          </div>
          <div class="major-table">
            <div class="mt-head">
              <span class="c-name">专业名称</span><span>学生总数</span><span>已选题数</span><span>选题率</span><span>未选题数</span>
            </div>
            <div v-for="m in majorRows" :key="m.name" class="mt-row">
              <span class="c-name">{{ m.name }}</span><span>{{ m.total }}</span><span>{{ m.chosen }}</span>
              <span class="rate">{{ m.rate }}%</span><span>{{ m.unchosen }}</span>
            </div>
          </div>
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />各月提交情况趋势</div>
            <span class="head-select">{{ scopeTags.months }}</span>
          </div>
          <div ref="monthChartRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />答辩结果统计</div>
            <span class="head-select">{{ scopeTags.defense }}</span>
          </div>
          <div class="donut-wrap">
            <div class="donut-chart">
              <div ref="defenseChartRef" class="chart-box donut-box" />
              <div class="donut-center">
                <b>{{ stats.defense_total }}</b><span>正式答辩已发布</span>
              </div>
            </div>
            <div class="donut-legend">
              <div v-for="d in defenseResults" :key="d.name" class="dl-item">
                <i :style="{ background: d.color }" />
                <span class="dl-name">{{ d.name }}</span>
                <span class="dl-val">{{ d.value }}（{{ pct(d.value, stats.defense_total) }}%）</span>
              </div>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, nextTick, computed, reactive, ref } from 'vue'
import * as echarts from 'echarts'
import { STAT_ICONS } from '@/components/statIcons'
import { dashboardApi } from '@/api/common'
import { scopeLabel, scopeLabelWithYear } from '@/utils/dashboardScope'
import type { DashboardScopeMeta, DashboardStats, PassCountRow } from '@/types/api'

interface NamedValue { name: string; value: number }
interface Analytics {
  cards: { student_count: number; teacher_count: number; topic_count: number;
           selected_count: number; completed_count: number; pending_count: number }
  colleges: NamedValue[]
  majors: { name: string; total: number; chosen: number; rate: number; unchosen: number }[]
  pass_rates: NamedValue[]
  topic_types: NamedValue[]
  months: string[]
  month_series: { name: string; data: number[] }[]
  defense_results: NamedValue[]
  defense_total: number
}
/** P8: analytics 响应的口径声明(scope/academic_year/scope_map) */
const analyticsMeta = ref<DashboardScopeMeta>({})
const stats = reactive<Analytics>({
  cards: { student_count: 0, teacher_count: 0, topic_count: 0, selected_count: 0,
           completed_count: 0, pending_count: 0 },
  colleges: [], majors: [], pass_rates: [], topic_types: [], months: [], month_series: [],
  defense_results: [], defense_total: 0,
})
const palette = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6', '#13c2c2']
/**
 * P8: 卡片口径不写死 —— /dashboard/analytics 的 scope_map 逐字段声明 current_year / all_years
 * (completed_count/selected_count 是当前学年, student_count/teacher_count/topic_count/pending_count 是全部学年)。
 */
const statCards = computed(() => [
  { label: '学生总数', value: stats.cards.student_count, icon: STAT_ICONS.users, color: '#2563eb', bg: '#d7f0fe',
    scope: scopeLabel(analyticsMeta.value, 'cards.student_count') },
  { label: '教师总数', value: stats.cards.teacher_count, icon: STAT_ICONS.person, color: '#22c55e', bg: '#ddfef0',
    scope: scopeLabel(analyticsMeta.value, 'cards.teacher_count') },
  { label: '题目总数', value: stats.cards.topic_count, icon: STAT_ICONS.doc, color: '#8b5cf6', bg: '#edeafd',
    scope: scopeLabel(analyticsMeta.value, 'cards.topic_count') },
  { label: '已选题人数', value: stats.cards.selected_count, icon: STAT_ICONS.case, color: '#f59e0b', bg: '#fef5ea',
    ring: pct(stats.cards.selected_count, stats.cards.student_count), ringColor: '#f59e0b',
    scope: scopeLabelWithYear(analyticsMeta.value, 'cards.selected_count') },
  { label: '已完成', value: stats.cards.completed_count, icon: STAT_ICONS.cap, color: '#ef4444', bg: '#fee7ed',
    ring: pct(stats.cards.completed_count, stats.cards.student_count), ringColor: '#ef4444',
    scope: scopeLabel(analyticsMeta.value, 'cards.completed_count') },
  { label: '待审核事项', value: stats.cards.pending_count, icon: STAT_ICONS.bars, color: '#3b82f6', bg: '#dcf9fe',
    scope: scopeLabel(analyticsMeta.value, 'cards.pending_count') },
])

/** P8: 六张图表的卡头角标同样读 scope_map, 后端放开/收窄字段时文案自动跟随 */
const scopeTags = computed(() => ({
  colleges: scopeLabel(analyticsMeta.value, 'colleges'),
  passRates: scopeLabel(analyticsMeta.value, 'pass_rates'),
  topicTypes: scopeLabel(analyticsMeta.value, 'topic_types'),
  majors: scopeLabel(analyticsMeta.value, 'majors'),
  months: scopeLabel(analyticsMeta.value, 'month_series'),
  defense: scopeLabel(analyticsMeta.value, 'defense_results'),
}))
/** P7-B: stats.pass_counts 每行 {name,value,total}, 通过率必须自己算; 无数据时回退 analytics.pass_rates(百分比) */
const passCounts = ref<PassCountRow[]>([])
const passRows = computed(() => (passCounts.value.length
  ? passCounts.value.map(row => ({
    name: row.name,
    value: row.total ? Math.round(row.value / row.total * 1000) / 10 : 0,
    approved: row.value,
    total: row.total,
  }))
  : stats.pass_rates.map(row => ({ name: row.name, value: row.value, approved: 0, total: 0 }))))
const stagePass = computed(() => passRows.value.map((s, i) => ({ ...s, color: palette[i % palette.length] })))
const topicTypes = computed(() => stats.topic_types.map((s, i) => ({ ...s, color: palette[i % palette.length] })))
const defenseResults = computed(() => stats.defense_results.map((s, i) => ({ ...s, color: palette[i % palette.length] })))
const majorRows = computed(() => stats.majors)
function pct(value: number, total: number) { return total ? Math.round(value / total * 100) : 0 }

const collegeChartRef = ref<HTMLElement>()
const passChartRef = ref<HTMLElement>()
const typeChartRef = ref<HTMLElement>()
const monthChartRef = ref<HTMLElement>()
const defenseChartRef = ref<HTMLElement>()
let charts: echarts.ECharts[] = []
function chartOn(el: HTMLElement | undefined) {
  if (!el) return null
  const chart = echarts.init(el)
  charts.push(chart)
  return chart
}
function initCharts() {
  chartOn(collegeChartRef.value)?.setOption({
    grid: { left: 46, right: 12, top: 30, bottom: 45 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: stats.colleges.map(c => c.name), axisLabel: { interval: 0, rotate: 20, fontSize: 10 } },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{ type: 'bar', barMaxWidth: 28, data: stats.colleges.map((c, i) => ({ value: c.value,
      itemStyle: { color: palette[i % palette.length], borderRadius: [4, 4, 0, 0] } })) }],
  })
  chartOn(passChartRef.value)?.setOption({
    grid: { left: 42, right: 15, top: 30, bottom: 30 }, tooltip: { trigger: 'axis',
      formatter: (params: any) => {
        const item = Array.isArray(params) ? params[0] : params
        const row = passRows.value[item?.dataIndex ?? 0]
        const detail = row && row.total ? ` (${row.approved}/${row.total})` : ''
        return `${item?.name ?? ''}<br/>通过率 ${item?.value ?? 0}%${detail}`
      } },
    xAxis: { type: 'category', data: passRows.value.map(s => s.name) },
    yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
    series: [{ type: 'line', smooth: true, data: passRows.value.map(s => s.value),
      itemStyle: { color: '#3b82f6' }, label: { show: true, position: 'top', formatter: '{c}%' } }],
  })
  chartOn(typeChartRef.value)?.setOption({
    tooltip: { trigger: 'item' },
    series: [{ type: 'pie', radius: ['56%', '80%'], label: { show: false },
      data: stats.topic_types.map((t, i) => ({ ...t, itemStyle: { color: palette[i % palette.length] } })) }],
  })
  chartOn(monthChartRef.value)?.setOption({
    grid: { left: 42, right: 12, top: 36, bottom: 28 }, tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 0, textStyle: { fontSize: 10 } },
    xAxis: { type: 'category', data: stats.months.map(m => m.slice(5) + '月') },
    yAxis: { type: 'value', minInterval: 1 },
    series: stats.month_series.map((s, i) => ({ name: s.name, type: 'line', smooth: true,
      data: s.data, itemStyle: { color: palette[i % palette.length] } })),
  })
  chartOn(defenseChartRef.value)?.setOption({
    tooltip: { trigger: 'item' },
    series: [{ type: 'pie', radius: ['56%', '80%'], label: { show: false },
      data: stats.defense_results.map((d, i) => ({ ...d, itemStyle: { color: palette[i % palette.length] } })) }],
  })
}
function resizeCharts() { charts.forEach(chart => chart.resize()) }
onMounted(async () => {
  const [response, distribution] = await Promise.all([dashboardApi.analytics(), dashboardApi.stats()])
  Object.assign(stats, response.data)
  analyticsMeta.value = (response.data || {}) as DashboardScopeMeta
  passCounts.value = (distribution.data as DashboardStats)?.pass_counts ?? []
  await nextTick()
  initCharts()
  window.addEventListener('resize', resizeCharts)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  charts.forEach(chart => chart.dispose())
  charts = []
})
</script>

<style scoped>
/* 筛选区 */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #fff;
  border-radius: 8px;
  padding: 12px 16px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  margin-bottom: 12px;
}
.filter-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-label {
  font-size: 13px;
  color: #5a6b80;
}
.filter-select {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  font-size: 12px;
  color: #3b4a5f;
  background: #fff;
  cursor: pointer;
  min-width: 88px;
  justify-content: space-between;
}
.filter-select .el-icon { font-size: 11px; color: #97a3b4; }
.filter-btn { height: 30px; padding: 0 18px; font-size: 12px; border-radius: 4px; }
.filter-btn.reset { border: 1px solid #dcdfe6; color: #5a6b80; }

/* 统计卡 */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}
.stat-card {
  position: relative;
  background: #fff;
  border-radius: 8px;
  padding: 14px 12px;
  display: flex;
  align-items: flex-start;
  gap: 11px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  min-height: 100px;
}
.stat-icon {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-icon svg { width: 22px; height: 22px; }
.stat-info { flex: 1; min-width: 0; }
.stat-num {
  font-size: 24px;
  font-weight: 700;
  color: #1f2d3d;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
  line-height: 1.15;
}
.stat-label {
  font-size: 12.5px;
  color: #8a97ad;
  margin-top: 2px;
}

/* P8: 口径角标(当前学年/全部学年), 由 scope_map 决定 */
.scope-tag {
  margin-left: 4px;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 10.5px;
  color: #6b7a90;
  background: #eef2f8;
}
.stat-trend {
  font-size: 11.5px;
  margin-top: 6px;
}
.stat-ring {
  --pct: 0;
  --ring-color: #f59e0b;
  position: absolute;
  right: 10px;
  top: 12px;
  width: 42px;
  height: 42px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(closest-side, #fff 72%, transparent 73% 100%),
    conic-gradient(var(--ring-color) calc(var(--pct) * 1%), #eef1f6 0);
}
.stat-ring span {
  font-size: 10px;
  font-weight: 600;
  color: #5a6b80;
}

/* 图表卡 */
.chart-row { margin-bottom: 12px; }
.chart-row :deep(.el-col) { min-width: 0; }
.chart-card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  padding: 14px 16px 10px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.head-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
}
.head-title i {
  width: 4px;
  height: 16px;
  background: #1878fd;
  border-radius: 2px;
}
.head-select {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  color: #5a6b80;
  border: 1px solid #e4e9f0;
  border-radius: 4px;
  padding: 2px 8px;
  cursor: default;
}
.head-select .el-icon { font-size: 10px; color: #97a3b4; }
.chart-box {
  width: 100%;
  height: 212px;
}
.lg-chart { height: 186px; }
.chart-body { position: relative; }
.stage-legend {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 0 2px 2px;
}
.stage-legend .lg-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: #5a6b80;
}
.stage-legend .lg-item i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

/* 环形图 */
.donut-wrap {
  display: flex;
  align-items: center;
  height: 212px;
}
.donut-chart {
  position: relative;
  width: 55%;
  height: 100%;
}
.donut-box { height: 100%; }
.donut-center {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  pointer-events: none;
}
.donut-center b {
  display: block;
  font-size: 20px;
  font-weight: 700;
  color: #1f2d3d;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
}
.donut-center span {
  font-size: 11px;
  color: #8a97a8;
}
.donut-legend {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.dl-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #5a6b80;
}
.dl-item i {
  width: 9px;
  height: 9px;
  border-radius: 2px;
  flex-shrink: 0;
}
.dl-name { color: #5a6b80; }
.dl-val { margin-left: auto; color: #1f2d3d; font-weight: 500; }

/* 专业选题表格 */
.major-table {
  font-size: 12px;
}
.mt-head {
  display: grid;
  grid-template-columns: 1.5fr 0.66fr 0.66fr 0.72fr 0.66fr;
  background: #f0f6ff;
  color: #3b4a5f;
  font-weight: 600;
  padding: 7px 10px;
  border-radius: 4px;
}
.mt-head span, .mt-row span { text-align: center; }
.mt-head .c-name, .mt-row .c-name { text-align: left; white-space: nowrap; }
.mt-row {
  display: grid;
  grid-template-columns: 1.5fr 0.66fr 0.66fr 0.72fr 0.66fr;
  padding: 7px 10px;
  color: #3b4a5f;
  border-bottom: 1px solid #f2f5fa;
}
.mt-row:last-child { border-bottom: none; }
.mt-row .rate { color: #17d09a; font-weight: 500; }
@media (max-width: 1450px) {
  .stat-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .stat-card:has(.stat-ring) .stat-info { padding-right: 48px; }
}
@media (max-width: 900px) {
  .chart-row :deep(.el-col) { max-width: 100%; flex: 0 0 100%; margin-bottom: 12px; }
  .major-table { overflow-x: auto; }
  .mt-head, .mt-row { min-width: 500px; }
}
@media (max-width: 600px) {
  .stat-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat-card { min-height: 90px; padding: 12px; }
  .stat-icon { width: 34px; height: 34px; }
  .stat-card:has(.stat-ring) .stat-info { padding-right: 0; }
  .stat-ring { width: 34px; height: 34px; right: 8px; bottom: 8px; top: auto; }
  .stat-ring span { font-size: 9px; }
  .donut-wrap { flex-direction: column; height: auto; }
  .donut-chart { width: 100%; height: 190px; }
  .donut-legend { width: 100%; padding-bottom: 10px; }
}
</style>
