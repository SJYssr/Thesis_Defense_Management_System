<template>
  <div class="progress-page">
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

    <!-- 主体两栏 -->
    <div class="review-layout">
      <div class="review-main">
        <!-- 筛选区 -->
        <div class="filter-card">
          <div class="filter-row">
            <div class="filter-item grow">
              <span class="filter-label">学生姓名</span>
              <el-input v-model="query.keyword" placeholder="姓名 / 学号" clearable class="filter-input" @keyup.enter="onSearch" />
            </div>
            <div class="filter-item">
              <span class="filter-label">当前阶段</span>
              <el-select v-model="query.stage" class="filter-select" clearable placeholder="全部阶段">
                <el-option v-for="s in STAGE_OPTIONS" :key="s.value" :label="s.label" :value="s.value" />
              </el-select>
            </div>
            <div class="filter-item">
              <span class="filter-label">进度状态</span>
              <el-select v-model="query.risk_level" class="filter-select" clearable placeholder="全部状态">
                <el-option label="正常" value="NORMAL" />
                <el-option label="进度滞后" value="WARNING" />
                <el-option label="严重滞后" value="HIGH" />
              </el-select>
            </div>
            <div class="filter-actions">
              <el-button type="primary" @click="onSearch"><el-icon><Search /></el-icon>&nbsp;查询</el-button>
              <el-button class="reset-btn" @click="onReset"><el-icon><Refresh /></el-icon>&nbsp;重置</el-button>
            </div>
          </div>
        </div>

        <!-- 表格卡 -->
        <div class="table-card">
          <div class="legend-row">
            <span><i class="lg-dot lg-done" />已完成</span>
            <span><i class="lg-dot lg-current" />进行中</span>
            <span><i class="lg-dot lg-todo" />未开始</span>
            <span class="legend-risk"><i class="lg-dot lg-warn" />滞后</span>
            <span><i class="lg-dot lg-high" />严重滞后</span>
          </div>

          <el-table v-loading="loading" :data="items" class="prog-table" empty-text="暂无指导学生进度数据">
            <el-table-column prop="index" label="序号" width="48" align="center" />
            <el-table-column label="学生姓名" width="90" align="center">
              <template #default="{ row }"><span class="stu-name">{{ row.student_name || '—' }}</span></template>
            </el-table-column>
            <el-table-column prop="student_no" label="学号" width="96" align="center" />
            <el-table-column prop="topic_title" label="题目名称" min-width="170" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="topic-title">{{ row.topic_title || '暂未选题' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="当前进度" width="168" align="center">
              <template #default="{ row }">
                <div class="dot-trail">
                  <template v-for="(st, i) in STAGES" :key="st">
                    <span class="dot" :class="dotClass(row.current_stage, i)" />
                    <i v-if="i < STAGES.length - 1" class="dot-line" :class="dotClass(row.current_stage, i)" />
                    <span class="dot-label">{{ st }}</span>
                  </template>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="总体进度" width="140" align="center">
              <template #default="{ row }">
                <div class="bar-cell">
                  <el-progress
                    :percentage="row.progress_percent || 0" :stroke-width="8" :show-text="false"
                    :color="barColor(row.risk_level)"
                  />
                  <span class="bar-num">{{ row.progress_percent || 0 }}%</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="进度状态" width="92" align="center">
              <template #default="{ row }">
                <span class="status-tag" :class="riskClass(row.risk_level)">{{ riskCn(row.risk_level) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="最后更新时间" width="118" align="center">
              <template #default="{ row }">{{ fmtDate(row.updated_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="104" align="center" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openDetail(row)">查看</el-button>
                <el-button link type="warning" size="small" @click="remind(row)">提醒</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-bar">
            <span class="page-total">共 {{ total }} 条数据</span>
            <el-select v-model="query.page_size" size="small" class="page-size" @change="onSearch">
              <el-option label="10条/页" :value="10" />
              <el-option label="20条/页" :value="20" />
            </el-select>
            <el-pagination
              v-model:current-page="query.page" :page-size="query.page_size" :total="total"
              background layout="prev, pager, next, jumper" class="pager" @current-change="load"
            />
          </div>
        </div>
      </div>

      <!-- 右侧: 学生详细信息 -->
      <aside class="detail-panel">
        <div class="panel-head">
          <h3>学生详细信息</h3>
          <el-icon v-if="detail" class="panel-close" @click="detail = null"><Close /></el-icon>
        </div>
        <el-empty v-if="!detail" description="选择学生查看进度与分析" :image-size="70" class="panel-empty" />
        <template v-else>
          <div class="panel-body">
            <div class="sec-title"><i class="sec-icon blue" />基本信息</div>
            <div class="info-rows">
              <div class="info-row"><span class="k">学生姓名</span><span class="v">{{ detail.student_name || '—' }}</span></div>
              <div class="info-row"><span class="k">学号</span><span class="v">{{ detail.student_no || '—' }}</span></div>
              <div class="info-row"><span class="k">所属专业</span><span class="v">{{ detail.major_name || '—' }}</span></div>
              <div class="info-row"><span class="k">题目名称</span><span class="v">{{ detail.topic_title || '暂未选题' }}</span></div>
              <div class="info-row"><span class="k">指导教师</span><span class="v">{{ detail.teacher_name || '—' }}</span></div>
              <div class="info-row">
                <span class="k">总体进度</span>
                <span class="v bar-line">
                  <el-progress
                    :percentage="detail.progress_percent || 0" :stroke-width="8" :show-text="false"
                    :color="barColor(detail.risk_level)" class="bar-inline"
                  />
                  <b>{{ detail.progress_percent || 0 }}%</b>
                </span>
              </div>
              <div class="info-row"><span class="k">进度状态</span><span class="status-tag" :class="riskClass(detail.risk_level)">{{ riskCn(detail.risk_level) }}</span></div>
            </div>

            <div class="sec-title"><i class="sec-icon cyan" />阶段完成情况</div>
            <div class="stage-list">
              <div v-for="t in detail.timeline || []" :key="t.stage" class="stage-item">
                <i class="stage-dot" :class="t.done ? 'done' : t.status === 'NOT_SUBMITTED' ? 'todo' : 'doing'" />
                <span class="stage-name">{{ t.stage_cn }}</span>
                <span class="stage-date">{{ t.reviewed_at || t.submitted_at ? (t.reviewed_at || t.submitted_at).slice(0, 10) : '—' }}</span>
                <span class="status-tag" :class="t.status === 'APPROVED' ? 's-passed' : t.status === 'NOT_SUBMITTED' ? 's-withdraw' : 's-pending'">{{ t.status_cn }}</span>
              </div>
            </div>

            <div class="sec-title">
              <i class="sec-icon purple" />AI 进度分析
              <el-button link type="primary" size="small" class="sec-action" :loading="analysing" @click="analyse">
                {{ analysis ? '重新分析' : '开始分析' }}
              </el-button>
            </div>
            <template v-if="analysis">
              <div class="ai-risk-row">
                <span>AI 风险评估:</span>
                <span class="status-tag" :class="riskClass(analysis.risk_level)">{{ riskCn(analysis.risk_level) }}</span>
                <small class="muted-note">规则引擎: {{ analysis.rule_snapshot?.rule_risk }} · 进度 {{ analysis.rule_snapshot?.progress_percent }}%</small>
              </div>
              <div class="content-block"><strong>总体分析：</strong>{{ analysis.summary }}</div>
              <div v-if="analysis.problems?.length" class="content-block">
                <strong>存在问题：</strong>
                <ul><li v-for="(p, i) in analysis.problems" :key="i">{{ p }}</li></ul>
              </div>
              <div v-if="analysis.suggestions?.length" class="content-block">
                <strong>改进建议：</strong>
                <ul><li v-for="(s, i) in analysis.suggestions" :key="i">{{ s }}</li></ul>
              </div>
            </template>
            <div v-else class="content-block muted-block">点击「开始分析」获取该生的 AI 进度评估与改进建议。</div>
          </div>
          <div class="panel-footer single">
            <el-button type="primary" class="remind-btn" @click="remind(detail)">
              <el-icon><Promotion /></el-icon>&nbsp;发送进度提醒
            </el-button>
          </div>
        </template>
      </aside>
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
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, Refresh, Close, Promotion } from '@element-plus/icons-vue'
import { progressApi } from '@/api/business'
import { aiApi } from '@/api/common'

const CAP_ICON = '<path d="M12 3 1 8l11 5 9-4.09V15h2V8L12 3zM5 12.18V16c0 1.66 3.13 3 7 3s7-1.34 7-3v-3.82l-7 3.18-7-3.18z"/>'
const CHECK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.2L6.4 11.8l1.5-1.5 2.9 2.9 5.3-5.3 1.5 1.5-6.8 6.8z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 10.9V6.5h-2v6.4l5 2.9 1-1.7-4-2.3z"/>'
const WARN_ICON = '<path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/>'

const STAGES = ['选题', '开题', '中期', '终期', '材料']
const STAGE_VALUE_CN: Record<string, string> = {
  SELECTION: '选题', OPENING: '开题', MIDTERM: '中期', FINAL: '终期', MATERIAL: '毕业材料', COMPLETED: '毕业材料',
}
const STAGE_OPTIONS = Object.entries(STAGE_VALUE_CN).map(([value, label]) => ({ value, label }))

const items = ref<any[]>([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 10, keyword: '', stage: '', risk_level: '' })

const counts = reactive<Record<string, number | null>>({ all: null, normal: null, warning: null, high: null })
const statCards = computed(() => [
  { label: '我的指导学生', value: counts.all, icon: CAP_ICON, tileBg: '#d7f0fe', iconColor: '#2563eb' },
  { label: '进度正常', value: counts.normal, icon: CHECK_ICON, tileBg: '#ddfef0', iconColor: '#22c55e',
    ring: pct(counts.normal, counts.all), ringColor: '#22c55e' },
  { label: '进度滞后', value: counts.warning, icon: CLOCK_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b',
    ring: pct(counts.warning, counts.all), ringColor: '#f59e0b' },
  { label: '严重滞后', value: counts.high, icon: WARN_ICON, tileBg: '#fee7ed', iconColor: '#ef4444',
    ring: pct(counts.high, counts.all), ringColor: '#ef4444' },
])
function pct(value: number | null, all: number | null) {
  return value !== null && all ? Math.round((value / all) * 100) : 0
}

async function load() {
  loading.value = true
  try {
    const res = await progressApi.teacherStudents({
      page: query.page, page_size: query.page_size,
      keyword: query.keyword || undefined,
      stage: query.stage || undefined,
      risk_level: query.risk_level || undefined,
    })
    items.value = res.data.items.map((row: any, i: number) => ({ ...row, index: (query.page - 1) * query.page_size + i + 1 }))
    total.value = res.data.total
  } finally { loading.value = false }
}

async function loadSummary() {
  const levels = ['', 'NORMAL', 'WARNING', 'HIGH']
  const results = await Promise.all(levels.map(l => progressApi.teacherStudents({ risk_level: l || undefined, page: 1, page_size: 1 })))
  counts.all = results[0].data.total
  counts.normal = results[1].data.total
  counts.warning = results[2].data.total
  counts.high = results[3].data.total
}

async function refresh() { await Promise.all([load(), loadSummary()]) }

function onSearch() { query.page = 1; load() }
function onReset() {
  Object.assign(query, { page: 1, keyword: '', stage: '', risk_level: '' })
  load()
}

function dotClass(currentStage: string, index: number) {
  const order = ['SELECTION', 'OPENING', 'MIDTERM', 'FINAL', 'MATERIAL']
  const cur = order.indexOf(currentStage === 'COMPLETED' ? 'MATERIAL' : currentStage)
  if (cur < 0) return index === 0 ? 'current' : 'todo'
  if (index < cur) return 'done'
  if (index === cur) return currentStage === 'COMPLETED' ? 'done' : 'current'
  return 'todo'
}
function barColor(risk: string) {
  if (risk === 'HIGH') return '#ef4444'
  if (risk === 'WARNING') return '#f59e0b'
  return '#22c55e'
}
function riskCn(risk: string) {
  return risk === 'HIGH' ? '严重滞后' : risk === 'WARNING' ? '进度滞后' : '正常'
}
function riskClass(risk: string) {
  return risk === 'HIGH' ? 's-high' : risk === 'WARNING' ? 's-warn' : 's-passed'
}
function fmtDate(value?: string | null) { return (value || '—').replace('T', ' ').slice(0, 16) }

/* ---------- 右侧详情 + AI 分析 ---------- */
const detail = ref<any>(null)
const analysis = ref<any>(null)
const analysing = ref(false)

async function openDetail(row: any) {
  detail.value = (await progressApi.teacherStudentDetail(row.student_id)).data
  analysis.value = null
}

async function analyse() {
  analysing.value = true
  try {
    analysis.value = (await aiApi.analyseProgress(detail.value.student_id)).data
  } catch {
    /* 已提示 */
  } finally {
    analysing.value = false
  }
}

/* ---------- 发送提醒 ---------- */
const reminderVisible = ref(false)
const reminding = ref(false)
const remindTarget = ref<any>(null)
const reminderForm = reactive({ title: '', content: '' })

function remind(row: any) {
  remindTarget.value = row
  reminderForm.title = '毕业设计进度提醒'
  reminderForm.content = `您当前处于「${row.current_stage_cn || '选题'}」阶段，请按计划推进毕设任务。`
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

onMounted(refresh)
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

/* 主体两栏 */
.review-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 12px;
  align-items: start;
}
.review-main { min-width: 0; display: flex; flex-direction: column; gap: 12px; }

/* 筛选区 */
.filter-card {
  background: #fff;
  border-radius: 8px;
  padding: 12px 14px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.filter-row { display: flex; align-items: center; gap: 14px; }
.filter-item { display: flex; align-items: center; gap: 8px; }
.filter-item.grow { flex: 1; min-width: 0; }
.filter-label { font-size: 13px; color: #5a6b80; white-space: nowrap; }
.filter-input { width: 200px; }
.filter-select { width: 140px; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }
.reset-btn { border: 1px solid #dcdfe6; color: #5a6b80; }

/* 表格卡 */
.table-card {
  background: #fff;
  border-radius: 8px;
  padding: 12px 14px 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.legend-row {
  display: flex;
  gap: 18px;
  font-size: 12px;
  color: #5a6b80;
  margin-bottom: 8px;
}
.legend-row span { display: flex; align-items: center; gap: 5px; }
.lg-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.lg-done { background: #22c55e; }
.lg-current { background: #1878fd; }
.lg-todo { background: #c2cbd8; }
.lg-warn { background: #f59e0b; }
.lg-high { background: #ef4444; }

.prog-table :deep(thead th) {
  background: #f0f6ff;
  color: #3b4a5f;
  font-weight: 600;
  font-size: 12px;
  padding: 7px 0;
}
.prog-table :deep(td) { font-size: 12px; color: #3b4a5f; padding: 8px 0; }
.prog-table :deep(.cell) { white-space: nowrap; padding: 0 6px; }
.prog-table :deep(.el-button + .el-button) { margin-left: 6px; }
.stu-name { color: #1f2d3d; font-weight: 600; }
.topic-title { color: #1f2d3d; font-weight: 500; }

/* 进度节点 */
.dot-trail { display: flex; align-items: center; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.dot.done { background: #22c55e; }
.dot.current { background: #1878fd; box-shadow: 0 0 0 3px rgba(24, 120, 253, 0.18); }
.dot.todo { background: #d5dde8; }
.dot-line { flex: 1; height: 2px; min-width: 8px; }
.dot-line.done { background: #22c55e; }
.dot-line.current { background: #1878fd; }
.dot-line.todo { background: #e2e8f0; }
.dot-label { font-size: 10px; color: #8a97ad; margin: 0 4px 0 2px; white-space: nowrap; }
.dot-trail .dot-label:last-child { margin-right: 0; }

/* 进度条 */
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
.s-pending { color: #e67e22; background: #fdf1e3; }
.s-warn { color: #d97706; background: #fef3c7; }
.s-high { color: #e74c3c; background: #fdeaea; }
.s-failed { color: #e74c3c; background: #fdeaea; }
.s-withdraw { color: #8a97ad; background: #eef1f5; }

/* 分页 */
.pagination-bar { display: flex; align-items: center; padding: 10px 4px 2px; }
.page-total { font-size: 12px; color: #8a97a8; margin-right: 12px; }
.page-size { width: 96px; margin-right: 12px; }
.pager { margin-left: auto; }

/* 右侧详情面板 */
.detail-panel {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  display: flex;
  flex-direction: column;
  min-height: 640px;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 12px;
  border-bottom: 1px solid #eef2f8;
}
.panel-head h3 { font-size: 15px; color: #1f2d3d; margin: 0; }
.panel-close { color: #8a97ad; cursor: pointer; }
.panel-close:hover { color: #1878fd; }
.panel-empty { flex: 1; display: flex; flex-direction: column; justify-content: center; }
.panel-body { flex: 1; padding: 14px 16px; overflow-y: auto; }
.sec-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  font-weight: 600;
  color: #1f2d3d;
  margin: 4px 0 10px;
}
.sec-icon { width: 16px; height: 16px; border-radius: 4px; flex-shrink: 0; }
.sec-icon.blue { background: #1878fd; }
.sec-icon.cyan { background: #0e7490; }
.sec-icon.purple { background: #8b5cf6; }
.sec-action { margin-left: auto; }
.info-rows { margin-bottom: 16px; }
.info-row { display: flex; padding: 5px 0; font-size: 12.5px; line-height: 1.5; }
.info-row .k { width: 72px; flex-shrink: 0; color: #8a97ad; }
.info-row .v { flex: 1; min-width: 0; color: #3b4a5f; overflow-wrap: anywhere; }
.bar-line { display: flex; align-items: center; gap: 8px; }
.bar-line :deep(.el-progress) { flex: 1; }
.bar-line b { font-size: 12.5px; color: #1f2d3d; }

.stage-list { margin-bottom: 16px; }
.stage-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0 7px 6px;
  font-size: 12.5px;
}
.stage-item::before {
  content: '';
  position: absolute;
  left: 11px;
  top: 24px;
  bottom: -8px;
  width: 1px;
  background: #e5eaf2;
}
.stage-item:last-child::before { display: none; }
.stage-dot { width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0; }
.stage-dot.done { background: #22c55e; }
.stage-dot.doing { background: #1878fd; }
.stage-dot.todo { background: #d5dde8; }
.stage-name { color: #1f2d3d; font-weight: 500; }
.stage-date { flex: 1; color: #8a97ad; font-size: 11.5px; text-align: right; margin-right: 8px; }

.ai-risk-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: #5e6d82;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.muted-note { color: #a8b3c5; }
.content-block {
  background: #f8fafc;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12.5px;
  color: #303133;
  line-height: 1.7;
  margin-bottom: 10px;
}
.content-block ul { margin: 4px 0 0; padding-left: 18px; }
.content-block.muted-block { color: #8a97ad; }
.panel-footer {
  display: flex;
  padding: 12px 16px;
  border-top: 1px solid #eef2f8;
}
.panel-footer.single { justify-content: stretch; }
.remind-btn { width: 100%; }

@media (max-width: 1200px) {
  .review-layout { grid-template-columns: 1fr; }
  .detail-panel { min-height: 0; }
}
@media (max-width: 900px) {
  .stat-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-row { flex-wrap: wrap; }
  .filter-item { flex: 1 1 220px; min-width: 0; }
  .filter-item.grow { flex: 1 1 220px; }
  .filter-item :deep(.el-input), .filter-item :deep(.el-select) { width: 100%; }
  .filter-actions { margin-left: 0; }
  .dot-trail .dot-label { display: none; }
  .dot-trail .dot-line { min-width: 12px; }
}
@media (max-width: 600px) {
  .stat-cards { grid-template-columns: minmax(0, 1fr); }
  .stat-card { min-height: 70px; align-items: center; }
  .filter-item { flex-basis: 100%; }
  .filter-actions { flex-basis: 100%; }
  .pagination-bar { flex-wrap: wrap; gap: 10px; }
  .pager { margin-left: 0; max-width: 100%; overflow-x: auto; }
}
</style>
