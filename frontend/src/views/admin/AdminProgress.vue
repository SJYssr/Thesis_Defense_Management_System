<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage, ElMessageBox } from 'element-plus'
import { progressApi } from '@/api/business'
import { materialApi } from '@/api/business'
import { STAT_ICONS } from '@/components/statIcons'
import type { OverdueScanResult } from '@/types/api'

interface Option { id: number; name: string; college_id?: number }
interface ProgressRow { student_id: number; student_name: string; student_no: string; college_name: string | null; major_name: string | null; topic_title: string | null; teacher_name: string | null; current_stage: string; current_stage_cn: string; progress_percent: number; risk_level: string; risk_reason: string | null; updated_at: string | null; academic_year: string | null }
const loading = ref(false)
const rows = ref<ProgressRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const selected = ref<ProgressRow[]>([])
const summary = ref<any>({ student_total: 0, process_total: 0, not_started: 0, normal: 0, warning: 0, high: 0, completed: 0, stages: [], trend: [] })
const filters = ref<{ years: string[]; colleges: Option[]; majors: Option[]; teachers: Option[] }>({ years: [], colleges: [], majors: [], teachers: [] })
const query = reactive({ year: '', collegeId: undefined as number | undefined, majorId: undefined as number | undefined,
  teacherId: undefined as number | undefined, student: '', status: '' })
const applied = ref({ ...query })
const majorOptions = computed(() => filters.value.majors.filter(m => !query.collegeId || m.college_id === query.collegeId))
const cards = computed(() => [
  { label: '学生总数', value: summary.value.student_total, icon: STAT_ICONS.users, color: '#2563eb', bg: '#d7f0fe' },
  { label: '正常推进', value: summary.value.normal, icon: STAT_ICONS.case, color: '#22c55e', bg: '#ddfef0' },
  { label: '进度滞后', value: summary.value.warning, icon: STAT_ICONS.bars, color: '#f59e0b', bg: '#fef5ea' },
  { label: '严重滞后', value: summary.value.high, icon: STAT_ICONS.doc, color: '#ef4444', bg: '#fee7ed' },
  { label: '已完成', value: summary.value.completed, icon: STAT_ICONS.cap, color: '#8b5cf6', bg: '#edeafd' },
])
const tabs = computed(() => [
  { label: '全部', value: '', count: summary.value.process_total },
  { label: '正常推进', value: 'NORMAL', count: summary.value.normal },
  { label: '进度滞后', value: 'WARNING', count: summary.value.warning },
  { label: '严重滞后', value: 'HIGH', count: summary.value.high },
  { label: '已完成', value: 'COMPLETED', count: summary.value.completed },
])
const distribution = computed(() => [
  { name: '正常', value: summary.value.normal, color: '#2e97fe' },
  { name: '进度滞后', value: summary.value.warning, color: '#f59e0b' },
  { name: '严重滞后', value: summary.value.high, color: '#ef4444' },
  { name: '已完成', value: summary.value.completed, color: '#8b5cf6' },
  { name: '未开始', value: summary.value.not_started, color: '#b8c4d4' },
])
const detail = ref<ProgressRow | null>(null)
const detailVisible = ref(false)
const remindVisible = ref(false)
const remindIds = ref<number[]>([])
const remindForm = reactive({ title: '毕业设计进度提醒', content: '' })
const sending = ref(false)
const stageChartRef = ref<HTMLElement>()
const distChartRef = ref<HTMLElement>()
const trendChartRef = ref<HTMLElement>()
let charts: echarts.ECharts[] = []
function drawCharts() {
  charts.forEach(chart => chart.dispose()); charts = []
  if (stageChartRef.value) {
    const chart = echarts.init(stageChartRef.value); charts.push(chart)
    chart.setOption({ grid: { left: 38, right: 10, top: 22, bottom: 38 }, tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: summary.value.stages.map((s: any) => s.name), axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{ type: 'bar', barMaxWidth: 24, data: summary.value.stages.map((s: any, i: number) => ({
        value: s.value, itemStyle: { color: ['#2e97fe', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6'][i], borderRadius: [4, 4, 0, 0] } })) }] })
  }
  if (distChartRef.value) {
    const chart = echarts.init(distChartRef.value); charts.push(chart)
    chart.setOption({ tooltip: { trigger: 'item' }, series: [{ type: 'pie', radius: ['55%', '78%'],
      label: { show: false }, data: distribution.value.map(d => ({ name: d.name, value: d.value, itemStyle: { color: d.color } })) }] })
  }
  if (trendChartRef.value) {
    const chart = echarts.init(trendChartRef.value); charts.push(chart)
    chart.setOption({ grid: { left: 35, right: 10, top: 22, bottom: 38 }, tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: summary.value.trend.map((d: any) => d.date.slice(5)), axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{ type: 'line', smooth: true, data: summary.value.trend.map((d: any) => d.value),
        itemStyle: { color: '#2e97fe' }, areaStyle: { color: 'rgba(46,151,254,.12)' } }] })
  }
}
function params(extra: Record<string, unknown> = {}) {
  return { page: page.value, page_size: pageSize.value, academic_year: applied.value.year || undefined,
    college_id: applied.value.collegeId, major_id: applied.value.majorId, teacher_id: applied.value.teacherId,
    student: applied.value.student || undefined, risk_level: applied.value.status || undefined, ...extra }
}
async function load() {
  loading.value = true
  try { const res = await progressApi.adminList(params()); rows.value = res.data.items || []; total.value = res.data.total || 0 }
  finally { loading.value = false }
}
async function loadSummary() {
  // P7-B: 不传 academic_year 时后端按当前学年(academic_year_for)统计, 不再全学年累计
  const res = await progressApi.adminSummary(query.year || undefined)
  summary.value = res.data
  await nextTick(); drawCharts()
}
async function search() { applied.value = { ...query }; page.value = 1; await Promise.all([load(), loadSummary()]) }

/** P7-B: 逾期扫描; celery 入队路径 scanned=0, 不能对用户说「扫描 0 条」 */
const scanning = ref(false)
async function runOverdueScan() {
  await ElMessageBox.confirm('将扫描当前学年的全部毕业流程，把逾期节点标记为滞后并生成提醒任务。', '逾期扫描',
    { confirmButtonText: '开始扫描', cancelButtonText: '取消', type: 'warning' })
  scanning.value = true
  try {
    const res = await progressApi.overdueScan()
    const data = (res.data || {}) as OverdueScanResult
    if (data.mode === 'celery') {
      ElMessage.success(`已提交后台扫描${data.queued ? '（任务已入队）' : ''}，完成后分布会自动更新`)
    } else {
      ElMessage.success(`扫描完成：处理 ${data.scanned ?? 0} 条流程，分 ${data.batches ?? 0} 批`)
    }
    await Promise.all([load(), loadSummary()])
  } finally { scanning.value = false }
}
async function reset() { Object.assign(query, { year: filters.value.years[0] || '', collegeId: undefined, majorId: undefined,
  teacherId: undefined, student: '', status: '' }); await search() }
async function chooseTab(value: string) { query.status = value; await search() }
watch([page, pageSize], () => { load() })
watch(() => query.collegeId, () => { query.majorId = undefined })
function statusLabel(row: ProgressRow) { return row.current_stage === 'COMPLETED' ? '已完成' :
  row.risk_level === 'HIGH' ? '严重滞后' : row.risk_level === 'WARNING' ? '进度滞后' : '正常推进' }
function statusClass(row: ProgressRow) { return row.current_stage === 'COMPLETED' ? 's-done' :
  row.risk_level === 'HIGH' ? 's-failed' : row.risk_level === 'WARNING' ? 's-pending' : 's-normal' }
function selectMobile(row: ProgressRow, checked: boolean) { selected.value = checked ?
  [...selected.value.filter(item => item.student_id !== row.student_id), row] :
  selected.value.filter(item => item.student_id !== row.student_id) }
function openRemind(ids: number[]) {
  if (!ids.length) { ElMessage.warning('请先勾选要提醒的学生'); return }
  remindIds.value = [...new Set(ids)]; remindForm.content = ''; remindVisible.value = true
}
async function sendRemind() {
  if (!remindForm.content.trim()) { ElMessage.warning('请填写提醒内容'); return }
  await ElMessageBox.confirm(`确认向 ${remindIds.value.length} 名学生发送提醒？`, '发送进度提醒', { type: 'warning' })
  sending.value = true
  try { await progressApi.adminRemind({ student_ids: remindIds.value, title: remindForm.title, content: remindForm.content })
    ElMessage.success('提醒已发送'); remindVisible.value = false; selected.value = [] }
  finally { sending.value = false }
}
function csvCell(value: unknown) { return `"${String(value ?? '').replaceAll('"', '""')}"` }
async function exportCsv() {
  const first = await progressApi.adminList(params({ page: 1, page_size: 100 }))
  const all: ProgressRow[] = [...(first.data.items || [])]
  for (let p = 2; p <= Math.ceil((first.data.total || 0) / 100); p++) {
    const res = await progressApi.adminList(params({ page: p, page_size: 100 })); all.push(...(res.data.items || []))
  }
  const lines = [['学年', '学生姓名', '学号', '学院', '专业', '题目', '指导教师', '当前阶段', '进度', '风险', '更新时间'].join(',')]
  for (const row of all) lines.push([row.academic_year, row.student_name, row.student_no, row.college_name,
    row.major_name, row.topic_title, row.teacher_name, row.current_stage_cn, `${row.progress_percent}%`,
    statusLabel(row), row.updated_at].map(csvCell).join(','))
  const url = URL.createObjectURL(new Blob(['\uFEFF', lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a'); a.href = url; a.download = '毕业设计进度.csv'; a.click(); URL.revokeObjectURL(url)
}
onMounted(async () => { try { const res = await materialApi.adminFilters(); filters.value = res.data
  query.year = filters.value.years[0] || ''; applied.value = { ...query }
  await Promise.all([load(), loadSummary()]) } catch { /* 请求层展示错误 */ }
  window.addEventListener('resize', drawCharts) })
onBeforeUnmount(() => { window.removeEventListener('resize', drawCharts); charts.forEach(chart => chart.dispose()); charts = [] })
</script>

<template>
  <div class="progress-page">
    <div class="stat-cards"><div v-for="card in cards" :key="card.label" class="stat-card"><div class="stat-icon" :style="{ background: card.bg, color: card.color }"><svg viewBox="0 0 24 24" fill="currentColor" v-html="card.icon" /></div><div><div class="stat-label">{{ card.label }}</div><div class="stat-num">{{ card.value }}</div></div></div></div>
    <div class="chart-grid">
      <section class="chart-card"><h3>各阶段完成情况</h3><div ref="stageChartRef" class="chart-box" /></section>
      <section class="chart-card"><h3>学生进度分布</h3><div class="donut-wrap"><div class="donut-box"><div ref="distChartRef" class="chart-box" /><div class="donut-center"><strong>{{ summary.student_total }}</strong><small>学生总数</small></div></div><div class="donut-legend"><div v-for="d in distribution" :key="d.name" class="legend-row"><i :style="{ background: d.color }" /><span>{{ d.name }}</span><b>{{ d.value }}</b></div></div></div></section>
      <section class="chart-card"><h3>近 7 天流程更新</h3><div ref="trendChartRef" class="chart-box" /></section>
    </div>
    <div class="filter-card"><div class="filter-grid">
      <label>学年<el-select v-model="query.year" clearable placeholder="当前学年"><el-option v-for="year in filters.years" :key="year" :label="year" :value="year" /></el-select></label>
      <label>学院<el-select v-model="query.collegeId" clearable placeholder="全部学院"><el-option v-for="c in filters.colleges" :key="c.id" :label="c.name" :value="c.id" /></el-select></label>
      <label>专业<el-select v-model="query.majorId" clearable placeholder="全部专业"><el-option v-for="m in majorOptions" :key="m.id" :label="m.name" :value="m.id" /></el-select></label>
      <label>进度状态<el-select v-model="query.status" clearable placeholder="全部状态"><el-option v-for="tab in tabs.slice(1)" :key="tab.value" :label="tab.label" :value="tab.value" /></el-select></label>
      <label>学生姓名 / 学号<el-input v-model="query.student" clearable placeholder="输入姓名或学号" /></label>
      <label>指导教师<el-select v-model="query.teacherId" clearable placeholder="全部教师"><el-option v-for="t in filters.teachers" :key="t.id" :label="t.name" :value="t.id" /></el-select></label>
    </div><div class="filter-actions"><el-button type="primary" @click="search">查询</el-button><el-button @click="reset">重置</el-button></div></div>
    <div class="table-card"><div class="tab-bar"><div class="tabs"><button v-for="tab in tabs" :key="tab.label" class="tab" :class="{ active: applied.status === tab.value }" @click="chooseTab(tab.value)">{{ tab.label }}（{{ tab.count }}）</button></div><div class="tab-actions"><el-button :loading="scanning" @click="runOverdueScan">逾期扫描</el-button><el-button @click="exportCsv">导出数据</el-button><el-button @click="openRemind(selected.map(row => row.student_id))">批量提醒</el-button></div></div>
      <p class="scope-note">{{ query.year || '当前学年' }} · 已建立流程 {{ summary.process_total }} 条，未开始 {{ summary.not_started }} 人。未开始的学生尚无流程记录。</p>
      <el-table v-loading="loading" :data="rows" class="pg-table" empty-text="暂无符合条件的流程记录" @selection-change="(value: ProgressRow[]) => selected = value">
        <el-table-column type="selection" width="42" /><el-table-column type="index" label="序号" width="55" :index="(i: number) => (page - 1) * pageSize + i + 1" />
        <el-table-column prop="student_name" label="学生姓名" min-width="90" /><el-table-column prop="student_no" label="学号" min-width="105" /><el-table-column prop="college_name" label="学院" min-width="105" /><el-table-column prop="major_name" label="专业" min-width="125" />
        <el-table-column prop="topic_title" label="题目名称" min-width="175" show-overflow-tooltip /><el-table-column prop="teacher_name" label="指导教师" min-width="90" /><el-table-column prop="current_stage_cn" label="当前阶段" min-width="100" />
        <el-table-column label="进度" width="130"><template #default="{ row }"><el-progress :percentage="row.progress_percent || 0" :stroke-width="8" /></template></el-table-column>
        <el-table-column label="进度状态" min-width="100"><template #default="{ row }"><span class="status-tag" :class="statusClass(row)">{{ statusLabel(row) }}</span></template></el-table-column>
        <el-table-column label="更新时间" min-width="140"><template #default="{ row }">{{ row.updated_at?.replace('T', ' ').slice(0, 16) || '—' }}</template></el-table-column>
        <el-table-column label="操作" width="125" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="detail = row; detailVisible = true">查看</el-button><el-button link type="primary" @click="openRemind([row.student_id])">提醒</el-button></template></el-table-column>
      </el-table>
      <div class="progress-mobile-list"><div v-if="!rows.length" class="mobile-empty">暂无符合条件的流程记录</div><article v-for="row in rows" :key="row.student_id" class="progress-mobile-item"><div class="mobile-top"><el-checkbox :model-value="selected.some(item => item.student_id === row.student_id)" @change="(checked: string | number | boolean) => selectMobile(row, Boolean(checked))" /><strong>{{ row.student_name }} <small>{{ row.student_no }}</small></strong><span class="status-tag" :class="statusClass(row)">{{ statusLabel(row) }}</span></div><p>{{ row.topic_title || '尚未确定题目' }}</p><div class="mobile-meta">{{ row.current_stage_cn }} · {{ row.progress_percent }}% · {{ row.teacher_name || '待分配导师' }}</div><div class="mobile-actions"><el-button link type="primary" @click="detail = row; detailVisible = true">查看</el-button><el-button link type="primary" @click="openRemind([row.student_id])">提醒</el-button></div></article></div>
      <div class="pagination-bar"><span>共 {{ total }} 条流程记录</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10, 20, 50]" layout="sizes, prev, pager, next" /></div>
    </div>
    <el-dialog v-model="detailVisible" :title="`${detail?.student_name || '学生'}的毕设进度`" width="620px"><el-descriptions :column="1" border><el-descriptions-item label="学号">{{ detail?.student_no }}</el-descriptions-item><el-descriptions-item label="题目">{{ detail?.topic_title || '—' }}</el-descriptions-item><el-descriptions-item label="指导教师">{{ detail?.teacher_name || '—' }}</el-descriptions-item><el-descriptions-item label="阶段">{{ detail?.current_stage_cn }}</el-descriptions-item><el-descriptions-item label="进度">{{ detail?.progress_percent }}%</el-descriptions-item><el-descriptions-item label="风险原因">{{ detail?.risk_reason || '无' }}</el-descriptions-item></el-descriptions></el-dialog>
    <el-dialog v-model="remindVisible" :title="`向 ${remindIds.length} 名学生发送提醒`" width="520px"><el-input v-model="remindForm.title" maxlength="255" placeholder="提醒标题" /><el-input v-model="remindForm.content" type="textarea" :rows="4" maxlength="2000" placeholder="提醒内容" class="remind-content" /><template #footer><el-button @click="remindVisible = false">取消</el-button><el-button type="primary" :loading="sending" @click="sendRemind">发送提醒</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.progress-page { min-width:0; }.stat-cards { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin-bottom:12px; }
.stat-card,.chart-card,.filter-card,.table-card { background:#fff; border-radius:8px; box-shadow:0 1px 4px rgba(15,46,93,.06); }
.stat-card { display:flex; align-items:center; gap:12px; padding:16px; min-width:0; }.stat-icon { width:42px; height:42px; border-radius:10px; display:grid; place-items:center; flex-shrink:0; }.stat-icon svg { width:22px; height:22px; }.stat-label { color:#8a97ad; font-size:12px; }.stat-num { font-size:24px; font-weight:700; color:#1f2d3d; }
.chart-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-bottom:12px; }.chart-card { padding:16px; min-width:0; }.chart-card h3 { margin:0 0 8px; font-size:15px; }.chart-box { width:100%; height:220px; }
.donut-wrap { display:flex; align-items:center; gap:12px; }.donut-box { width:55%; position:relative; min-width:0; }.donut-center { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; pointer-events:none; }.donut-center strong { font-size:26px; }.donut-center small { color:#8a97ad; }.donut-legend { flex:1; }.legend-row { display:flex; align-items:center; gap:6px; font-size:12px; padding:4px 0; }.legend-row i { width:8px; height:8px; border-radius:50%; }.legend-row span { flex:1; }.legend-row b { font-weight:600; }
.filter-card { padding:18px; margin-bottom:12px; }.filter-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }.filter-grid label { display:grid; gap:7px; min-width:0; font-size:13px; color:#3b4a5f; }.filter-grid :deep(.el-select),.filter-grid :deep(.el-input) { width:100%; }.filter-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:16px; }
.table-card { padding:14px; min-width:0; }.tab-bar { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; border-bottom:1px solid #ebeef5; }.tabs { display:flex; gap:18px; overflow-x:auto; }.tab { border:0; background:transparent; white-space:nowrap; cursor:pointer; color:#5a6b80; padding:8px 2px 10px; }.tab.active { color:#1878fd; border-bottom:2px solid #1878fd; font-weight:600; }.tab-actions { display:flex; gap:8px; }.scope-note { color:#8a97ad; font-size:12px; }.pg-table :deep(thead th) { background:#f0f6ff; color:#2c3e5d; font-size:12px; }.pg-table :deep(td) { font-size:12px; }
.status-tag { display:inline-block; padding:2px 8px; border-radius:10px; white-space:nowrap; font-size:11px; }.s-normal { color:#178b69; background:#e4f7ec; }.s-pending { color:#e67e22; background:#fdf1e3; }.s-failed { color:#e74c3c; background:#fdeaea; }.s-done { color:#8b5cf6; background:#ede9fe; }
.pagination-bar { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; padding-top:12px; font-size:12px; color:#8a97ad; }.progress-mobile-list { display:none; }.remind-content { margin-top:12px; }
@media (max-width:1200px) { .stat-cards { grid-template-columns:repeat(2,minmax(0,1fr)); }.chart-grid { grid-template-columns:1fr; }.filter-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:640px) { .stat-cards,.filter-grid { grid-template-columns:1fr; }.pg-table { display:none; }.progress-mobile-list { display:grid; gap:12px; }.progress-mobile-item { padding:14px; border:1px solid #e5eaf2; border-radius:8px; min-width:0; }.mobile-top { display:flex; align-items:center; gap:8px; }.mobile-top strong { flex:1; min-width:0; overflow-wrap:anywhere; }.mobile-top small,.mobile-meta { font-size:12px; color:#8995a7; font-weight:400; }.mobile-top .status-tag { flex-shrink:0; }.progress-mobile-item p { overflow-wrap:anywhere; line-height:1.5; }.mobile-actions { display:flex; gap:12px; }.mobile-empty { padding:20px; text-align:center; color:#8995a7; }.tab-actions { width:100%; } }
</style>
