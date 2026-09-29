<template>
  <div class="progress-page">
    <!-- 统计卡 -->
    <div class="stat-cards">
      <div v-for="c in statCards" :key="c.label" class="stat-card">
        <div class="stat-icon" :style="{ background: c.tileBg, color: c.iconColor }">
          <svg viewBox="0 0 24 24" fill="currentColor" v-html="c.icon" />
        </div>
        <div class="stat-info">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-num">{{ c.value }}</div>
          <div class="stat-trend"><span class="trend-prefix">较去年</span> <span :style="{ color: c.trendColor }">{{ c.trend }} {{ c.arrow }}</span></div>
        </div>
        <div v-if="c.ring !== undefined" class="stat-ring" :class="{ big: c.ringBig }" :style="{ '--pct': c.ring, '--ring-color': c.ringColor }">
          <span>{{ c.ring }}%</span>
        </div>
      </div>
    </div>

    <!-- 图表区：三卡并排 -->
    <el-row :gutter="12" class="chart-row">
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />各阶段完成情况</div>
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
          <div ref="stageChartRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />学生进度分布</div>
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
              <div ref="distChartRef" class="chart-box donut-box" />
              <div class="donut-center">
                <b>356</b><span>学生总数</span>
              </div>
            </div>
            <div class="donut-legend">
              <div v-for="d in dist" :key="d.name" class="dl-item">
                <i :style="{ background: d.color }" />
                <span class="dl-name">{{ d.name }}</span>
                <span class="dl-val">{{ d.value }}（{{ d.pct }}%）</span>
              </div>
            </div>
          </div>
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i />每月进度变化趋势</div>
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
          <div ref="trendChartRef" class="chart-box" />
        </div>
      </el-col>
    </el-row>

    <!-- 筛选区 -->
    <div class="filter-card">
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">学年</span>
          <el-select v-model="query.year" class="filter-select narrow">
            <el-option label="2024届" value="2024届" />
            <el-option label="2023届" value="2023届" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">学院</span>
          <el-select v-model="query.college" class="filter-select" placeholder="全部学院">
            <el-option label="全部学院" value="" />
            <el-option v-for="c in collegeOptions" :key="c" :label="c" :value="c" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">专业</span>
          <el-select v-model="query.major" class="filter-select" placeholder="全部专业">
            <el-option label="全部专业" value="" />
            <el-option v-for="m in majorOptions" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <div class="filter-item grow">
          <span class="filter-label">进度状态</span>
          <el-select v-model="query.status" class="filter-select" placeholder="全部状态">
            <el-option label="全部状态" value="" />
            <el-option label="正常推进" value="正常推进" />
            <el-option label="进度滞后" value="进度滞后" />
            <el-option label="严重滞后" value="严重滞后" />
            <el-option label="已完成" value="已完成" />
          </el-select>
        </div>
      </div>
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">学生姓名</span>
          <el-input v-model="query.student" placeholder="请输入学生姓名" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">学号</span>
          <el-input v-model="query.studentNo" placeholder="请输入学号" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">指导教师</span>
          <el-select v-model="query.teacher" class="filter-select" placeholder="全部教师">
            <el-option label="全部教师" value="" />
            <el-option v-for="t in teacherOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </div>
        <div class="filter-actions">
          <el-button type="primary" @click="onSearch"><el-icon><Search /></el-icon>&nbsp;查询</el-button>
          <el-button class="reset-btn" @click="onReset"><el-icon><Refresh /></el-icon>&nbsp;重置</el-button>
        </div>
      </div>
    </div>

    <!-- 表格卡：页签 + 工具按钮 + 表格 + 分页 -->
    <div class="table-card">
      <div class="tab-bar">
        <div class="tabs">
          <div
            v-for="t in tabs" :key="t.name"
            class="tab" :class="{ active: activeTab === t.name }"
            @click="activeTab = t.name"
          >{{ t.name }} ({{ t.count }})</div>
        </div>
        <div class="tab-actions">
          <el-button class="ghost-btn" @click="onExport"><el-icon><Download /></el-icon>&nbsp;导出数据</el-button>
          <el-button class="ghost-btn" @click="onBatchRemind"><el-icon><Promotion /></el-icon>&nbsp;批量提醒</el-button>
          <el-button class="ghost-btn" @click="onSendNotice"><el-icon><Bell /></el-icon>&nbsp;发送通知</el-button>
        </div>
      </div>

      <el-table :data="pagedRows" class="pg-table" @selection-change="onSelectionChange">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="index" label="序号" width="48" />
        <el-table-column prop="student" label="学生姓名" width="78" />
        <el-table-column prop="studentNo" label="学号" width="88" />
        <el-table-column prop="college" label="学院" width="100" />
        <el-table-column prop="major" label="专业" width="130" />
        <el-table-column prop="title" label="题目名称" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="topic-title">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="teacher" label="指导教师" width="80" />
        <el-table-column prop="stage" label="当前阶段" width="84" align="center" />
        <el-table-column label="进度" width="136">
          <template #default="{ row }">
            <div class="progress-cell">
              <div class="p-track"><div class="p-fill" :style="{ width: row.progress + '%', background: row.progressColor }" /></div>
              <span class="p-num">{{ row.progress }}%</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="进度状态" width="86" align="center">
          <template #default="{ row }">
            <span class="status-tag" :class="row.statusClass">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="updateTime" label="最后更新时间" width="106" />
        <el-table-column label="操作" width="104" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="viewDetail(row)">查看</el-button>
            <el-button link type="primary" size="small" @click="onRemind(row)">提醒</el-button>
            <el-dropdown trigger="hover" class="more-drop">
              <span class="more-dots">⋯</span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="onDelete(row)">删除记录</el-dropdown-item>
                  <el-dropdown-item @click="onRemark()">备注</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-bar">
        <span class="page-total">共 {{ total }} 条数据</span>
        <div class="pager-group">
          <el-select v-model="pageSize" size="small" class="page-size">
            <el-option label="10条/页" :value="10" />
            <el-option label="20条/页" :value="20" />
          </el-select>
          <el-pagination
            v-model:current-page="page" :page-size="pageSize" :total="total" :pager-count="7"
            background layout="prev, pager, next, jumper" class="pager"
          />
        </div>
      </div>
    </div>

    <!-- 查看详情 -->
    <el-dialog v-model="detailVisible" :title="detail?.title" width="620px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="学生姓名">{{ detail?.student }}</el-descriptions-item>
        <el-descriptions-item label="学号">{{ detail?.studentNo }}</el-descriptions-item>
        <el-descriptions-item label="学院">{{ detail?.college }}</el-descriptions-item>
        <el-descriptions-item label="专业">{{ detail?.major }}</el-descriptions-item>
        <el-descriptions-item label="指导教师">{{ detail?.teacher }}</el-descriptions-item>
        <el-descriptions-item label="当前阶段">{{ detail?.stage }}</el-descriptions-item>
        <el-descriptions-item label="进度状态">{{ detail?.status }}</el-descriptions-item>
        <el-descriptions-item label="最后更新时间">{{ detail?.updateTime }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, nextTick, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { STAT_ICONS } from '@/components/statIcons'

const CHECK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.2L6.4 11.8l1.5-1.5 2.9 2.9 5.3-5.3 1.5 1.5-6.8 6.8z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm.9-12.9h-1.8v5.2l4.4 2.6.9-1.5-3.5-2.1V7.1z"/>'
const EXCLAM_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1 5h2v8h-2V7zm0 10h2v2h-2v-2z"/>'
const FLAG_ICON = '<path d="M5 3v18h2v-7h11l-2.5-5L18 4H7V3H5zm2 3h8.2l-1.8 3 1.8 3H7V6z"/>'

/* 趋势色遵循设计稿：涨为红、跌为绿，"较去年"前缀灰 */
const statCards = [
  { label: '学生总数', value: 356, icon: STAT_ICONS.users, tileBg: '#d7f0fe', iconColor: '#2563eb', trend: '+12%', trendColor: '#ef4444', arrow: '↗' },
  { label: '正常推进', value: 198, icon: CHECK_ICON, tileBg: '#ddfef0', iconColor: '#22c55e', ring: 56, ringColor: '#22c55e', ringBig: true, trend: '+8%', trendColor: '#ef4444', arrow: '↗' },
  { label: '进度滞后', value: 96, icon: CLOCK_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b', ring: 27, ringColor: '#f59e0b', trend: '-5%', trendColor: '#22c55e', arrow: '↘' },
  { label: '严重滞后', value: 42, icon: EXCLAM_ICON, tileBg: '#fee7ed', iconColor: '#ef4444', ring: 12, ringColor: '#ef4444', trend: '+6%', trendColor: '#ef4444', arrow: '↗' },
  { label: '已完成', value: 20, icon: FLAG_ICON, tileBg: '#edeafd', iconColor: '#8b5cf6', ring: 6, ringColor: '#8b5cf6', trend: '+4%', trendColor: '#ef4444', arrow: '↗' },
]

const stageChart = [
  { name: '选题', value: 298, color: '#2e97fe' },
  { name: '开题报告', value: 242, color: '#22c55e' },
  { name: '中期答辩', value: 186, color: '#f59e0b' },
  { name: '终期答辩', value: 142, color: '#ef4444' },
  { name: '毕业材料', value: 98, color: '#8b5cf6' },
]

const dist = [
  { name: '正常', value: 198, pct: 56, color: '#2e97fe' },
  { name: '进度滞后', value: 96, pct: 27, color: '#f59e0b' },
  { name: '严重滞后', value: 42, pct: 12, color: '#ef4444' },
  { name: '已完成', value: 20, pct: 6, color: '#8b5cf6' },
]

const trendMonths = ['1月', '2月', '3月', '4月', '5月', '6月']
const trendSeries = [
  { name: '按时推进', color: '#2e97fe', data: [52, 58, 64, 71, 76, 82], area: true },
  { name: '进度滞后', color: '#f59e0b', data: [30, 28, 26, 24, 22, 20], area: false },
  { name: '严重滞后', color: '#ef4444', data: [15, 13, 12, 10, 9, 7], area: false },
]

const collegeOptions = ['计算机学院', '人工智能学院', '电子信息学院', '管理学院', '经济管理学院', '数学与统计学院']
const majorOptions = ['计算机科学与技术', '软件工程', '人工智能', '网络工程', '电子信息工程', '信息管理与信息系统', '数据科学与大数据技术', '信息安全', '应用统计学']
const teacherOptions = ['李老师', '王老师', '张老师', '陈老师', '刘老师', '黄老师', '林老师', '何老师']

const tabs = [
  { name: '全部', count: 356 },
  { name: '正常推进', count: 198 },
  { name: '进度滞后', count: 96 },
  { name: '严重滞后', count: 42 },
  { name: '已完成', count: 20 },
]
const activeTab = ref('全部')

const PROGRESS_COLOR: Record<string, string> = {
  正常推进: '#2e97fe',
  进度滞后: '#f59e0b',
  严重滞后: '#ef4444',
  已完成: '#22c55e',
}

interface PgRow {
  student: string
  studentNo: string
  college: string
  major: string
  title: string
  teacher: string
  stage: string
  progress: number
  status: string
  updateTime: string
}

const RAW_ROWS: PgRow[] = [
  { student: '张三', studentNo: '2021001', college: '计算机学院', major: '计算机科学与技术', title: '基于大模型的智能问答系统研究', teacher: '李老师', stage: '开题报告', progress: 70, status: '正常推进', updateTime: '2024-05-18' },
  { student: '李四', studentNo: '2021002', college: '计算机学院', major: '软件工程', title: '基于SpringBoot的校园二手交易平台设计', teacher: '王老师', stage: '中期答辩', progress: 45, status: '进度滞后', updateTime: '2024-05-17' },
  { student: '王五', studentNo: '2021003', college: '人工智能学院', major: '人工智能', title: '基于深度学习的图像识别算法研究', teacher: '张老师', stage: '开题报告', progress: 20, status: '严重滞后', updateTime: '2024-05-16' },
  { student: '赵六', studentNo: '2021004', college: '计算机学院', major: '软件工程', title: '面向高校的毕业设计管理系统设计', teacher: '陈老师', stage: '终期答辩', progress: 85, status: '正常推进', updateTime: '2024-05-16' },
  { student: '孙七', studentNo: '2021005', college: '计算机学院', major: '网络工程', title: '基于Vue3的在线学习平台前端设计', teacher: '刘老师', stage: '中期答辩', progress: 60, status: '进度滞后', updateTime: '2024-05-15' },
  { student: '周八', studentNo: '2021006', college: '电子信息学院', major: '电子信息工程', title: '基于物联网的智能家居系统设计', teacher: '黄老师', stage: '开题报告', progress: 30, status: '严重滞后', updateTime: '2024-05-15' },
  { student: '吴九', studentNo: '2021007', college: '管理学院', major: '信息管理与信息系统', title: '基于大数据的高校教学质量分析', teacher: '林老师', stage: '毕业材料', progress: 95, status: '已完成', updateTime: '2024-05-14' },
  { student: '郑十', studentNo: '2021008', college: '经济管理学院', major: '数据科学与大数据技术', title: '基于Python的金融数据分析与可视化', teacher: '何老师', stage: '中期答辩', progress: 55, status: '进度滞后', updateTime: '2024-05-14' },
]

const rows = ref<PgRow[]>([...RAW_ROWS])
const total = 356
const page = ref(1)
const pageSize = ref(10)
const selected = ref<PgRow[]>([])

const query = reactive({
  year: '2024届',
  college: '',
  major: '',
  status: '',
  student: '',
  studentNo: '',
  teacher: '',
})

const filtered = computed(() => {
  return rows.value.filter((r) => {
    if (activeTab.value !== '全部' && r.status !== activeTab.value) return false
    if (query.college && r.college !== query.college) return false
    if (query.major && r.major !== query.major) return false
    if (query.status && r.status !== query.status) return false
    if (query.student && !r.student.includes(query.student)) return false
    if (query.studentNo && !r.studentNo.includes(query.studentNo)) return false
    if (query.teacher && r.teacher !== query.teacher) return false
    return true
  })
})

const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const slice = filtered.value.slice(start, start + pageSize.value)
  return slice.map((r, i) => ({
    ...r,
    index: start + i + 1,
    progressColor: PROGRESS_COLOR[r.status] || '#2e97fe',
    statusClass: r.status === '正常推进' ? 's-normal' : r.status === '进度滞后' ? 's-pending' : r.status === '严重滞后' ? 's-failed' : 's-done',
  }))
})

const stageChartRef = ref<HTMLElement>()
const distChartRef = ref<HTMLElement>()
const trendChartRef = ref<HTMLElement>()
let charts: echarts.ECharts[] = []

function initCharts() {
  if (stageChartRef.value) {
    const chart = echarts.init(stageChartRef.value)
    charts.push(chart)
    chart.setOption({
      grid: { left: 40, right: 8, top: 18, bottom: 24 },
      xAxis: { type: 'category', data: stageChart.map((s) => s.name), axisLabel: { interval: 0, fontSize: 10, color: '#7b8794' }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e4e9f0' } } },
      yAxis: { type: 'value', max: 300, interval: 100, axisLabel: { color: '#7b8794' }, splitLine: { lineStyle: { color: '#eef2f8', type: 'dashed' } } },
      series: [{
        type: 'bar', barWidth: 20,
        data: stageChart.map((s) => ({
          value: s.value,
          itemStyle: { color: s.color, borderRadius: [4, 4, 0, 0] },
          label: { show: true, position: 'top', color: '#3b4a5f', fontWeight: 600, fontSize: 10 },
        })),
      }],
    })
  }
  if (distChartRef.value) {
    const chart = echarts.init(distChartRef.value)
    charts.push(chart)
    chart.setOption({
      series: [{
        type: 'pie', radius: ['54%', '78%'], center: ['50%', '50%'],
        label: { show: false }, labelLine: { show: false },
        data: dist.map((d) => ({ value: d.value, name: d.name, itemStyle: { color: d.color } })),
      }],
    })
  }
  if (trendChartRef.value) {
    const chart = echarts.init(trendChartRef.value)
    charts.push(chart)
    chart.setOption({
      grid: { left: 38, right: 10, top: 32, bottom: 24 },
      legend: { top: 2, right: 0, itemWidth: 14, itemHeight: 8, textStyle: { color: '#5a6b80', fontSize: 10 } },
      xAxis: { type: 'category', data: trendMonths, axisLabel: { color: '#7b8794', fontSize: 10 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e4e9f0' } } },
      yAxis: { type: 'value', max: 100, interval: 20, axisLabel: { formatter: '{value}%', color: '#7b8794' }, splitLine: { lineStyle: { color: '#eef2f8', type: 'dashed' } } },
      series: trendSeries.map((s) => ({
        name: s.name, type: 'line', smooth: true, symbol: 'circle', symbolSize: 4,
        lineStyle: { color: s.color, width: 2 }, itemStyle: { color: s.color },
        areaStyle: s.area ? { color: 'rgba(46,151,254,0.10)' } : undefined,
        label: s.area ? { show: true, position: 'top', formatter: '{c}%', color: '#3b4a5f', fontSize: 9 } : undefined,
        data: s.data,
      })),
    })
  }
}

function handleResize() {
  charts.forEach((c) => c.resize())
}

function onSearch() {
  page.value = 1
}

function onReset() {
  query.year = '2024届'
  query.college = ''
  query.major = ''
  query.status = ''
  query.student = ''
  query.studentNo = ''
  query.teacher = ''
  page.value = 1
}

function onSelectionChange(sel: PgRow[]) {
  selected.value = sel
}

const detailVisible = ref(false)
const detail = ref<PgRow | null>(null)

function viewDetail(row: PgRow) {
  detail.value = row
  detailVisible.value = true
}

function onRemind(row: PgRow) {
  ElMessage.success(`已向「${row.student}」发送进度提醒`)
}

function onBatchRemind() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选要提醒的学生')
    return
  }
  ElMessage.success(`已向 ${selected.value.length} 名学生批量发送提醒`)
}

function onSendNotice() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选要通知的学生')
    return
  }
  ElMessageBox.confirm(`确定向选中的 ${selected.value.length} 名学生发送通知吗？`, '发送通知', { type: 'info' })
    .then(() => ElMessage.success('通知已发送'))
    .catch(() => {})
}

function onDelete(row: PgRow) {
  rows.value = rows.value.filter((r) => r !== row)
  ElMessage.success('记录已删除')
}

function onRemark() {
  ElMessage.info('备注功能演示')
}

function onExport() {
  ElMessage.success('导出成功')
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
/* 统计卡（标签在数值上方） */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}
.stat-card {
  position: relative;
  background: #fff;
  border-radius: 8px;
  padding: 10px 10px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  min-height: 76px;
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
.stat-info { flex: 1; min-width: 0; padding-right: 30px; }
.stat-label {
  font-size: 12.5px;
  color: #8a97ad;
}
.stat-num {
  font-size: 24px;
  font-weight: 700;
  color: #1f2d3d;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
  line-height: 1.2;
  margin-top: 2px;
}
.stat-trend {
  font-size: 11.5px;
  margin-top: 4px;
}
.trend-prefix {
  color: #8a97ad;
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
.stat-ring.big { width: 48px; height: 48px; right: 8px; }
.stat-ring span {
  font-size: 9.5px;
  font-weight: 600;
  color: #2c3e5d;
}

/* 图表卡 */
.chart-row { margin-bottom: 12px; }
.chart-row :deep(.el-col) { min-width: 0; }
.chart-card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  padding: 8px 14px 4px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.head-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #1f2d3d;
}
.head-title i {
  width: 4px;
  height: 15px;
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
  height: 152px;
}
.donut-wrap {
  display: flex;
  align-items: center;
  height: 152px;
}
.donut-chart {
  position: relative;
  width: 56%;
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
  font-size: 18px;
  font-weight: 700;
  color: #1f2d3d;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
}
.donut-center span {
  font-size: 10.5px;
  color: #8a97a8;
}
.donut-legend {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 7px;
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
.dl-val { margin-left: auto; color: #1f2d3d; font-weight: 500; }

/* 筛选区 */
.filter-card {
  background: #fff;
  border-radius: 8px;
  padding: 10px 14px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  margin-bottom: 12px;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 14px;
}
.filter-row + .filter-row { margin-top: 6px; }
.filter-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-item.grow { flex: 1; min-width: 0; }
.filter-label {
  font-size: 13px;
  color: #3b4a5f;
  white-space: nowrap;
}
.filter-input { width: 170px; }
.filter-select { width: 128px; }
.filter-select.narrow { width: 100px; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }
.reset-btn { border: 1px solid #dcdfe6; color: #2c3e5d; }

/* 表格卡 */
.table-card {
  background: #fff;
  border-radius: 8px;
  padding: 4px 14px 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.tab-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #ebeef5;
  padding: 6px 0 0;
}
.tabs {
  display: flex;
  gap: 24px;
}
.tab {
  position: relative;
  font-size: 13.5px;
  color: #5a6b80;
  padding: 7px 2px 8px;
  cursor: pointer;
}
.tab.active {
  color: #1878fd;
  font-weight: 500;
}
.tab.active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background: #1878fd;
  border-radius: 1px;
}
.tab-actions { display: flex; gap: 8px; }
.ghost-btn { border: 1px solid #dcdfe6; color: #2c3e5d; }

.pg-table :deep(thead th) {
  background: #f0f6ff;
  color: #2c3e5d;
  font-weight: 600;
  font-size: 12px;
  padding: 6px 0;
}
.pg-table :deep(td) {
  font-size: 12px;
  color: #3b4a5f;
  padding: 4px 0;
}
.pg-table :deep(.cell) { padding: 0 6px; }
.pg-table :deep(.el-button + .el-button) { margin-left: 6px; }

.topic-title { color: #1f2d3d; font-weight: 500; }

/* 进度条 */
.progress-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}
.p-track {
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: #eef1f6;
  overflow: hidden;
}
.p-fill {
  height: 100%;
  border-radius: 4px;
}
.p-num {
  font-size: 11px;
  font-weight: 600;
  color: #2c3e5d;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
  white-space: nowrap;
}

.more-drop { vertical-align: middle; margin-left: 4px; }
.more-dots {
  color: #97a3b4;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
}

/* 状态标签 */
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.s-normal { color: #27ae60; background: #e4f7ec; }
.s-pending { color: #e67e22; background: #fdf1e3; }
.s-failed { color: #e74c3c; background: #fdeaea; }
.s-done { color: #27ae60; background: #e4f7ec; }

/* 分页 */
.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 4px 2px;
}
.pager-group {
  display: flex;
  align-items: center;
}
.page-total {
  font-size: 12px;
  color: #8a97a8;
  margin-right: 12px;
}
.page-size { width: 96px; margin-right: 12px; }
</style>
