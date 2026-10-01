<template>
  <div class="ai-progress-page">
    <!-- 统计卡 -->
    <div class="stat-cards">
      <div v-for="c in statCards" :key="c.label" class="stat-card">
        <div class="stat-icon" :style="{ background: c.tileBg, color: c.iconColor }">
          <svg viewBox="0 0 24 24" fill="currentColor" v-html="c.icon" />
        </div>
        <div class="stat-info">
          <div class="stat-num">{{ c.value ?? '—' }}</div>
          <div class="stat-label">{{ c.label }}</div>
        </div>
        <div v-if="c.ring !== undefined" class="stat-ring" :style="{ '--pct': c.ring, '--ring-color': c.ringColor }">
          <span>{{ c.ring }}%</span>
        </div>
      </div>
    </div>

    <!-- 第一行图表 -->
    <el-row :gutter="12" class="chart-row">
      <el-col :xs="24" :md="8">
        <div class="chart-card">
          <div class="card-head"><div class="head-title"><i />各阶段平均进度（%）</div></div>
          <div ref="stageBarRef" class="chart-box" />
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="chart-card">
          <div class="card-head"><div class="head-title"><i />各阶段进度分布（人数）</div></div>
          <div class="donut-wrap">
            <div class="donut-chart">
              <div ref="stagePieRef" class="chart-box donut-box" />
              <div class="donut-center"><b>{{ students.length }}</b><span>指导学生</span></div>
            </div>
            <div class="donut-legend">
              <div v-for="s in stageDistribution" :key="s.name" class="dl-item">
                <i :style="{ background: s.color }" />
                <span class="dl-name">{{ s.name }}</span>
                <span class="dl-val">{{ s.value }}（{{ pctOf(s.value, students.length) }}%）</span>
              </div>
            </div>
          </div>
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="chart-card ai-card">
          <div class="card-head">
            <div class="head-title"><i class="head-ai" />AI 进度分析结论</div>
            <span v-if="analysisTime" class="head-time">更新时间：{{ analysisTime }}</span>
          </div>
          <div class="ai-select-row">
            <el-select v-model="selectedId" placeholder="选择学生" size="default" class="ai-select">
              <el-option v-for="s in students" :key="s.student_id" :label="`${s.student_name} · ${s.student_no}`" :value="s.student_id" />
            </el-select>
            <el-button type="primary" :loading="busy" :disabled="!selectedId" @click="analyse()">
              <el-icon><MagicStick /></el-icon>&nbsp;生成分析
            </el-button>
          </div>
          <template v-if="analysis">
            <div class="ai-conclusion">
              <div class="ai-risk-row">
                <span>风险评估:</span>
                <span class="status-tag" :class="riskClass(analysis.risk_level)">{{ riskCn(analysis.risk_level) }}</span>
                <small class="muted-note">规则引擎: {{ analysis.rule_snapshot?.rule_risk }} · 进度 {{ analysis.rule_snapshot?.progress_percent }}%</small>
              </div>
              <p class="ai-text">{{ analysis.summary }}</p>
            </div>
          </template>
          <div v-else class="ai-conclusion empty">选择学生并点击「生成分析」，AI 将结合规则引擎进度、阶段材料与截止时间给出评估结论。</div>
        </div>
      </el-col>
    </el-row>

    <!-- 第二行图表 -->
    <el-row :gutter="12" class="chart-row">
      <el-col :xs="24" :md="8">
        <div class="chart-card">
          <div class="card-head"><div class="head-title"><i />学生进度明细</div></div>
          <div class="student-bars">
            <div v-for="s in students.slice(0, 8)" :key="s.student_id" class="student-bar-row">
              <span class="sb-name">{{ s.student_name }}</span>
              <el-progress
                :percentage="s.progress_percent || 0" :stroke-width="8" :show-text="false"
                :color="barColor(s.risk_level)" class="sb-bar"
              />
              <span class="sb-num">{{ s.progress_percent || 0 }}%</span>
            </div>
            <el-empty v-if="!students.length" description="暂无学生进度" :image-size="60" />
          </div>
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="chart-card">
          <div class="card-head"><div class="head-title"><i />进度风险分布</div></div>
          <div class="donut-wrap">
            <div class="donut-chart">
              <div ref="riskPieRef" class="chart-box donut-box" />
              <div class="donut-center"><b>{{ students.length }}</b><span>指导学生</span></div>
            </div>
            <div class="donut-legend">
              <div v-for="r in riskDistribution" :key="r.name" class="dl-item">
                <i :style="{ background: r.color }" />
                <span class="dl-name">{{ r.name }}</span>
                <span class="dl-val">{{ r.value }}（{{ pctOf(r.value, students.length) }}%）</span>
              </div>
            </div>
          </div>
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="chart-card">
          <div class="card-head">
            <div class="head-title"><i class="head-ai" />AI 建议与行动方案</div>
            <el-button v-if="analysis?.suggestions?.length" size="small" class="gen-btn" @click="useSuggestion">
              <el-icon><Promotion /></el-icon>&nbsp;生成提醒
            </el-button>
          </div>
          <div v-if="analysis?.suggestions?.length" class="suggestion-list">
            <div v-for="(s, i) in analysis.suggestions" :key="i" class="suggestion-item" :class="`sg-${i % 4}`">
              <span class="sg-no">{{ i + 1 }}</span>{{ s }}
            </div>
          </div>
          <div v-else class="ai-conclusion empty">生成 AI 分析后，这里将给出针对该生的建议与行动方案。</div>
        </div>
      </el-col>
    </el-row>

    <!-- 需要重点关注的学生 -->
    <div class="risk-card">
      <div class="card-head">
        <div class="head-title"><i class="head-warn" />需要重点关注的学生<span v-if="riskStudents.length">（共 {{ riskStudents.length }} 人）</span></div>
      </div>
      <el-table :data="riskStudents" class="risk-table" empty-text="当前没有需要重点关注的学生">
        <el-table-column prop="index" label="序号" width="56" align="center" />
        <el-table-column prop="student_name" label="学生姓名" width="96" align="center" />
        <el-table-column prop="student_no" label="学号" width="104" align="center" />
        <el-table-column prop="topic_title" label="题目名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="current_stage_cn" label="当前阶段" width="96" align="center" />
        <el-table-column label="进度" width="150" align="center">
          <template #default="{ row }">
            <div class="bar-cell">
              <el-progress :percentage="row.progress_percent || 0" :stroke-width="8" :show-text="false" :color="barColor(row.risk_level)" />
              <span class="bar-num">{{ row.progress_percent || 0 }}%</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="风险等级" width="104" align="center">
          <template #default="{ row }">
            <span class="status-tag" :class="riskClass(row.risk_level)">{{ riskCn(row.risk_level) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="analyse(row.student_id)">分析</el-button>
            <el-button link type="warning" size="small" @click="remind(row)">发送提醒</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 发送提醒弹窗 -->
    <el-dialog v-model="reminderVisible" title="发送进度提醒" width="520px">
      <el-form label-width="60px">
        <el-form-item label="学生">
          <span>{{ remindTarget?.student_name }}（{{ remindTarget?.student_no }}）</span>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="reminderForm.title" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="reminderForm.content" type="textarea" :rows="4" maxlength="2000" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reminderVisible = false">取消</el-button>
        <el-button type="primary" :loading="reminding" @click="sendReminder">发送</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { MagicStick, Promotion } from '@element-plus/icons-vue'
import { progressApi } from '@/api/business'
import { aiApi } from '@/api/common'

const USERS_ICON = '<path d="M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm7 1a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2 20c0-3 3.2-5 7-5s7 2 7 5v1H2v-1zm15.5-3.6c2.1.5 4.5 1.9 4.5 3.6v1h-4.2c.1-.3.2-.7.2-1v-1c0-.9-.2-1.8-.5-2.6z"/>'
const CHECK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.2L6.4 11.8l1.5-1.5 2.9 2.9 5.3-5.3 1.5 1.5-6.8 6.8z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 10.9V6.5h-2v6.4l5 2.9 1-1.7-4-2.3z"/>'
const WARN_ICON = '<path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/>'

const students = ref<any[]>([])
const counts = reactive<Record<string, number | null>>({ all: null, normal: null, warning: null, high: null })
const statCards = computed(() => [
  { label: '指导学生总数', value: counts.all, icon: USERS_ICON, tileBg: '#d7f0fe', iconColor: '#2563eb' },
  { label: '进度正常', value: counts.normal, icon: CHECK_ICON, tileBg: '#ddfef0', iconColor: '#22c55e',
    ring: pctOf(counts.normal, counts.all), ringColor: '#22c55e' },
  { label: '进度滞后', value: counts.warning, icon: CLOCK_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b',
    ring: pctOf(counts.warning, counts.all), ringColor: '#f59e0b' },
  { label: '存在风险', value: counts.high, icon: WARN_ICON, tileBg: '#fee7ed', iconColor: '#ef4444',
    ring: pctOf(counts.high, counts.all), ringColor: '#ef4444' },
])
function pctOf(value: number | null | undefined, all: number | null | undefined) {
  return value !== null && value !== undefined && all ? Math.round((value / all) * 100) : 0
}

const STAGE_ORDER = ['SELECTION', 'OPENING', 'MIDTERM', 'FINAL', 'MATERIAL', 'COMPLETED']
const STAGE_CN: Record<string, string> = {
  SELECTION: '选题', OPENING: '开题报告', MIDTERM: '中期答辩', FINAL: '终期答辩', MATERIAL: '毕业材料', COMPLETED: '已完成',
}
const STAGE_COLORS = ['#94a3b8', '#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6']

const stageDistribution = computed(() => {
  const groups = new Map<string, number>()
  students.value.forEach((s) => {
    const name = STAGE_CN[s.current_stage] || s.current_stage_cn || '未知'
    groups.set(name, (groups.get(name) || 0) + 1)
  })
  const names = [...new Set(students.value.map(s => STAGE_CN[s.current_stage]).filter(Boolean))]
  return names.map((name, i) => ({ name, value: groups.get(name) || 0, color: STAGE_COLORS[i % STAGE_COLORS.length] }))
})

const riskDistribution = computed(() => [
  { name: '正常', value: counts.normal || 0, color: '#3b82f6' },
  { name: '进度滞后', value: counts.warning || 0, color: '#f59e0b' },
  { name: '严重滞后', value: counts.high || 0, color: '#ef4444' },
].filter(r => r.value > 0))

const riskStudents = computed(() =>
  students.value
    .filter(s => s.risk_level && s.risk_level !== 'NORMAL')
    .map((s, i) => ({ ...s, index: i + 1 })))

function barColor(risk: string) {
  if (risk === 'HIGH') return '#ef4444'
  if (risk === 'WARNING') return '#f59e0b'
  return '#3b82f6'
}
function riskCn(risk: string) {
  return risk === 'HIGH' ? '严重风险' : risk === 'WARNING' ? '轻度滞后' : '正常'
}
function riskClass(risk: string) {
  return risk === 'HIGH' ? 's-high' : risk === 'WARNING' ? 's-warn' : 's-passed'
}

/* ---------- 图表 ---------- */
const stageBarRef = ref<HTMLElement>()
const stagePieRef = ref<HTMLElement>()
const riskPieRef = ref<HTMLElement>()
let charts: echarts.ECharts[] = []
let resizeHandler: (() => void) | undefined

function renderCharts() {
  charts.forEach(c => c.dispose())
  charts = []
  const init = (el: HTMLElement | undefined) => { if (el) charts.push(echarts.init(el)) }
  init(stageBarRef.value)
  init(stagePieRef.value)
  init(riskPieRef.value)
  const [bar, stagePie, riskPie] = charts

  const stageNames = students.value.length
    ? [...new Set(students.value.map(s => STAGE_CN[s.current_stage]).filter(Boolean))]
    : []
  const avgByStage = stageNames.map((name) => {
    const rows = students.value.filter(s => STAGE_CN[s.current_stage] === name)
    return Math.round(rows.reduce((acc, r) => acc + (r.progress_percent || 0), 0) / rows.length)
  })
  bar?.setOption({
    grid: { left: 44, right: 16, top: 24, bottom: 32 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: stageNames, axisLabel: { interval: 0, fontSize: 10 } },
    yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
    series: [{ type: 'bar', barMaxWidth: 30, data: avgByStage.map((v, i) => ({
      value: v, itemStyle: { color: STAGE_COLORS[i % STAGE_COLORS.length], borderRadius: [4, 4, 0, 0] },
    })) }],
  })

  stagePie?.setOption({
    tooltip: { trigger: 'item' },
    series: [{ type: 'pie', radius: ['58%', '82%'], center: ['50%', '50%'],
      data: stageDistribution.value.map(s => ({ name: s.name, value: s.value, itemStyle: { color: s.color } })),
      label: { show: false } }],
  })

  riskPie?.setOption({
    tooltip: { trigger: 'item' },
    series: [{ type: 'pie', radius: ['58%', '82%'], center: ['50%', '50%'],
      data: riskDistribution.value.map(r => ({ name: r.name, value: r.value, itemStyle: { color: r.color } })),
      label: { show: false } }],
  })

  resizeHandler = () => charts.forEach(c => c.resize())
  window.addEventListener('resize', resizeHandler)
}

/* ---------- 数据加载 ---------- */
async function load() {
  const res = await progressApi.teacherStudents({ page: 1, page_size: 100 })
  students.value = res.data.items
  counts.all = res.data.total
  counts.normal = students.value.filter(s => s.risk_level === 'NORMAL').length
  counts.warning = students.value.filter(s => s.risk_level === 'WARNING').length
  counts.high = students.value.filter(s => s.risk_level === 'HIGH').length
  await nextTick()
  renderCharts()
}

/* ---------- AI 分析 ---------- */
const selectedId = ref<number>()
const analysis = ref<any>(null)
const analysisTime = ref('')
const busy = ref(false)

async function analyse(id?: number) {
  if (id) selectedId.value = id
  if (!selectedId.value) return
  busy.value = true
  try {
    analysis.value = (await aiApi.analyseProgress(selectedId.value)).data
    analysisTime.value = new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch {
    /* 已提示 */
  } finally {
    busy.value = false
  }
}

function useSuggestion() {
  if (!analysis.value) return
  const target = students.value.find(s => s.student_id === selectedId.value)
  if (target) remind(target, analysis.value.recommended_reminder)
}

/* ---------- 发送提醒 ---------- */
const reminderVisible = ref(false)
const reminding = ref(false)
const remindTarget = ref<any>(null)
const reminderForm = reactive({ title: '', content: '' })

function remind(row: any, preset?: string) {
  remindTarget.value = row
  reminderForm.title = '毕业设计进度提醒'
  reminderForm.content = preset || `您当前处于「${row.current_stage_cn || '选题'}」阶段，请按计划推进毕设任务。`
  reminderVisible.value = true
}

async function sendReminder() {
  reminding.value = true
  try {
    await progressApi.sendReminder(remindTarget.value.student_id, { ...reminderForm })
    ElMessage.success('提醒已发送')
    reminderVisible.value = false
  } catch {
    /* 已提示 */
  } finally {
    reminding.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => {
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
  charts.forEach(c => c.dispose())
})
</script>

<style scoped>
/* 统计卡 */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}
.stat-card {
  position: relative;
  background: #fff;
  border-radius: 8px;
  padding: 14px;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  min-height: 88px;
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
.stat-info { flex: 1; min-width: 0; padding-right: 44px; }
.stat-num {
  font-size: 26px;
  font-weight: 700;
  color: #1f2d3d;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
  line-height: 1.15;
}
.stat-label { font-size: 12.5px; color: #8a97ad; margin-top: 2px; }
.stat-ring {
  --pct: 0;
  --ring-color: #f59e0b;
  position: absolute;
  right: 12px;
  top: 14px;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(closest-side, #fff 72%, transparent 73% 100%),
    conic-gradient(var(--ring-color) calc(var(--pct) * 1%), #eef1f6 0);
}
.stat-ring span { font-size: 10px; font-weight: 600; color: #5a6b80; }

/* 图表卡 */
.chart-row { margin-bottom: 12px; }
.chart-card {
  background: #fff;
  border-radius: 8px;
  padding: 14px 16px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  min-height: 300px;
  display: flex;
  flex-direction: column;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.head-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #1f2d3d;
}
.head-title i { width: 14px; height: 14px; border-radius: 4px; background: #1878fd; }
.head-title i.head-ai { background: #8b5cf6; }
.head-title i.head-warn { background: #f59e0b; }
.head-time { margin-left: auto; font-size: 11px; color: #a8b3c5; }
.chart-box { flex: 1; min-height: 230px; }

.donut-wrap { display: flex; align-items: center; gap: 10px; flex: 1; }
.donut-chart { position: relative; width: 180px; height: 200px; flex-shrink: 0; }
.donut-box { width: 100%; height: 100%; }
.donut-center {
  position: absolute;
  left: 0;
  right: 0;
  top: 44%;
  text-align: center;
  pointer-events: none;
}
.donut-center b { display: block; font-size: 26px; color: #1f2d3d; line-height: 1.1; }
.donut-center span { font-size: 11px; color: #8a97ad; }
.donut-legend { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.dl-item { display: flex; align-items: center; gap: 7px; font-size: 12px; color: #5a6b80; }
.dl-item i { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.dl-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dl-val { color: #1f2d3d; white-space: nowrap; }

/* AI 卡 */
.ai-select-row { display: flex; gap: 8px; margin-bottom: 12px; }
.ai-select { flex: 1; min-width: 0; }
.ai-conclusion {
  background: #eef5ff;
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.8;
  color: #3b4a5f;
  flex: 1;
  overflow-y: auto;
}
.ai-conclusion.empty { color: #8a97ad; }
.ai-risk-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.ai-text { margin: 0; white-space: pre-wrap; }
.muted-note { color: #93a2b8; }
.gen-btn { border: 1px solid #dcdfe6; color: #5a6b80; }

/* 学生进度明细 */
.student-bars { flex: 1; display: flex; flex-direction: column; gap: 12px; overflow-y: auto; }
.student-bar-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.sb-name { width: 64px; text-align: right; color: #3b4a5f; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex-shrink: 0; }
.sb-bar { flex: 1; }
.sb-num { width: 38px; color: #3b4a5f; text-align: right; }

/* 建议列表 */
.suggestion-list { flex: 1; display: flex; flex-direction: column; gap: 9px; overflow-y: auto; }
.suggestion-item {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  background: #f8fafc;
  border-radius: 8px;
  padding: 9px 12px;
  font-size: 12.5px;
  line-height: 1.6;
  color: #3b4a5f;
}
.sg-no {
  width: 18px;
  height: 18px;
  border-radius: 5px;
  color: #fff;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}
.sg-0 .sg-no { background: #1878fd; }
.sg-1 .sg-no { background: #f59e0b; }
.sg-2 .sg-no { background: #22c55e; }
.sg-3 .sg-no { background: #8b5cf6; }

/* 风险学生表 */
.risk-card {
  background: #fff;
  border-radius: 8px;
  padding: 14px 16px 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.risk-table :deep(thead th) {
  background: #f0f6ff;
  color: #3b4a5f;
  font-weight: 600;
  font-size: 12px;
  padding: 7px 0;
}
.risk-table :deep(td) { font-size: 12px; color: #3b4a5f; padding: 6px 0; }
.risk-table :deep(.cell) { white-space: nowrap; padding: 0 6px; }
.risk-table :deep(.el-button + .el-button) { margin-left: 6px; }
.bar-cell { display: flex; align-items: center; gap: 8px; }
.bar-cell :deep(.el-progress) { flex: 1; }
.bar-num { font-size: 12px; color: #3b4a5f; width: 38px; text-align: right; }

/* 状态标签 */
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.s-passed { color: #27ae60; background: #e4f7ec; }
.s-warn { color: #d97706; background: #fef3c7; }
.s-high { color: #e74c3c; background: #fdeaea; }

@media (max-width: 900px) {
  .stat-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .donut-wrap { flex-direction: column; }
  .donut-legend { width: 100%; }
}
@media (max-width: 600px) {
  .stat-cards { grid-template-columns: minmax(0, 1fr); }
  .stat-card { min-height: 70px; align-items: center; }
}
</style>
