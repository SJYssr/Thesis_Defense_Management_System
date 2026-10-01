<template>
  <div class="topics-page">
    <!-- 面包屑 -->
    <div class="breadcrumb">
      <span class="crumb">毕业设计管理</span>
      <span class="sep">/</span>
      <span class="crumb current">题目信息</span>
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
        </div>
      </div>
    </div>

    <!-- 筛选区 -->
    <div class="filter-card">
      <div class="filter-row">
        <div class="filter-item grow">
          <span class="filter-label">题目名称</span>
          <el-input v-model="query.keyword" placeholder="请输入题目名称" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">所属学院</span>
          <el-select v-model="query.college_id" class="filter-select" clearable placeholder="全部学院" @change="query.major_id = null">
            <el-option v-for="c in collegeOptions" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">所属专业</span>
          <el-select v-model="query.major_id" class="filter-select" clearable placeholder="全部专业">
            <el-option v-for="m in filteredMajors" :key="m.id" :label="m.name" :value="m.id" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">指导教师</span>
          <el-input v-model="query.teacher_name" placeholder="请输入教师姓名" clearable class="filter-input narrow" />
        </div>
      </div>
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">题目类型</span>
          <el-select v-model="query.topic_type" class="filter-select" clearable placeholder="全部类型">
            <el-option v-for="t in typeOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">题目状态</span>
          <el-select v-model="query.status" class="filter-select" clearable placeholder="全部状态">
            <el-option label="已发布" value="PUBLISHED" />
            <el-option label="待发布" value="DRAFT" />
            <el-option label="已关闭" value="CLOSED" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">发布时间</span>
          <el-date-picker
            v-model="query.dateRange" type="daterange" range-separator="至"
            start-placeholder="开始日期" end-placeholder="结束日期"
            value-format="YYYY-MM-DD" class="filter-date"
          />
        </div>
        <div class="filter-actions">
          <el-button type="primary" @click="onSearch">查询</el-button>
          <el-button @click="onReset">重置</el-button>
        </div>
      </div>
    </div>

    <!-- 表格卡 -->
    <div class="table-card">
      <div class="table-toolbar">
        <div class="toolbar-left">
          <el-button type="primary" @click="openCreate">新增题目</el-button>
          <el-button type="success" @click="onBatchPublish">批量发布</el-button>
          <el-button type="danger" @click="onBatchDelete">批量删除</el-button>
        </div>
        <el-button class="export-btn" @click="onExport">
          <el-icon><Download /></el-icon>&nbsp;导出数据
        </el-button>
      </div>

      <el-table v-loading="loading" :data="pagedRows" class="topic-table" empty-text="暂无符合条件的题目" @selection-change="onSelectionChange">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="index" label="序号" width="46" align="center" />
        <el-table-column prop="title" label="题目名称" min-width="230">
          <template #default="{ row }">
            <span class="topic-title">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="college" label="所属学院" width="96" align="center" />
        <el-table-column prop="major" label="所属专业" width="140" align="center" />
        <el-table-column label="题目类型" width="84" align="center">
          <template #default="{ row }">
            <span class="type-tag" :class="row.typeClass">{{ row.type }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="teacher" label="指导教师" width="80" align="center" />
        <el-table-column prop="expect" label="预计人数" width="68" align="center" />
        <el-table-column prop="chosen" label="已选人数" width="68" align="center" />
        <el-table-column label="题目状态" width="84" align="center">
          <template #default="{ row }">
            <span class="status-tag" :class="row.statusClass">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="publishTime" label="发布时间" width="120" align="center" />
        <el-table-column label="操作" width="138" align="center">
          <template #default="{ row }">
            <el-button v-if="row.rawStatus !== 'CLOSED'" link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" size="small" @click="viewDetail(row)">查看</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-bar">
        <span class="page-total">共 {{ total }} 条数据</span>
        <el-select v-model="pageSize" size="small" class="page-size">
          <el-option label="10条/页" :value="10" />
          <el-option label="20条/页" :value="20" />
        </el-select>
        <el-pagination
          v-model:current-page="page" :page-size="pageSize" :total="total"
          background layout="prev, pager, next, jumper" class="pager" @current-change="load"
        />
      </div>
    </div>

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑题目' : '新增题目'" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="题目名称" required>
          <el-input v-model="form.title" placeholder="请输入题目名称" />
        </el-form-item>
        <el-form-item label="题目描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="请输入题目描述" />
        </el-form-item>
        <el-form-item label="选题要求">
          <el-input v-model="form.requirements" type="textarea" :rows="2" placeholder="请输入选题要求" />
        </el-form-item>
        <el-form-item label="所属学院">
          <el-select v-model="form.college_id" style="width: 100%" placeholder="请选择学院" @change="form.major_id = null">
            <el-option v-for="c in collegeOptions" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属专业">
          <el-select v-model="form.major_id" style="width: 100%" placeholder="请选择专业">
            <el-option v-for="m in formMajors" :key="m.id" :label="m.name" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="题目类型" required>
          <el-select v-model="form.type" style="width: 100%">
            <el-option v-for="t in typeOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="指导教师" required>
          <el-select v-model="form.teacher_id" style="width: 100%" placeholder="请选择教师">
            <el-option v-for="t in teacherOptions" :key="t.profile_id" :label="t.real_name" :value="t.profile_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="预计人数">
          <el-input-number v-model="form.expect" :min="1" :max="5" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveForm">确定</el-button>
      </template>
    </el-dialog>

    <!-- 查看详情 -->
    <el-dialog v-model="detailVisible" :title="detail?.title" width="620px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="所属学院">{{ detail?.college }}</el-descriptions-item>
        <el-descriptions-item label="所属专业">{{ detail?.major }}</el-descriptions-item>
        <el-descriptions-item label="题目类型">{{ detail?.type }}</el-descriptions-item>
        <el-descriptions-item label="指导教师">{{ detail?.teacher }}</el-descriptions-item>
        <el-descriptions-item label="预计人数">{{ detail?.expect }}</el-descriptions-item>
        <el-descriptions-item label="已选人数">{{ detail?.chosen }}</el-descriptions-item>
        <el-descriptions-item label="题目状态">{{ detail?.status }}</el-descriptions-item>
        <el-descriptions-item label="发布时间">{{ detail?.publishTime }}</el-descriptions-item>
      </el-descriptions>
      <h4 style="margin: 16px 0 8px">题目描述</h4>
      <p class="detail-text">{{ detail?.description || '暂无描述' }}</p>
      <h4 style="margin: 16px 0 8px">选题要求</h4>
      <p class="detail-text">{{ detail?.requirements || '暂无要求' }}</p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { STAT_ICONS } from '@/components/statIcons'
import { topicApi } from '@/api/business'
import { userAdminApi } from '@/api/common'

interface CollegeOption { id: number; name: string }
interface MajorOption { id: number; name: string; college_id: number }
interface TeacherOption { profile_id: number; real_name: string }
interface TopicItem {
  id: number; title: string; description: string | null; requirements: string | null
  topic_type: string | null; major_id: number | null; major_name: string | null
  college_name: string | null; teacher_id: number; teacher_name: string | null
  max_students: number; approved_count: number; status: string; publish_time: string | null
}
interface TopicRow extends TopicItem {
  index: number; college: string; major: string; type: string; teacher: string
  expect: number; chosen: number; publishTime: string; statusClass: string; typeClass: string; rawStatus: string
}
const TYPE_CLASS: Record<string, string> = {
  软件开发: 't-blue', 硬件设计: 't-cyan', 算法研究: 't-green', 应用研究: 't-purple', 其他: 't-gray',
}
const typeOptions = computed(() => [...new Set([...Object.keys(TYPE_CLASS), ...rows.value.map(r => r.topic_type).filter((v): v is string => Boolean(v))])])
const rows = ref<TopicItem[]>([])
const collegeOptions = ref<CollegeOption[]>([])
const majorOptions = ref<MajorOption[]>([])
const teacherOptions = ref<TeacherOption[]>([])
const summary = reactive({ total: 0, published: 0, draft: 0, teacher_count: 0, major_count: 0 })
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const saving = ref(false)
const selected = ref<TopicRow[]>([])
const query = reactive({
  keyword: '', college_id: null as number | null, major_id: null as number | null,
  teacher_name: '', topic_type: '', status: '', dateRange: null as [string, string] | null,
})
const filteredMajors = computed(() => majorOptions.value.filter(m => !query.college_id || m.college_id === query.college_id))
const statCards = computed(() => [
  { label: '题目总数', value: summary.total, icon: STAT_ICONS.doc, color: '#2563eb', bg: '#d7f0fe' },
  { label: '已发布题目', value: summary.published, icon: STAT_ICONS.doc, color: '#22c55e', bg: '#ddfef0' },
  { label: '待发布题目', value: summary.draft, icon: STAT_ICONS.doc, color: '#f59e0b', bg: '#fef5ea' },
  { label: '关联教师数', value: summary.teacher_count, icon: STAT_ICONS.person, color: '#ef4444', bg: '#fee7ed' },
  { label: '涉及专业数', value: summary.major_count, icon: STAT_ICONS.doc, color: '#8b5cf6', bg: '#edeafd' },
])
const pagedRows = computed<TopicRow[]>(() => rows.value.map((r, i) => ({
  ...r, index: (page.value - 1) * pageSize.value + i + 1,
  college: r.college_name || '—', major: r.major_name || '—',
  type: r.topic_type || '其他', teacher: r.teacher_name || '—',
  expect: r.max_students, chosen: r.approved_count,
  rawStatus: r.status,
  status: r.status === 'PUBLISHED' ? '已发布' : r.status === 'DRAFT' ? '待发布' : '已关闭',
  publishTime: r.publish_time?.slice(0, 10) || '—',
  typeClass: TYPE_CLASS[r.topic_type || ''] || 't-gray',
  statusClass: r.status === 'DRAFT' ? 's-pending' : 's-published',
})))

function filterParams() {
    const end = query.dateRange?.[1] ? new Date(query.dateRange[1] + 'T00:00:00Z') : null
    if (end) end.setUTCDate(end.getUTCDate() + 1)
    return {
      keyword: query.keyword || undefined, college_id: query.college_id || undefined,
      major_id: query.major_id || undefined, teacher_name: query.teacher_name || undefined,
      topic_type: query.topic_type || undefined, status: query.status || undefined,
      published_from: query.dateRange?.[0] || undefined,
      published_to: end ? end.toISOString().slice(0, 10) : undefined,
    }
}
async function load() {
  loading.value = true
  try {
    const res = await topicApi.adminList({ page: page.value, page_size: pageSize.value, ...filterParams() })
    rows.value = res.data.items
    total.value = res.data.total
  } finally { loading.value = false }
}
async function loadSummary() {
  const res = await topicApi.adminSummary()
  Object.assign(summary, res.data)
}
async function refresh() { await Promise.all([load(), loadSummary()]) }
function onSearch() { page.value = 1; load() }
function onReset() {
  Object.assign(query, { keyword: '', college_id: null, major_id: null, teacher_name: '',
                         topic_type: '', status: '', dateRange: null })
  onSearch()
}
function onSelectionChange(sel: TopicRow[]) { selected.value = sel }

const dialogVisible = ref(false)
const editing = ref(false)
const form = reactive({
  id: 0, title: '', description: '', requirements: '', college_id: null as number | null,
  major_id: null as number | null, type: '软件开发', teacher_id: null as number | null, expect: 1,
})
const formMajors = computed(() => majorOptions.value.filter(m => !form.college_id || m.college_id === form.college_id))
function openCreate() {
  editing.value = false
  Object.assign(form, { id: 0, title: '', description: '', requirements: '', college_id: null,
                        major_id: null, type: '软件开发', teacher_id: null, expect: 1 })
  dialogVisible.value = true
}
function openEdit(row: TopicRow) {
  editing.value = true
  const major = majorOptions.value.find(m => m.id === row.major_id)
  Object.assign(form, { id: row.id, title: row.title, description: row.description || '',
    requirements: row.requirements || '', college_id: major?.college_id || null,
    major_id: row.major_id, type: row.type, teacher_id: row.teacher_id, expect: row.expect })
  dialogVisible.value = true
}
async function saveForm() {
  if (!form.title.trim() || !form.teacher_id) { ElMessage.warning('请填写题目名称并选择指导教师'); return }
  saving.value = true
  try {
    const body = { title: form.title.trim(), description: form.description, requirements: form.requirements,
      topic_type: form.type, major_id: form.major_id, teacher_id: form.teacher_id, max_students: form.expect }
    if (editing.value) await topicApi.adminUpdate(form.id, body)
    else await topicApi.adminCreate(body)
    ElMessage.success(editing.value ? '题目已更新' : '题目草稿已创建')
    dialogVisible.value = false
    await refresh()
  } finally { saving.value = false }
}
const detailVisible = ref(false)
const detail = ref<TopicRow | null>(null)
function viewDetail(row: TopicRow) { detail.value = row; detailVisible.value = true }
async function onDelete(row: TopicRow) {
  await ElMessageBox.confirm(`确定删除题目「${row.title}」吗？`, '提示', { type: 'warning' })
  await topicApi.adminRemove(row.id)
  ElMessage.success('删除成功')
  await refresh()
}
async function onBatchPublish() {
  const drafts = selected.value.filter(s => s.rawStatus === 'DRAFT')
  if (!drafts.length) { ElMessage.warning('请先勾选待发布题目'); return }
  await ElMessageBox.confirm(`确定发布 ${drafts.length} 个题目吗？`, '批量发布', { type: 'info' })
  const results = await Promise.allSettled(drafts.map(s => topicApi.adminPublish(s.id)))
  const success = results.filter(r => r.status === 'fulfilled').length
  ElMessage[success === drafts.length ? 'success' : 'warning'](`成功发布 ${success}/${drafts.length} 个题目`)
  await refresh()
}
async function onBatchDelete() {
  if (!selected.value.length) { ElMessage.warning('请先勾选要删除的题目'); return }
  await ElMessageBox.confirm(`确定删除选中的 ${selected.value.length} 个题目吗？`, '提示', { type: 'warning' })
  const results = await Promise.allSettled(selected.value.map(s => topicApi.adminRemove(s.id)))
  const success = results.filter(r => r.status === 'fulfilled').length
  ElMessage[success === selected.value.length ? 'success' : 'warning'](`成功删除 ${success}/${selected.value.length} 个题目`)
  await refresh()
}
async function onExport() {
  const all: TopicItem[] = []
  for (let current = 1; current <= Math.ceil(total.value / 100); current++) {
    const res = await topicApi.adminList({ page: current, page_size: 100, ...filterParams() })
    all.push(...res.data.items)
  }
  const quote = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`
  const csv = [['题目名称', '学院', '专业', '类型', '教师', '人数上限', '已选人数', '状态', '发布时间'],
    ...all.map(r => [r.title, r.college_name, r.major_name, r.topic_type, r.teacher_name,
                     r.max_students, r.approved_count, r.status, r.publish_time])].map(line => line.map(quote).join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url; a.download = 'topics.csv'; a.click(); URL.revokeObjectURL(url)
}
async function loadTeachers() {
  const teachers: TeacherOption[] = []
  for (let current = 1; ; current++) {
    const res = await userAdminApi.list('TEACHER', { page: current, page_size: 100, status: 1 })
    teachers.push(...res.data.items.filter((t: TeacherOption) => t.profile_id))
    if (current * 100 >= res.data.total) break
  }
  return teachers
}
onMounted(async () => {
  const [colleges, majors, teachers] = await Promise.all([
    userAdminApi.colleges(), userAdminApi.majors(), loadTeachers(),
  ])
  collegeOptions.value = colleges.data
  majorOptions.value = majors.data
  teacherOptions.value = teachers
  await refresh()
})
</script>

<style scoped>
/* 面包屑 */
.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 13px;
}
.crumb { color: #8a97ad; }
.crumb.current { color: #3b4a5f; font-weight: 500; }
.sep { color: #c2cbd8; }

/* 统计卡 */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}
.stat-card {
  background: #fff;
  border-radius: 8px;
  padding: 12px 10px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
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

/* 筛选区 */
.filter-card {
  background: #fff;
  border-radius: 8px;
  padding: 12px 14px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  margin-bottom: 12px;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 14px;
}
.filter-row + .filter-row { margin-top: 10px; }
.filter-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-item.grow { flex: 1; min-width: 0; }
.filter-label {
  font-size: 13px;
  color: #5a6b80;
  white-space: nowrap;
}
.filter-input { width: 220px; }
.filter-input.narrow { width: 150px; }
.filter-select { width: 132px; }
.filter-date { width: 250px; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }

/* 表格卡 */
.table-card {
  background: #fff;
  border-radius: 8px;
  padding: 12px 14px 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.toolbar-left { display: flex; gap: 8px; }
.export-btn { border: 1px solid #dcdfe6; color: #5a6b80; }
.topic-table :deep(thead th) {
  background: #f0f6ff;
  color: #3b4a5f;
  font-weight: 600;
  font-size: 12px;
  padding: 7px 0;
}
.topic-table :deep(td) {
  font-size: 12px;
  color: #3b4a5f;
  padding: 5px 0;
}
.topic-table :deep(.cell) { white-space: nowrap; padding: 0 4px; }
.topic-table :deep(.el-button + .el-button) { margin-left: 6px; }
.topic-title {
  color: #1f2d3d;
  font-weight: 500;
}

/* 类型标签 */
.type-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 3px;
  font-size: 11px;
  line-height: 18px;
}
.t-blue { color: #1878fd; background: #e3effe; }
.t-cyan { color: #0e7490; background: #dcf5fb; }
.t-green { color: #0e9f6e; background: #def7ec; }
.t-purple { color: #7c3aed; background: #ede9fe; }
.t-gray { color: #64748b; background: #eef1f5; }

/* 状态标签 */
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 3px;
  font-size: 11px;
  line-height: 18px;
}
.s-pending { color: #e67e22; background: #fdf1e3; }
.s-published { color: #27ae60; background: #e4f7ec; }

/* 分页 */
.pagination-bar {
  display: flex;
  align-items: center;
  padding: 10px 4px 2px;
}
.page-total {
  font-size: 12px;
  color: #8a97a8;
  margin-right: 12px;
}
.page-size { width: 96px; margin-right: 12px; }
.pager { margin-left: auto; }

.detail-text {
  white-space: pre-wrap;
  color: #606266;
  background: #f8fafc;
  border-radius: 6px;
  padding: 12px;
  margin: 0;
  line-height: 1.7;
}
@media (max-width: 900px) {
  .stat-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-row { flex-wrap: wrap; }
  .filter-item { flex: 1 1 220px; min-width: 0; }
  .filter-item.grow { flex: 1 1 220px; }
  .filter-item :deep(.el-input), .filter-item :deep(.el-select), .filter-item :deep(.el-date-editor) { width: 100%; }
  .table-toolbar { flex-wrap: wrap; gap: 10px; }
  .toolbar-left { flex-wrap: wrap; }
}
@media (max-width: 600px) {
  .stat-cards { grid-template-columns: minmax(0, 1fr); }
  .stat-card { min-height: 70px; align-items: center; }
  .filter-item { flex-basis: 100%; }
  .filter-actions { margin-left: 0; }
  .table-card { overflow: hidden; }
  .pagination-bar { flex-wrap: wrap; gap: 10px; }
  .pager { margin-left: 0; max-width: 100%; overflow-x: auto; }
}
</style>
