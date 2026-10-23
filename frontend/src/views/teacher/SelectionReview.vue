<template>
  <div class="selection-page">
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
            <div class="filter-item">
              <span class="filter-label">学生姓名</span>
              <el-input v-model="query.keyword" placeholder="请输入学生姓名" clearable class="filter-input" @keyup.enter="onSearch" />
            </div>
            <div class="filter-item">
              <span class="filter-label">学号</span>
              <el-input v-model="query.studentNo" placeholder="请输入学号" clearable class="filter-input narrow" @keyup.enter="onSearch" />
            </div>
            <div class="filter-item grow">
              <span class="filter-label">题目名称</span>
              <el-input v-model="query.topic" placeholder="请输入题目名称" clearable class="filter-input" @keyup.enter="onSearch" />
            </div>
            <div class="filter-item">
              <span class="filter-label">审核状态</span>
              <el-select v-model="query.status" class="filter-select" placeholder="全部状态">
                <el-option label="全部状态" value="" />
                <el-option label="待审核" value="PENDING" />
                <el-option label="已通过" value="APPROVED" />
                <el-option label="未通过" value="REJECTED" />
              </el-select>
            </div>
          </div>
          <div class="filter-row">
            <div class="filter-item">
              <span class="filter-label">申请时间</span>
              <el-date-picker
                v-model="query.dates" type="daterange" range-separator="至"
                start-placeholder="开始日期" end-placeholder="结束日期"
                value-format="YYYY-MM-DD" class="filter-date"
              />
            </div>
            <div class="filter-actions">
              <el-button type="primary" @click="onSearch"><el-icon><Search /></el-icon>&nbsp;查询</el-button>
              <el-button class="reset-btn" @click="onReset"><el-icon><Refresh /></el-icon>&nbsp;重置</el-button>
            </div>
          </div>
        </div>

        <!-- 表格卡 -->
        <div class="table-card">
          <div class="table-toolbar">
            <div class="toolbar-left">
              <el-button class="pass-btn" @click="onBatch(true)"><el-icon><Select /></el-icon>&nbsp;批量通过</el-button>
              <el-button class="reject-btn" @click="onBatch(false)"><el-icon><CloseBold /></el-icon>&nbsp;批量驳回</el-button>
            </div>
          </div>

          <el-table v-loading="loading" :data="items" class="sel-table" :element-loading-text="loadingText" empty-text="暂无符合条件的选题申请" @selection-change="onSelectionChange">
            <el-table-column type="selection" width="40" />
            <el-table-column prop="index" label="序号" width="48" align="center" />
            <el-table-column label="学生姓名" width="90" align="center">
              <template #default="{ row }">
                <span class="stu-name">{{ row.student_name || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="student_no" label="学号" width="96" align="center" />
            <el-table-column v-if="isAdmin" label="指导教师" width="90" align="center">
              <template #default="{ row }">{{ row.teacher_name || '—' }}</template>
            </el-table-column>
            <el-table-column prop="topic_title" label="题目名称" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="topic-title">{{ row.topic_title || '题目已删除' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="student_reason" label="选题理由" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <span :class="{ dash: !row.student_reason }">{{ row.student_reason || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="申请时间" width="108" align="center">
              <template #default="{ row }">
                <div class="time-cell">
                  <span>{{ (row.created_at || '').slice(0, 10) }}</span>
                  <span>{{ (row.created_at || '').slice(11, 16) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="审核状态" width="86" align="center">
              <template #default="{ row }">
                <span class="status-tag" :class="row.statusClass">{{ row.statusCn }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="96" align="center" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.status === 'PENDING'" link type="primary" size="small" @click="openDetail(row)">审核</el-button>
                <el-button v-else link type="primary" size="small" @click="openDetail(row)">查看</el-button>
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

      <!-- 右侧: 选题审核详情 -->
      <aside class="detail-panel">
        <div class="panel-head">
          <h3>选题审核详情</h3>
          <el-icon v-if="current" class="panel-close" @click="current = null"><Close /></el-icon>
        </div>
        <el-empty v-if="!current" description="请选择左侧记录查看详情" :image-size="70" class="panel-empty" />
        <template v-else>
          <div class="panel-body">
            <div class="sec-title"><i class="sec-icon blue" />基本信息</div>
            <div class="info-rows">
              <div class="info-row"><span class="k">学生姓名</span><span class="v">{{ current.student_name || '—' }}</span></div>
              <div class="info-row"><span class="k">学号</span><span class="v">{{ current.student_no || '—' }}</span></div>
              <div class="info-row"><span class="k">题目名称</span><span class="v">{{ current.topic_title || '题目已删除' }}</span></div>
              <div class="info-row"><span class="k">申请时间</span><span class="v">{{ fmt(current.created_at) }}</span></div>
              <div class="info-row"><span class="k">审核状态</span><span class="status-tag" :class="current.statusClass">{{ current.statusCn }}</span></div>
            </div>

            <div class="sec-title"><i class="sec-icon cyan" />选题理由</div>
            <div class="content-block">{{ current.student_reason || '未填写选题理由' }}</div>

            <template v-if="current.review_comment">
              <div class="sec-title"><i class="sec-icon purple" />审核意见</div>
              <div class="content-block">{{ current.review_comment }}</div>
            </template>

            <template v-if="current.status === 'PENDING'">
              <div class="sec-title"><i class="sec-icon green" />审核意见（驳回时必填）</div>
              <el-input v-model="comment" type="textarea" :rows="4" maxlength="500" show-word-limit placeholder="请输入审核意见" />
            </template>
          </div>
          <div v-if="current.status === 'PENDING'" class="panel-footer">
            <el-button @click="current = null">取消</el-button>
            <el-button type="danger" :loading="reviewing" @click="review(false)">不通过</el-button>
            <el-button type="primary" :loading="reviewing" @click="review(true)">通过</el-button>
          </div>
        </template>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh, Select, CloseBold, Close } from '@element-plus/icons-vue'
import { reviewList } from '@/utils/reviewList'
import { selectionApi } from '@/api/business'

const route = useRoute()
const isAdmin = computed(() => route.meta.reviewer === 'admin')
const listLoader = (params: Record<string, unknown>) =>
  isAdmin.value ? selectionApi.adminList(params) : selectionApi.teacherList(params)

const CHECK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.2L6.4 11.8l1.5-1.5 2.9 2.9 5.3-5.3 1.5 1.5-6.8 6.8z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 10.9V6.5h-2v6.4l5 2.9 1-1.7-4-2.3z"/>'
const CROSS_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm3.5 13.1-1.4 1.4L12 14.4l-2.1 2.1-1.4-1.4 2.1-2.1-2.1-2.1 1.4-1.4 2.1 2.1 2.1-2.1 1.4 1.4-2.1 2.1 2.1 2.1z"/>'

const items = ref<any[]>([])
const total = ref(0)
const loading = ref(false)
const loadingText = ref('加载中…')
const query = reactive({ page: 1, page_size: 10, status: '', keyword: '', studentNo: '', topic: '', dates: [] as string[] })

const counts = reactive<Record<string, number | null>>({ all: null, approved: null, pending: null, rejected: null })
const statCards = computed(() => [
  { label: '选题申请总数', value: counts.all, icon: '<path d="M6 2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm9 1.5V8h4.5L15 3.5zM8 11h8v1.6H8V11zm0 4h8v1.6H8V15z"/>', tileBg: '#d7f0fe', iconColor: '#2563eb' },
  { label: '已通过', value: counts.approved, icon: CHECK_ICON, tileBg: '#ddfef0', iconColor: '#22c55e',
    ring: pct(counts.approved, counts.all), ringColor: '#22c55e' },
  { label: '待审核', value: counts.pending, icon: CLOCK_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b',
    ring: pct(counts.pending, counts.all), ringColor: '#f59e0b' },
  { label: '未通过', value: counts.rejected, icon: CROSS_ICON, tileBg: '#fee7ed', iconColor: '#ef4444',
    ring: pct(counts.rejected, counts.all), ringColor: '#ef4444' },
])
function pct(value: number | null, all: number | null) {
  return value !== null && all ? Math.round((value / all) * 100) : 0
}

const STATUS_CN: Record<string, string> = { PENDING: '待审核', APPROVED: '已通过', REJECTED: '未通过', CANCELLED: '已撤销' }
const STATUS_CLASS: Record<string, string> = { PENDING: 's-pending', APPROVED: 's-passed', REJECTED: 's-failed', CANCELLED: 's-withdraw' }

async function load() {
  loading.value = true
  try {
    loadingText.value = '加载中…'
    const result = await reviewList(listLoader, query)
    items.value = result.items.map((row: any) => ({
      ...row,
      statusCn: STATUS_CN[row.status] || row.status,
      statusClass: STATUS_CLASS[row.status] || 's-withdraw',
    }))
    total.value = result.total
  } finally { loading.value = false }
}

async function loadSummary() {
  const statuses = ['', 'APPROVED', 'PENDING', 'REJECTED']
  const results = await Promise.all(statuses.map(s => listLoader({ status: s || undefined, page: 1, page_size: 1 })))
  counts.all = results[0].data.total
  counts.approved = results[1].data.total
  counts.pending = results[2].data.total
  counts.rejected = results[3].data.total
}

async function refresh() { await Promise.all([load(), loadSummary()]) }

function onSearch() { query.page = 1; load() }
function onReset() {
  Object.assign(query, { page: 1, status: '', keyword: '', studentNo: '', topic: '', dates: [] })
  load()
}

const current = ref<any>(null)
const comment = ref('')
const reviewing = ref(false)
const selected = ref<any[]>([])

function onSelectionChange(sel: any[]) { selected.value = sel }

async function openDetail(row: any) {
  current.value = row
  comment.value = ''
}

function fmt(value?: string | null) { return (value || '—').replace('T', ' ').slice(0, 16) }

async function review(approve: boolean) {
  if (!approve && !comment.value.trim()) { ElMessage.warning('请填写审核意见'); return }
  reviewing.value = true
  try {
    if (isAdmin.value) {
      if (approve) await selectionApi.adminApprove(current.value.id, comment.value || undefined)
      else await selectionApi.adminReject(current.value.id, comment.value)
    } else if (approve) {
      await selectionApi.approve(current.value.id, comment.value || undefined)
    } else {
      await selectionApi.reject(current.value.id, comment.value)
    }
    ElMessage.success(approve ? '已通过' : '已驳回')
    current.value = null
    await refresh()
  } catch {
    /* 已提示 */
  } finally {
    reviewing.value = false
  }
}

async function onBatch(approve: boolean) {
  const pending = selected.value.filter(s => s.status === 'PENDING')
  if (!pending.length) { ElMessage.warning('请先勾选待审核的申请'); return }
  let commentText: string | undefined
  if (!approve) {
    const { value } = await ElMessageBox.prompt(
      `将为 ${pending.length} 条申请统一填写驳回意见（必填）`, '批量驳回',
      { inputPlaceholder: '请输入驳回意见', inputValidator: (v: string) => Boolean(v?.trim()) || '请输入驳回意见' },
    )
    commentText = value.trim()
  }
  await ElMessageBox.confirm(
    approve ? `确定批量通过选中的 ${pending.length} 条申请吗？` : `确定批量驳回选中的 ${pending.length} 条申请吗？`,
    approve ? '批量通过' : '批量驳回',
    { type: approve ? 'success' : 'warning' },
  )
  const results = await Promise.allSettled(pending.map(s =>
    isAdmin.value
      ? (approve ? selectionApi.adminApprove(s.id, commentText) : selectionApi.adminReject(s.id, commentText))
      : (approve ? selectionApi.approve(s.id, commentText) : selectionApi.reject(s.id, commentText))))
  const success = results.filter(r => r.status === 'fulfilled').length
  ElMessage[success === pending.length ? 'success' : 'warning'](
    `成功${approve ? '通过' : '驳回'} ${success}/${pending.length} 条申请`)
  await refresh()
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
.filter-row + .filter-row { margin-top: 10px; }
.filter-item { display: flex; align-items: center; gap: 8px; }
.filter-item.grow { flex: 1; min-width: 0; }
.filter-label { font-size: 13px; color: #5a6b80; white-space: nowrap; }
.filter-input { width: 170px; }
.filter-input.narrow { width: 140px; }
.filter-select { width: 132px; }
.filter-date { width: 250px; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }
.reset-btn { border: 1px solid #dcdfe6; color: #5a6b80; }

/* 表格卡 */
.table-card {
  background: #fff;
  border-radius: 8px;
  padding: 12px 14px 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.table-toolbar { display: flex; align-items: center; margin-bottom: 10px; }
.toolbar-left { display: flex; gap: 8px; }
.pass-btn { border-color: #b7e6c8; color: #22a353; }
.pass-btn:hover { border-color: #22a353; }
.reject-btn { border-color: #f6c3cb; color: #e74c3c; }
.reject-btn:hover { border-color: #e74c3c; }
.sel-table :deep(thead th) {
  background: #f0f6ff;
  color: #3b4a5f;
  font-weight: 600;
  font-size: 12px;
  padding: 7px 0;
}
.sel-table :deep(td) { font-size: 12px; color: #3b4a5f; padding: 6px 0; }
.sel-table :deep(.cell) { white-space: nowrap; padding: 0 6px; }
.sel-table :deep(.el-button + .el-button) { margin-left: 6px; }
.stu-name { color: #1f2d3d; font-weight: 600; }
.topic-title { color: #1f2d3d; font-weight: 500; }
.dash { color: #c2cbd8; }
.time-cell { display: flex; flex-direction: column; line-height: 1.5; }
.time-cell span { white-space: nowrap; }

/* 状态标签 */
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.s-pending { color: #e67e22; background: #fdf1e3; }
.s-passed { color: #27ae60; background: #e4f7ec; }
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
.sec-icon {
  width: 16px;
  height: 16px;
  border-radius: 4px;
  flex-shrink: 0;
}
.sec-icon.blue { background: #1878fd; }
.sec-icon.cyan { background: #0e7490; }
.sec-icon.green { background: #22c55e; }
.sec-icon.purple { background: #8b5cf6; }
.info-rows { margin-bottom: 16px; }
.info-row {
  display: flex;
  padding: 5px 0;
  font-size: 12.5px;
  line-height: 1.5;
}
.info-row .k { width: 72px; flex-shrink: 0; color: #8a97ad; }
.info-row .v { flex: 1; min-width: 0; color: #3b4a5f; overflow-wrap: anywhere; }
.content-block {
  background: #f8fafc;
  border-radius: 8px;
  padding: 12px 14px;
  white-space: pre-wrap;
  font-size: 13px;
  color: #303133;
  line-height: 1.7;
  margin-bottom: 16px;
}
.panel-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 16px;
  border-top: 1px solid #eef2f8;
}

@media (max-width: 1200px) {
  .review-layout { grid-template-columns: 1fr; }
  .detail-panel { min-height: 0; }
}
@media (max-width: 900px) {
  .stat-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-row { flex-wrap: wrap; }
  .filter-item { flex: 1 1 220px; min-width: 0; }
  .filter-item.grow { flex: 1 1 220px; }
  .filter-item :deep(.el-input), .filter-item :deep(.el-select), .filter-item :deep(.el-date-editor) { width: 100%; }
  .filter-actions { margin-left: 0; }
}
@media (max-width: 600px) {
  .stat-cards { grid-template-columns: minmax(0, 1fr); }
  .stat-card { min-height: 70px; align-items: center; }
  .filter-item { flex-basis: 100%; }
  .filter-actions { flex-basis: 100%; }
  .pagination-bar { flex-wrap: wrap; gap: 10px; }
  .pager { margin-left: 0; max-width: 100%; overflow-x: auto; }
  .panel-footer { flex-wrap: wrap; }
}
</style>
