<template>
  <div class="stats-page">
    <!-- 筛选区 -->
    <div class="filter-bar">
      <div class="filter-item">
        <span class="filter-label">统计年份</span>
        <el-dropdown trigger="click">
          <span class="filter-select">2024届 <el-icon><ArrowDown /></el-icon></span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item>2024届</el-dropdown-item>
              <el-dropdown-item>2023届</el-dropdown-item>
              <el-dropdown-item>2022届</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <div class="filter-item">
        <span class="filter-label">学院</span>
        <el-dropdown trigger="click">
          <span class="filter-select">全部学院 <el-icon><ArrowDown /></el-icon></span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item>全部学院</el-dropdown-item>
              <el-dropdown-item>计算机学院</el-dropdown-item>
              <el-dropdown-item>电子信息学院</el-dropdown-item>
              <el-dropdown-item>机械工程学院</el-dropdown-item>
              <el-dropdown-item>经济管理学院</el-dropdown-item>
              <el-dropdown-item>艺术设计学院</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <div class="filter-item">
        <span class="filter-label">专业</span>
        <el-dropdown trigger="click">
          <span class="filter-select">全部专业 <el-icon><ArrowDown /></el-icon></span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item>全部专业</el-dropdown-item>
              <el-dropdown-item>计算机科学与技术</el-dropdown-item>
              <el-dropdown-item>软件工程</el-dropdown-item>
              <el-dropdown-item>电子信息工程</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <el-button type="primary" class="filter-btn">查询</el-button>
      <el-button class="filter-btn reset">重置</el-button>
    </div>

    <!-- 统计卡 -->
    <div class="stat-cards">
      <div v-for="c in statCards" :key="c.label" class="stat-card">
        <div class="stat-icon" :style="{ background: c.bg, color: c.color }">
          <svg viewBox="0 0 24 24" fill="currentColor" v-html="c.icon" />
        </div>
        <div class="stat-info">
          <div class="stat-num">{{ c.value }}</div>
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-trend" :style="{ color: c.trendColor }">{{ c.trend }} {{ c.arrow }}</div>
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
            <el-dropdown trigger="click">
              <span class="head-select">2024届 <el-icon><ArrowDown /></el-icon></span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item>2024届</el-dropdown-item>
                  <el-dropdown-item>2023届</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <div ref="collegeChartRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />各阶段通过率统计</div>
            <el-dropdown trigger="click">
              <span class="head-select">2024届 <el-icon><ArrowDown /></el-icon></span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item>2024届</el-dropdown-item>
                  <el-dropdown-item>2023届</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
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
            <el-dropdown trigger="click">
              <span class="head-select">2024届 <el-icon><ArrowDown /></el-icon></span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item>2024届</el-dropdown-item>
                  <el-dropdown-item>2023届</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <div class="donut-wrap">
            <div class="donut-chart">
              <div ref="typeChartRef" class="chart-box donut-box" />
              <div class="donut-center">
                <b>320</b><span>题目总数</span>
              </div>
            </div>
            <div class="donut-legend">
              <div v-for="t in topicTypes" :key="t.name" class="dl-item">
                <i :style="{ background: t.color }" />
                <span class="dl-name">{{ t.name }}</span>
                <span class="dl-val">{{ t.value }}（{{ t.pct }}%）</span>
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
            <el-dropdown trigger="click">
              <span class="head-select">2024届 <el-icon><ArrowDown /></el-icon></span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item>2024届</el-dropdown-item>
                  <el-dropdown-item>2023届</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <div class="major-table">
            <div class="mt-head">
              <span class="c-name">专业名称</span><span>学生总数</span><span>已选题数</span><span>选题率</span><span>未选题数</span>
            </div>
            <div v-for="m in majorRows" :key="m.name" class="mt-row">
              <span class="c-name">{{ m.name }}</span><span>{{ m.total }}</span><span>{{ m.chosen }}</span>
              <span class="rate">{{ m.rate }}</span><span>{{ m.unchosen }}</span>
            </div>
          </div>
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />各月提交情况趋势</div>
            <el-dropdown trigger="click">
              <span class="head-select">2024届 <el-icon><ArrowDown /></el-icon></span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item>2024届</el-dropdown-item>
                  <el-dropdown-item>2023届</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <div ref="monthChartRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />答辩结果统计</div>
            <el-dropdown trigger="click">
              <span class="head-select">2024届 <el-icon><ArrowDown /></el-icon></span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item>2024届</el-dropdown-item>
                  <el-dropdown-item>2023届</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <div class="donut-wrap">
            <div class="donut-chart">
              <div ref="defenseChartRef" class="chart-box donut-box" />
              <div class="donut-center">
                <b>186</b><span>已完成答辩</span>
              </div>
            </div>
            <div class="donut-legend">
              <div v-for="d in defenseResults" :key="d.name" class="dl-item">
                <i :style="{ background: d.color }" />
                <span class="dl-name">{{ d.name }}</span>
                <span class="dl-val">{{ d.value }}（{{ d.pct }}%）</span>
              </div>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, nextTick, ref } from 'vue'
import * as echarts from 'echarts'
import { STAT_ICONS } from '@/components/statIcons'

const collegeChartRef = ref<HTMLElement>()
const passChartRef = ref<HTMLElement>()
const typeChartRef = ref<HTMLElement>()
const monthChartRef = ref<HTMLElement>()
const defenseChartRef = ref<HTMLElement>()
let charts: echarts.ECharts[] = []

const statCards = [
  { label: '学生总数', value: 356, icon: STAT_ICONS.users, color: '#2563eb', bg: '#d7f0fe', trend: '较去年 +12%', trendColor: '#1878fd', arrow: '↗' },
  { label: '教师总数', value: 56, icon: STAT_ICONS.person, color: '#22c55e', bg: '#ddfef0', trend: '较去年 +8%', trendColor: '#1878fd', arrow: '↗' },
  { label: '题目总数', value: 320, icon: STAT_ICONS.doc, color: '#8b5cf6', bg: '#edeafd', trend: '较去年 +15%', trendColor: '#1878fd', arrow: '↗' },
  { label: '已选题人数', value: 298, icon: STAT_ICONS.case, color: '#f59e0b', bg: '#fef5ea', trend: '较去年 +10%', trendColor: '#f59e0b', arrow: '↗', ring: 84, ringColor: '#f59e0b' },
  { label: '已完成答辩', value: 186, icon: STAT_ICONS.cap, color: '#ef4444', bg: '#fee7ed', trend: '较去年 +18%', trendColor: '#ef4444', arrow: '↑', ring: 52, ringColor: '#ef4444' },
  { label: '待审核事项', value: 24, icon: STAT_ICONS.bars, color: '#3b82f6', bg: '#dcf9fe', trend: '较上周 +6', trendColor: '#ef4444', arrow: '↑' },
]

const stagePass = [
  { name: '选题', value: 95, color: '#3b82f6' },
  { name: '开题', value: 86, color: '#22c55e' },
  { name: '中期', value: 71, color: '#f59e0b' },
  { name: '终期', value: 58, color: '#ef4444' },
  { name: '毕业材料', value: 52, color: '#8b5cf6' },
]

const topicTypes = [
  { name: '软件开发类', value: 120, pct: 37, color: '#2e97fe' },
  { name: '硬件设计类', value: 68, pct: 21, color: '#9c5ef5' },
  { name: '算法研究类', value: 56, pct: 18, color: '#1ed4a6' },
  { name: '应用研究类', value: 48, pct: 15, color: '#fe6940' },
  { name: '其他', value: 28, pct: 9, color: '#f0a050' },
]

const defenseResults = [
  { name: '优秀', value: 42, pct: 23, color: '#3b82f6' },
  { name: '良好', value: 68, pct: 37, color: '#22c55e' },
  { name: '中等', value: 52, pct: 28, color: '#f59e0b' },
  { name: '及格', value: 18, pct: 10, color: '#f56c6c' },
  { name: '不及格', value: 6, pct: 3, color: '#d6303a' },
]

const majorRows = [
  { name: '计算机科学与技术', total: 98, chosen: 92, rate: '93.9%', unchosen: 6 },
  { name: '软件工程', total: 86, chosen: 78, rate: '90.7%', unchosen: 8 },
  { name: '电子信息工程', total: 76, chosen: 64, rate: '84.2%', unchosen: 12 },
  { name: '机械设计制造及其自动化', total: 58, chosen: 46, rate: '79.3%', unchosen: 12 },
  { name: '会计学', total: 38, chosen: 28, rate: '73.7%', unchosen: 10 },
  { name: '视觉传达设计', total: 32, chosen: 26, rate: '81.3%', unchosen: 6 },
]

const colleges = [
  { name: '计算机学院', value: 98, color: '#3b82f6' },
  { name: '电子信息学院', value: 76, color: '#22c55e' },
  { name: '机械工程学院', value: 68, color: '#f59e0b' },
  { name: '经济管理学院', value: 58, color: '#ef4444' },
  { name: '艺术设计学院', value: 56, color: '#8b5cf6' },
]

const months = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月']
const monthSeries = [
  { name: '开题报告', color: '#3b82f6', data: [22, 44, 73, 66, 122, 83, 52, 62] },
  { name: '中期报告', color: '#22c55e', data: [15, 31, 45, 52, 76, 55, 39, 41] },
  { name: '终期报告', color: '#f59e0b', data: [13, 22, 30, 46, 64, 44, 31, 29] },
  { name: '毕业材料', color: '#ef4444', data: [8, 12, 18, 31, 45, 26, 18, 15] },
]

function initCharts() {
  if (collegeChartRef.value) {
    const chart = echarts.init(collegeChartRef.value)
    charts.push(chart)
    chart.setOption({
      grid: { left: 42, right: 10, top: 20, bottom: 28 },
      xAxis: { type: 'category', data: colleges.map((c) => c.name), axisLabel: { interval: 0, fontSize: 10, color: '#7b8794' }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e4e9f0' } } },
      yAxis: { type: 'value', max: 120, interval: 20, name: '人数', nameTextStyle: { color: '#7b8794', fontSize: 10, align: 'right' }, axisLabel: { color: '#7b8794' }, splitLine: { lineStyle: { color: '#eef2f8' } } },
      series: [{
        type: 'bar', barWidth: 22,
        data: colleges.map((c) => ({
          value: c.value,
          itemStyle: { color: c.color, borderRadius: [4, 4, 0, 0] },
          label: { show: true, position: 'top', color: '#5a6b80', fontSize: 10 },
        })),
      }],
    })
  }
  if (passChartRef.value) {
    const chart = echarts.init(passChartRef.value)
    charts.push(chart)
    chart.setOption({
      grid: { left: 38, right: 14, top: 26, bottom: 26 },
      xAxis: { type: 'category', data: stagePass.map((s) => s.name), axisLabel: { color: '#7b8794', fontSize: 11 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e4e9f0' } } },
      yAxis: { type: 'value', max: 100, interval: 20, axisLabel: { formatter: '{value}%', color: '#7b8794' }, splitLine: { lineStyle: { color: '#eef2f8' } } },
      series: [{
        type: 'line', smooth: true, symbol: 'circle', symbolSize: 8,
        lineStyle: { color: '#93b8f5', width: 3 },
        areaStyle: { color: 'rgba(59,130,246,0.08)' },
        data: stagePass.map((s) => ({
          value: s.value,
          itemStyle: { color: s.color, borderColor: '#fff', borderWidth: 2 },
          label: { show: true, position: 'top', formatter: '{c}%', color: '#5a6b80', fontSize: 10 },
        })),
      }],
    })
  }
  if (typeChartRef.value) {
    const chart = echarts.init(typeChartRef.value)
    charts.push(chart)
    chart.setOption({
      series: [{
        type: 'pie', radius: ['56%', '80%'], center: ['50%', '50%'],
        label: { show: false }, labelLine: { show: false },
        data: topicTypes.map((t) => ({ value: t.value, name: t.name, itemStyle: { color: t.color } })),
      }],
    })
  }
  if (monthChartRef.value) {
    const chart = echarts.init(monthChartRef.value)
    charts.push(chart)
    chart.setOption({
      grid: { left: 42, right: 12, top: 36, bottom: 26 },
      legend: { top: 2, right: 0, itemWidth: 14, itemHeight: 8, textStyle: { color: '#5a6b80', fontSize: 10 } },
      xAxis: { type: 'category', data: months, axisLabel: { color: '#7b8794', fontSize: 10 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e4e9f0' } } },
      yAxis: { type: 'value', max: 150, interval: 30, name: '提交数量', nameTextStyle: { color: '#7b8794', fontSize: 10, align: 'right' }, axisLabel: { color: '#7b8794' }, splitLine: { lineStyle: { color: '#eef2f8' } } },
      series: monthSeries.map((s) => ({
        name: s.name, type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
        lineStyle: { color: s.color, width: 2 }, itemStyle: { color: s.color }, data: s.data,
      })),
    })
  }
  if (defenseChartRef.value) {
    const chart = echarts.init(defenseChartRef.value)
    charts.push(chart)
    chart.setOption({
      series: [{
        type: 'pie', radius: ['56%', '80%'], center: ['50%', '50%'],
        label: { show: false }, labelLine: { show: false },
        data: defenseResults.map((d) => ({ value: d.value, name: d.name, itemStyle: { color: d.color } })),
      }],
    })
  }
}

function handleResize() {
  charts.forEach((c) => c.resize())
}

onMounted(async () => {
  await nextTick()
  initCharts()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  charts.forEach((c) => c.dispose())
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
  cursor: pointer;
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
</style>
