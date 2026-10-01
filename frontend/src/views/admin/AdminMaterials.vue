<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { materialApi } from '@/api/business'
import { STAT_ICONS } from '@/components/statIcons'

interface MaterialFile { id: number; file_id: number; material_type: string; material_type_cn: string; original_name: string; size: number; url: string }
interface MaterialRow { id: number; student_name: string; student_no: string; college_name: string | null; major_name: string | null; teacher_name: string | null; academic_year: string | null; status: string; status_cn: string; review_comment: string | null; submitted_at: string | null; files: MaterialFile[] }
interface Option { id: number; name: string; college_id?: number }

const loading = ref(false)
const rows = ref<MaterialRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const selected = ref<MaterialRow[]>([])
const stats = ref({ total: 0, approved: 0, pending: 0, rejected: 0, not_submitted: 0 })
const filters = ref<{ years: string[]; colleges: Option[]; majors: Option[]; teachers: Option[] }>({ years: [], colleges: [], majors: [], teachers: [] })
const query = reactive({ year: '', collegeId: undefined as number | undefined, majorId: undefined as number | undefined,
  teacherId: undefined as number | undefined, student: '', materialType: '', status: '', dateRange: [] as string[] })
const applied = ref({ ...query, dateRange: [] as string[] })
const tabs = computed(() => [
  { label: '全部', value: '', count: stats.value.total + stats.value.not_submitted },
  { label: '已通过', value: 'APPROVED', count: stats.value.approved },
  { label: '审核中', value: 'SUBMITTED', count: stats.value.pending },
  { label: '未通过', value: 'REJECTED,RESUBMIT_REQUIRED', count: stats.value.rejected },
  { label: '未提交', value: 'NOT_SUBMITTED,DRAFT', count: stats.value.not_submitted },
])
const statCards = computed(() => [
  { label: '材料提交总数', value: stats.value.total, icon: STAT_ICONS.doc, color: '#2563eb', bg: '#d7f0fe' },
  { label: '已通过', value: stats.value.approved, icon: STAT_ICONS.case, color: '#22c55e', bg: '#ddfef0' },
  { label: '审核中', value: stats.value.pending, icon: STAT_ICONS.bars, color: '#f59e0b', bg: '#fef5ea' },
  { label: '未通过', value: stats.value.rejected, icon: STAT_ICONS.doc, color: '#ef4444', bg: '#fee7ed' },
  { label: '未提交（已建档）', value: stats.value.not_submitted, icon: STAT_ICONS.cap, color: '#8b5cf6', bg: '#edeafd' },
])
const majorOptions = computed(() => filters.value.majors.filter(m => !query.collegeId || m.college_id === query.collegeId))
const detailVisible = ref(false)
const detail = ref<MaterialRow | null>(null)
const reviewVisible = ref(false)
const reviewRow = ref<MaterialRow | null>(null)
const comment = ref('')
const reviewing = ref(false)

function params(extra: Record<string, unknown> = {}) {
  return { page: page.value, page_size: pageSize.value, status: applied.value.status || undefined,
    academic_year: applied.value.year || undefined, college_id: applied.value.collegeId,
    major_id: applied.value.majorId, teacher_id: applied.value.teacherId,
    student: applied.value.student || undefined, material_type: applied.value.materialType || undefined,
    from_date: applied.value.dateRange?.[0] || undefined, to_date: applied.value.dateRange?.[1] || undefined, ...extra }
}
async function load() {
  loading.value = true
  try {
    const res = await materialApi.adminList(params())
    rows.value = res.data.items || []
    total.value = res.data.total || 0
  } finally { loading.value = false }
}
async function loadStats() {
  const res = await materialApi.adminStats(query.year || undefined)
  stats.value = res.data
}
async function loadFilters() {
  const res = await materialApi.adminFilters()
  filters.value = res.data
}
async function search() {
  applied.value = { ...query, dateRange: [...query.dateRange] }
  page.value = 1
  await Promise.all([load(), loadStats()])
}
async function chooseTab(value: string) {
  query.status = value
  await search()
}
async function reset() {
  Object.assign(query, { year: '', collegeId: undefined, majorId: undefined, teacherId: undefined,
    student: '', materialType: '', status: '', dateRange: [] })
  await search()
}
watch([page, pageSize], () => { load() })
watch(() => query.collegeId, () => { query.majorId = undefined })
function openReview(row: MaterialRow) { reviewRow.value = row; comment.value = ''; reviewVisible.value = true }
function selectMobile(row: MaterialRow, checked: boolean) {
  selected.value = checked ? [...selected.value.filter(item => item.id !== row.id), row]
    : selected.value.filter(item => item.id !== row.id)
}
async function review(approve: boolean) {
  if (!reviewRow.value) return
  if (!approve && !comment.value.trim()) { ElMessage.warning('驳回时请填写审核意见'); return }
  reviewing.value = true
  try {
    if (approve) await materialApi.adminApprove(reviewRow.value.id, comment.value || undefined)
    else await materialApi.adminReject(reviewRow.value.id, comment.value)
    ElMessage.success(approve ? '审核通过' : '已驳回')
    reviewVisible.value = false
    await Promise.all([load(), loadStats()])
  } finally { reviewing.value = false }
}
async function batchApprove() {
  const pending = selected.value.filter(r => r.status === 'SUBMITTED')
  if (!pending.length) { ElMessage.warning('请先勾选待审核的材料'); return }
  await ElMessageBox.confirm(`确认通过选中的 ${pending.length} 份待审核材料？`, '批量审核', { type: 'warning' })
  for (const row of pending) await materialApi.adminApprove(row.id)
  ElMessage.success(`已审核 ${pending.length} 份材料`)
  selected.value = []
  await Promise.all([load(), loadStats()])
}
function csvCell(value: unknown) { return `"${String(value ?? '').replaceAll('"', '""')}"` }
async function exportCsv() {
  const first = await materialApi.adminList(params({ page: 1, page_size: 100 }))
  const all: MaterialRow[] = [...(first.data.items || [])]
  const pages = Math.ceil((first.data.total || 0) / 100)
  for (let p = 2; p <= pages; p++) {
    const res = await materialApi.adminList(params({ page: p, page_size: 100 }))
    all.push(...(res.data.items || []))
  }
  const lines = [['学年', '学生姓名', '学号', '学院', '专业', '指导教师', '状态', '提交时间', '文件', '审核意见'].join(',')]
  for (const row of all) lines.push([row.academic_year, row.student_name, row.student_no, row.college_name,
    row.major_name, row.teacher_name, row.status_cn, row.submitted_at,
    row.files.map(f => f.original_name).join('；'), row.review_comment].map(csvCell).join(','))
  const url = URL.createObjectURL(new Blob(['\uFEFF', lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a'); a.href = url; a.download = '毕业材料清单.csv'; a.click()
  URL.revokeObjectURL(url)
}
onMounted(async () => { await loadFilters(); await Promise.all([load(), loadStats()]) })
</script>

<template>
  <div class="materials-page">
    <div class="stat-cards">
      <div v-for="card in statCards" :key="card.label" class="stat-card">
        <div class="stat-icon" :style="{ background: card.bg, color: card.color }"><svg viewBox="0 0 24 24" fill="currentColor" v-html="card.icon" /></div>
        <div class="stat-info"><div class="stat-label">{{ card.label }}</div><div class="stat-num">{{ card.value }}</div></div>
      </div>
    </div>
    <div class="filter-card">
      <div class="filter-grid">
        <label>学年<el-select v-model="query.year" clearable placeholder="全部学年"><el-option v-for="year in filters.years" :key="year" :label="year" :value="year" /></el-select></label>
        <label>学院<el-select v-model="query.collegeId" clearable placeholder="全部学院"><el-option v-for="c in filters.colleges" :key="c.id" :label="c.name" :value="c.id" /></el-select></label>
        <label>专业<el-select v-model="query.majorId" clearable placeholder="全部专业"><el-option v-for="m in majorOptions" :key="m.id" :label="m.name" :value="m.id" /></el-select></label>
        <label>学生姓名 / 学号<el-input v-model="query.student" clearable placeholder="输入姓名或学号" /></label>
        <label>材料类型<el-select v-model="query.materialType" clearable placeholder="全部类型"><el-option label="毕业论文" value="THESIS" /><el-option label="毕业设计" value="DESIGN" /><el-option label="源代码" value="SOURCE_CODE" /><el-option label="说明文档" value="MANUAL" /><el-option label="演示视频" value="VIDEO" /><el-option label="其他" value="OTHER" /></el-select></label>
        <label>提交状态<el-select v-model="query.status" clearable placeholder="全部状态"><el-option v-for="tab in tabs.slice(1)" :key="tab.value" :label="tab.label" :value="tab.value" /></el-select></label>
        <label>指导教师<el-select v-model="query.teacherId" clearable placeholder="全部教师"><el-option v-for="t in filters.teachers" :key="t.id" :label="t.name" :value="t.id" /></el-select></label>
        <label>提交时间<el-date-picker v-model="query.dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" /></label>
      </div>
      <div class="filter-actions"><el-button type="primary" @click="search">查询</el-button><el-button @click="reset">重置</el-button></div>
    </div>
    <div class="table-card">
      <div class="tab-bar"><div class="tabs"><button v-for="tab in tabs" :key="tab.label" class="tab" :class="{ active: applied.status === tab.value }" @click="chooseTab(tab.value)">{{ tab.label }}（{{ tab.count }}）</button></div><div class="tab-actions"><el-button @click="exportCsv">导出数据</el-button><el-button @click="batchApprove">批量审核</el-button></div></div>
      <el-table v-loading="loading" :data="rows" class="mat-table" @selection-change="(value: MaterialRow[]) => selected = value" empty-text="暂无符合条件的材料记录">
        <el-table-column type="selection" width="42" /><el-table-column type="index" label="序号" width="55" :index="(i: number) => (page - 1) * pageSize + i + 1" />
        <el-table-column prop="student_name" label="学生姓名" min-width="90" /><el-table-column prop="student_no" label="学号" min-width="105" />
        <el-table-column prop="college_name" label="学院" min-width="110" /><el-table-column prop="major_name" label="专业" min-width="130" />
        <el-table-column label="材料类型" min-width="95"><template #default="{ row }">{{ row.files.map((f: MaterialFile) => f.material_type_cn).join('、') || '未上传' }}</template></el-table-column>
        <el-table-column label="文件名称" min-width="180" show-overflow-tooltip><template #default="{ row }">{{ row.files.map((f: MaterialFile) => f.original_name).join('、') || '—' }}</template></el-table-column>
        <el-table-column label="提交时间" min-width="140"><template #default="{ row }">{{ row.submitted_at?.replace('T', ' ').slice(0, 16) || '—' }}</template></el-table-column>
        <el-table-column label="审核状态" min-width="100"><template #default="{ row }"><span class="status-tag" :class="row.status === 'APPROVED' ? 's-passed' : row.status === 'SUBMITTED' ? 's-pending' : row.status === 'DRAFT' ? 's-missing' : 's-failed'">{{ row.status_cn }}</span></template></el-table-column>
        <el-table-column prop="review_comment" label="评审意见" min-width="130" show-overflow-tooltip />
        <el-table-column label="操作" width="150" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="detail = row; detailVisible = true">查看</el-button><el-button v-if="row.status === 'SUBMITTED'" link type="primary" @click="openReview(row)">审核</el-button></template></el-table-column>
      </el-table>
      <div class="material-mobile-list">
        <div v-if="!rows.length" class="mobile-empty">暂无符合条件的材料记录</div>
        <article v-for="row in rows" :key="row.id" class="material-mobile-item">
          <div class="mobile-top"><el-checkbox :model-value="selected.some(item => item.id === row.id)" :disabled="row.status !== 'SUBMITTED'" @change="(checked: string | number | boolean) => selectMobile(row, Boolean(checked))" /><strong>{{ row.student_name }} <small>{{ row.student_no }}</small></strong><span class="status-tag" :class="row.status === 'APPROVED' ? 's-passed' : row.status === 'SUBMITTED' ? 's-pending' : row.status === 'DRAFT' ? 's-missing' : 's-failed'">{{ row.status_cn }}</span></div>
          <p class="mobile-meta">{{ row.college_name || '学院未设置' }} · {{ row.major_name || '专业未设置' }} · {{ row.academic_year || '学年未设置' }}</p>
          <p class="mobile-files">{{ row.files.map(f => f.original_name).join('、') || '暂无文件' }}</p>
          <p class="mobile-meta">提交：{{ row.submitted_at?.replace('T', ' ').slice(0, 16) || '未提交' }}</p>
          <div class="mobile-actions"><el-button link type="primary" @click="detail = row; detailVisible = true">查看材料</el-button><el-button v-if="row.status === 'SUBMITTED'" link type="primary" @click="openReview(row)">审核</el-button></div>
        </article>
      </div>
      <div class="pagination-bar"><span>共 {{ total }} 条数据</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10, 20, 50]" layout="sizes, prev, pager, next" /></div>
    </div>
    <el-dialog v-model="detailVisible" :title="`${detail?.student_name || '学生'}的毕业材料`" width="620px">
      <el-descriptions :column="1" border><el-descriptions-item label="学号">{{ detail?.student_no }}</el-descriptions-item><el-descriptions-item label="指导教师">{{ detail?.teacher_name || '—' }}</el-descriptions-item><el-descriptions-item label="状态">{{ detail?.status_cn }}</el-descriptions-item><el-descriptions-item label="审核意见">{{ detail?.review_comment || '—' }}</el-descriptions-item></el-descriptions>
      <h4>材料文件</h4><div v-if="!detail?.files.length">暂无文件</div><div v-for="file in detail?.files || []" :key="file.id" class="detail-file"><span>{{ file.material_type_cn }} · {{ file.original_name }}</span><a :href="file.url" target="_blank" rel="noopener">下载</a></div>
    </el-dialog>
    <el-dialog v-model="reviewVisible" :title="`审核 ${reviewRow?.student_name || ''} 的毕业材料`" width="520px"><el-input v-model="comment" type="textarea" :rows="4" placeholder="审核意见（驳回时必填）" /><template #footer><el-button :loading="reviewing" @click="review(false)">驳回</el-button><el-button type="primary" :loading="reviewing" @click="review(true)">通过</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.materials-page { min-width: 0; }
.stat-cards { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin-bottom:12px; }
.stat-card, .filter-card, .table-card { background:#fff; border-radius:8px; box-shadow:0 1px 4px rgba(15,46,93,.06); }
.stat-card { display:flex; align-items:center; gap:12px; min-width:0; padding:16px; }
.stat-icon { width:42px; height:42px; border-radius:10px; flex-shrink:0; display:grid; place-items:center; }
.stat-icon svg { width:22px; height:22px; }
.stat-label { color:#8a97ad; font-size:12px; }
.stat-num { font-size:24px; font-weight:700; color:#1f2d3d; }
.filter-card { padding:18px; margin-bottom:12px; }
.filter-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }
.filter-grid label { display:grid; gap:7px; min-width:0; font-size:13px; color:#3b4a5f; }
.filter-grid :deep(.el-select), .filter-grid :deep(.el-input), .filter-grid :deep(.el-date-editor) { width:100%; min-width:0; }
.filter-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:16px; }
.table-card { padding:14px; min-width:0; }
.tab-bar { display:flex; align-items:center; justify-content:space-between; gap:12px; border-bottom:1px solid #ebeef5; margin-bottom:8px; flex-wrap:wrap; }
.tabs { display:flex; gap:18px; overflow-x:auto; }
.tab { border:0; background:transparent; white-space:nowrap; cursor:pointer; color:#5a6b80; padding:8px 2px 10px; }
.tab.active { color:#1878fd; border-bottom:2px solid #1878fd; font-weight:600; }
.tab-actions { display:flex; gap:8px; }
.mat-table :deep(thead th) { background:#f0f6ff; color:#2c3e5d; font-size:12px; }
.mat-table :deep(td) { font-size:12px; }
.status-tag { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; white-space:nowrap; }
.s-pending { color:#e67e22; background:#fdf1e3; }.s-passed { color:#27ae60; background:#e4f7ec; }.s-failed { color:#e74c3c; background:#fdeaea; }.s-missing { color:#8b5cf6; background:#ede9fe; }
.pagination-bar { display:flex; justify-content:space-between; align-items:center; gap:12px; padding-top:12px; color:#8a97a8; font-size:12px; flex-wrap:wrap; }
.detail-file { display:flex; justify-content:space-between; gap:12px; padding:10px 0; border-bottom:1px solid #eef1f6; overflow-wrap:anywhere; }
.detail-file a { flex-shrink:0; }
.material-mobile-list { display:none; }
@media (max-width:1100px) { .stat-cards { grid-template-columns:repeat(2,minmax(0,1fr)); } .filter-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:640px) {
  .stat-cards, .filter-grid { grid-template-columns:minmax(0,1fr); }
  .tab-actions { width:100%; }.filter-actions { flex-wrap:wrap; }
  .mat-table { display:none; }.material-mobile-list { display:grid; gap:12px; }
  .material-mobile-item { border:1px solid #e5eaf2; border-radius:8px; padding:14px; min-width:0; }
  .mobile-top { display:flex; align-items:center; gap:8px; }.mobile-top strong { flex:1; min-width:0; overflow-wrap:anywhere; }
  .mobile-top small, .mobile-meta { color:#8995a7; font-weight:400; font-size:12px; }
  .mobile-top .status-tag { flex-shrink:0; }.mobile-files { overflow-wrap:anywhere; line-height:1.5; }
  .mobile-actions { display:flex; gap:12px; }.mobile-empty { color:#8995a7; text-align:center; padding:20px; }
}
</style>
