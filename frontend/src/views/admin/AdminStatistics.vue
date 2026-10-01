<template>
  <div class="stats-page">
    <!-- 筛选区 -->
    <div class="filter-bar"><strong>数据统计</strong><span class="filter-label">全部学年 · 实时业务数据</span></div>

    <!-- 统计卡 -->
    <div class="stat-cards">
      <div v-for="c in statCards" :key="c.label" class="stat-card">
        <div class="stat-icon" :style="{ background: c.bg, color: c.color }">
          <svg viewBox="0 0 24 24" fill="currentColor" v-html="c.icon" />
        </div>
        <div class="stat-info">
          <div class="stat-num">{{ c.value }}</div>
          <div class="stat-label">{{ c.label }}</div>

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
            <span class="head-select">全部学年</span>
          </div>
          <div ref="collegeChartRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />各阶段通过率统计</div>
            <span class="head-select">全部学年</span>
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
            <span class="head-select">全部学年</span>
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
            <span class="head-select">全部学年</span>
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
            <span class="head-select">全部学年</span>
          </div>
          <div ref="monthChartRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />答辩结果统计</div>
            <span class="head-select">全部学年</span>
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
const stats = reactive<Analytics>({
  cards: { student_count: 0, teacher_count: 0, topic_count: 0, selected_count: 0,
           completed_count: 0, pending_count: 0 },
  colleges: [], majors: [], pass_rates: [], topic_types: [], months: [], month_series: [],
  defense_results: [], defense_total: 0,
})
const palette = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6', '#13c2c2']
const statCards = computed(() => [
  { label: '学生总数', value: stats.cards.student_count, icon: STAT_ICONS.users, color: '#2563eb', bg: '#d7f0fe' },
  { label: '教师总数', value: stats.cards.teacher_count, icon: STAT_ICONS.person, color: '#22c55e', bg: '#ddfef0' },
  { label: '题目总数', value: stats.cards.topic_count, icon: STAT_ICONS.doc, color: '#8b5cf6', bg: '#edeafd' },
  { label: '已选题人数', value: stats.cards.selected_count, icon: STAT_ICONS.case, color: '#f59e0b', bg: '#fef5ea',
    ring: pct(stats.cards.selected_count, stats.cards.student_count), ringColor: '#f59e0b' },
  { label: '材料审核完成', value: stats.cards.completed_count, icon: STAT_ICONS.cap, color: '#ef4444', bg: '#fee7ed',
    ring: pct(stats.cards.completed_count, stats.cards.student_count), ringColor: '#ef4444' },
  { label: '待审核事项', value: stats.cards.pending_count, icon: STAT_ICONS.bars, color: '#3b82f6', bg: '#dcf9fe' },
])
const stagePass = computed(() => stats.pass_rates.map((s, i) => ({ ...s, color: palette[i % palette.length] })))
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
    grid: { left: 42, right: 15, top: 30, bottom: 30 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: stats.pass_rates.map(s => s.name) },
    yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
    series: [{ type: 'line', smooth: true, data: stats.pass_rates.map(s => s.value),
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
  const response = await dashboardApi.analytics()
  Object.assign(stats, response.data)
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
