<template>
  <div class="topics-page">
    <!-- 统计卡 -->
    <div class="stat-cards">
      <div v-for="c in statCards" :key="c.label" class="stat-card">
        <div class="stat-icon" :style="{ background: c.tileBg, color: c.iconColor }">
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

    <!-- 主体两栏 -->
    <div class="topic-layout">
      <div class="topic-main">
        <!-- 筛选区 -->
        <div class="filter-card">
          <div class="filter-row">
            <div class="filter-item grow">
              <span class="filter-label">题目名称</span>
              <el-input v-model="query.keyword" placeholder="请输入题目名称" clearable class="filter-input" @keyup.enter="onSearch" />
            </div>
            <div class="filter-item">
              <span class="filter-label">题目方向</span>
              <el-select v-model="query.topicType" class="filter-select" clearable placeholder="全部方向">
                <el-option v-for="t in typeOptions" :key="t" :label="t" :value="t" />
              </el-select>
            </div>
            <div class="filter-item">
              <span class="filter-label">发布状态</span>
              <el-select v-model="query.status" class="filter-select" clearable placeholder="全部状态">
                <el-option label="已发布" value="PUBLISHED" />
                <el-option label="待发布" value="DRAFT" />
                <el-option label="已关闭" value="CLOSED" />
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
          <div class="table-toolbar">
            <div class="toolbar-left">
              <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新增题目</el-button>
              <el-button type="success" @click="onBatchPublish"><el-icon><VideoPlay /></el-icon>&nbsp;批量发布</el-button>
              <el-button type="warning" plain @click="onBatchClose">批量下架</el-button>
              <el-button type="danger" plain @click="onBatchDelete"><el-icon><Delete /></el-icon>&nbsp;批量删除</el-button>
            </div>
          </div>

          <el-table v-loading="loading" :data="pagedRows" class="topic-table" empty-text="暂无符合条件的题目" @selection-change="onSelectionChange">
            <el-table-column type="selection" width="40" />
            <el-table-column prop="index" label="序号" width="48" align="center" />
            <el-table-column prop="title" label="题目名称" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="topic-title">{{ row.title }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="topic_type" label="题目方向" width="120" align="center">
              <template #default="{ row }">
                <span v-if="row.topic_type" class="type-tag" :class="row.typeClass">{{ row.topic_type }}</span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="选题人数" width="88" align="center">
              <template #default="{ row }">{{ row.approved_count }}/{{ row.max_students }}</template>
            </el-table-column>
            <el-table-column label="待审申请" width="84" align="center">
              <template #default="{ row }">
                <el-badge v-if="row.pending_count" :value="row.pending_count" type="warning" />
                <span v-else class="muted">0</span>
              </template>
            </el-table-column>
            <el-table-column label="发布状态" width="90" align="center">
              <template #default="{ row }">
                <span class="status-tag" :class="row.statusClass">{{ row.statusCn }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="createdAt" label="创建时间" width="150" align="center" sortable sort-by="createdAt" />
            <el-table-column label="操作" width="150" align="center" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.status !== 'CLOSED'" link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
                <el-button link type="primary" size="small" @click="viewDetail(row)">详情</el-button>
                <el-dropdown trigger="hover" class="more-drop">
                  <el-button link type="primary" size="small">更多<el-icon><ArrowDown /></el-icon></el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item v-if="row.status === 'DRAFT'" @click="onPublish(row)">发布</el-dropdown-item>
                      <el-dropdown-item v-if="row.status === 'PUBLISHED'" @click="onClose(row)">下架</el-dropdown-item>
                      <el-dropdown-item v-if="row.status !== 'PUBLISHED'" @click="onDelete(row)">删除</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-bar">
            <span class="page-total">共 {{ filteredTotal }} 条数据</span>
            <el-select v-model="pageSize" size="small" class="page-size">
              <el-option label="10条/页" :value="10" />
              <el-option label="20条/页" :value="20" />
            </el-select>
            <el-pagination
              v-model:current-page="page" :page-size="pageSize" :total="filteredTotal"
              background layout="prev, pager, next, jumper" class="pager"
            />
          </div>
        </div>
      </div>

    </div>

    <!-- 新增/编辑题目: 弹窗（不再常驻右侧占位） -->
    <el-dialog v-model="formVisible" :title="form.id ? '编辑题目' : '新增题目'" width="640px"
               append-to-body destroy-on-close @closed="resetForm">
        <div class="panel-body">
          <div class="form-item">
            <label class="form-label required">题目名称</label>
            <el-input v-model="form.title" type="textarea" :rows="2" maxlength="100" show-word-limit placeholder="请输入题目名称" />
          </div>
          <div class="form-item">
            <label class="form-label required">题目方向</label>
            <el-select v-model="form.topic_type" filterable allow-create default-first-option placeholder="请选择题目方向" style="width: 100%">
              <el-option v-for="t in TYPE_PRESETS" :key="t" :label="t" :value="t" />
            </el-select>
          </div>
          <div class="form-item">
            <label class="form-label required">题目简介</label>
            <el-input v-model="form.description" type="textarea" :rows="4" maxlength="500" show-word-limit placeholder="请输入题目简介" />
          </div>
          <div class="form-item">
            <label class="form-label required">研究要求</label>
            <el-input v-model="form.requirements" type="textarea" :rows="4" maxlength="500" show-word-limit placeholder="请输入研究要求" />
          </div>
          <div class="form-row2">
            <div class="form-item">
              <label class="form-label">难度</label>
              <el-select v-model="form.difficulty" style="width: 100%">
                <el-option label="简单" value="EASY" />
                <el-option label="中等" value="MEDIUM" />
                <el-option label="困难" value="HARD" />
              </el-select>
            </div>
            <div class="form-item">
              <label class="form-label">最大名额</label>
              <el-input-number v-model="form.max_students" :min="1" :max="10" style="width: 100%" />
            </div>
          </div>
          <div v-if="!form.id" class="form-item">
            <label class="form-label required">发布状态</label>
            <el-radio-group v-model="form.publishNow">
              <el-radio value="now">立即发布</el-radio>
              <el-radio value="draft">保存为草稿</el-radio>
            </el-radio-group>
          </div>
          <div v-else class="form-item">
            <label class="form-label">当前状态</label>
            <span class="status-tag" :class="editingStatusClass">{{ editingStatusCn }}</span>
          </div>
        </div>
        <div class="panel-footer">
          <el-button @click="formVisible = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">确定</el-button>
        </div>
    </el-dialog>

    <!-- 查看详情 -->
    <el-dialog v-model="detailVisible" :title="detail?.title" width="620px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="题目方向">{{ detail?.topic_type || '—' }}</el-descriptions-item>
        <el-descriptions-item label="难度">{{ detail?.difficultyCn }}</el-descriptions-item>
        <el-descriptions-item label="选题人数">{{ detail?.approved_count }}/{{ detail?.max_students }}</el-descriptions-item>
        <el-descriptions-item label="待审申请">{{ detail?.pending_count }}</el-descriptions-item>
        <el-descriptions-item label="发布状态">{{ detail?.statusCn }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ detail?.createdAt }}</el-descriptions-item>
      </el-descriptions>
      <h4 class="detail-sub">题目简介</h4>
      <p class="detail-text">{{ detail?.description || '暂无简介' }}</p>
      <h4 class="detail-sub">研究要求</h4>
      <p class="detail-text">{{ detail?.requirements || '暂无要求' }}</p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh, Plus, VideoPlay, Delete, ArrowDown } from '@element-plus/icons-vue'
import { STAT_ICONS } from '@/components/statIcons'
import { topicApi } from '@/api/business'

interface TopicItem {
  id: number; title: string; description: string | null; requirements: string | null
  topic_type: string | null; difficulty: string | null; max_students: number
  approved_count: number; pending_count: number; status: string
  publish_time: string | null; created_at: string | null
}
interface TopicRow extends TopicItem {
  index: number; statusCn: string; statusClass: string; typeClass: string
  difficultyCn: string; createdAt: string
}

const CHECK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.2L6.4 11.8l1.5-1.5 2.9 2.9 5.3-5.3 1.5 1.5-6.8 6.8z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 10.9V6.5h-2v6.4l5 2.9 1-1.7-4-2.3z"/>'
const CROSS_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm3.5 13.1-1.4 1.4L12 14.4l-2.1 2.1-1.4-1.4 2.1-2.1-2.1-2.1 1.4-1.4 2.1 2.1 2.1-2.1 1.4 1.4-2.1 2.1 2.1 2.1z"/>'

const TYPE_CLASS: Record<string, string> = {
  软件开发: 't-blue', 硬件设计: 't-cyan', 算法研究: 't-green', 应用研究: 't-purple', 其他: 't-gray',
}
const TYPE_PRESETS = Object.keys(TYPE_CLASS).filter(t => t !== '其他')
const DIFF_CN: Record<string, string> = { EASY: '简单', MEDIUM: '中等', HARD: '困难' }

const rows = ref<TopicItem[]>([])
const loading = ref(false)
const saving = ref(false)
const page = ref(1)
const pageSize = ref(10)
const selected = ref<TopicRow[]>([])
const summary = reactive({ total: 0, published: 0, draft: 0, closed: 0 })
const query = reactive({ keyword: '', topicType: '', status: '' })

const statCards = computed(() => [
  { label: '题目总数', value: summary.total, icon: STAT_ICONS.doc, tileBg: '#d7f0fe', iconColor: '#2563eb' },
  { label: '已发布', value: summary.published, icon: CHECK_ICON, tileBg: '#ddfef0', iconColor: '#22c55e',
    ring: pct(summary.published, summary.total), ringColor: '#22c55e' },
  { label: '待发布', value: summary.draft, icon: CLOCK_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b',
    ring: pct(summary.draft, summary.total), ringColor: '#f59e0b' },
  { label: '已关闭', value: summary.closed, icon: CROSS_ICON, tileBg: '#fee7ed', iconColor: '#ef4444',
    ring: pct(summary.closed, summary.total), ringColor: '#ef4444' },
])
function pct(value: number, total: number) { return total ? Math.round((value / total) * 100) : 0 }

const typeOptions = computed(() =>
  [...new Set([...TYPE_PRESETS, ...rows.value.map(r => r.topic_type || '').filter(Boolean)])])

const filteredRows = computed(() => rows.value.filter((r) => {
  if (query.status && r.status !== query.status) return false
  if (query.topicType && (r.topic_type || '') !== query.topicType) return false
  if (query.keyword && !r.title.includes(query.keyword.trim())) return false
  return true
}))
const filteredTotal = computed(() => filteredRows.value.length)
const pagedRows = computed<TopicRow[]>(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredRows.value.slice(start, start + pageSize.value).map((r, i) => ({
    ...r, index: start + i + 1,
    statusCn: r.status === 'PUBLISHED' ? '已发布' : r.status === 'DRAFT' ? '待发布' : '已关闭',
    statusClass: r.status === 'DRAFT' ? 's-pending' : r.status === 'PUBLISHED' ? 's-published' : 's-closed',
    typeClass: TYPE_CLASS[r.topic_type || ''] || 't-gray',
    difficultyCn: DIFF_CN[r.difficulty || ''] || '—',
    createdAt: (r.created_at || '').replace('T', ' ').slice(0, 16) || '—',
  }))
})

async function loadSummary() {
  const statuses = ['', 'PUBLISHED', 'DRAFT', 'CLOSED']
  const results = await Promise.all(statuses.map(s => topicApi.teacherList({ page: 1, page_size: 1, status: s || undefined })))
  summary.total = results[0].data.total
  summary.published = results[1].data.total
  summary.draft = results[2].data.total
  summary.closed = results[3].data.total
}

async function loadRows() {
  loading.value = true
  try {
    const all: TopicItem[] = []
    for (let current = 1; current <= 10; current++) {
      const res = await topicApi.teacherList({ page: current, page_size: 100 })
      all.push(...res.data.items)
      if (all.length >= res.data.total) break
    }
    rows.value = all
  } finally { loading.value = false }
}

async function refresh() { await Promise.all([loadRows(), loadSummary()]) }

function onSearch() { page.value = 1 }
function onReset() {
  Object.assign(query, { keyword: '', topicType: '', status: '' })
  page.value = 1
}
function onSelectionChange(sel: TopicRow[]) { selected.value = sel }

/* ---------- 新增 / 编辑表单 ---------- */
const emptyForm = () => ({
  id: 0, title: '', topic_type: '', description: '', requirements: '',
  difficulty: 'MEDIUM', max_students: 1, publishNow: 'draft',
})
const form = reactive(emptyForm())
const editingStatusCn = computed(() =>
  form.id ? (rows.value.find(r => r.id === form.id)?.status === 'PUBLISHED' ? '已发布'
    : rows.value.find(r => r.id === form.id)?.status === 'DRAFT' ? '待发布' : '已关闭') : '')
const editingStatusClass = computed(() =>
  form.id ? (rows.value.find(r => r.id === form.id)?.status === 'PUBLISHED' ? 's-published'
    : rows.value.find(r => r.id === form.id)?.status === 'DRAFT' ? 's-pending' : 's-closed') : '')

function resetForm() { Object.assign(form, emptyForm()) }
function openCreate() { resetForm(); formVisible.value = true }
function openEdit(row: TopicRow) {
  Object.assign(form, {
    id: row.id, title: row.title, topic_type: row.topic_type || '', description: row.description || '',
    requirements: row.requirements || '', difficulty: row.difficulty || 'MEDIUM',
    max_students: row.max_students, publishNow: 'draft',
  })
  formVisible.value = true
}
async function save() {
  if (!form.title.trim() || !form.topic_type || !form.description.trim() || !form.requirements.trim()) {
    ElMessage.warning('请完整填写题目名称、方向、简介与研究要求'); return
  }
  saving.value = true
  try {
    const body = {
      title: form.title.trim(), description: form.description, requirements: form.requirements,
      topic_type: form.topic_type, difficulty: form.difficulty, max_students: form.max_students,
    }
    if (form.id) {
      await topicApi.update(form.id, body)
      ElMessage.success('题目已更新')
    } else {
      const res = await topicApi.create(body)
      if (form.publishNow === 'now') await topicApi.publish(res.data.id)
      ElMessage.success(form.publishNow === 'now' ? '题目已创建并发布' : '题目草稿已创建')
    }
    formVisible.value = false
    resetForm()
    await refresh()
  } catch {
    /* 已提示 */
  } finally { saving.value = false }
}

const formVisible = ref(false)

/* ---------- 详情 ---------- */
const detailVisible = ref(false)
const detail = ref<TopicRow | null>(null)
function viewDetail(row: TopicRow) { detail.value = row; detailVisible.value = true }

/* ---------- 单条操作 ---------- */
async function onPublish(row: TopicRow) {
  await ElMessageBox.confirm(`发布后学生即可看到「${row.title}」，确定？`, '发布题目', { type: 'info' })
  await topicApi.publish(row.id)
  ElMessage.success('已发布')
  await refresh()
}
async function onClose(row: TopicRow) {
  await ElMessageBox.confirm(`下架后学生将无法再选择该题目，确定？`, '下架题目', { type: 'warning' })
  await topicApi.close(row.id)
  ElMessage.success('已下架')
  await refresh()
}
async function onDelete(row: TopicRow) {
  await ElMessageBox.confirm(`删除题目「${row.title}」？`, '删除', { type: 'error' })
  await topicApi.remove(row.id)
  ElMessage.success('已删除')
  if (form.id === row.id) resetForm()
  await refresh()
}

/* ---------- 批量操作 ---------- */
async function onBatchPublish() {
  const drafts = selected.value.filter(s => s.status === 'DRAFT')
  if (!drafts.length) { ElMessage.warning('请先勾选待发布题目'); return }
  await ElMessageBox.confirm(`确定发布 ${drafts.length} 个题目吗？`, '批量发布', { type: 'info' })
  const results = await Promise.allSettled(drafts.map(s => topicApi.publish(s.id)))
  const success = results.filter(r => r.status === 'fulfilled').length
  ElMessage[success === drafts.length ? 'success' : 'warning'](`成功发布 ${success}/${drafts.length} 个题目`)
  await refresh()
}
async function onBatchClose() {
  const published = selected.value.filter(s => s.status === 'PUBLISHED')
  if (!published.length) { ElMessage.warning('请先勾选已发布题目'); return }
  await ElMessageBox.confirm(`确定下架 ${published.length} 个题目吗？`, '批量下架', { type: 'warning' })
  const results = await Promise.allSettled(published.map(s => topicApi.close(s.id)))
  const success = results.filter(r => r.status === 'fulfilled').length
  ElMessage[success === published.length ? 'success' : 'warning'](`成功下架 ${success}/${published.length} 个题目`)
  await refresh()
}
async function onBatchDelete() {
  const deletable = selected.value.filter(s => s.status !== 'PUBLISHED')
  if (!deletable.length) { ElMessage.warning('已发布题目不可删除，请先勾选草稿或已下架题目'); return }
  await ElMessageBox.confirm(`确定删除选中的 ${deletable.length} 个题目吗？`, '批量删除', { type: 'warning' })
  const results = await Promise.allSettled(deletable.map(s => topicApi.remove(s.id)))
  const success = results.filter(r => r.status === 'fulfilled').length
  ElMessage[success === deletable.length ? 'success' : 'warning'](`成功删除 ${success}/${deletable.length} 个题目`)
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
  padding: 14px 14px;
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
.topic-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.topic-main { min-width: 0; display: flex; flex-direction: column; gap: 12px; }

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
.filter-input { width: 220px; }
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
.table-toolbar { display: flex; align-items: center; margin-bottom: 10px; }
.toolbar-left { display: flex; gap: 8px; flex-wrap: wrap; }
.topic-table :deep(thead th) {
  background: #f0f6ff;
  color: #3b4a5f;
  font-weight: 600;
  font-size: 12px;
  padding: 7px 0;
}
.topic-table :deep(td) { font-size: 12px; color: #3b4a5f; padding: 5px 0; }
.topic-table :deep(.cell) { white-space: nowrap; padding: 0 6px; }
.topic-table :deep(.el-button + .el-button) { margin-left: 6px; }
.topic-title { color: #1f2d3d; font-weight: 500; }
.muted { color: #c2cbd8; }
.more-drop { margin-left: 6px; vertical-align: middle; }

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
.s-closed { color: #8a97ad; background: #eef1f5; }

/* 分页 */
.pagination-bar { display: flex; align-items: center; padding: 10px 4px 2px; }
.page-total { font-size: 12px; color: #8a97a8; margin-right: 12px; }
.page-size { width: 96px; margin-right: 12px; }
.pager { margin-left: auto; }

/* 右侧表单面板 */
.topic-form-panel {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  display: flex;
  flex-direction: column;
  min-height: 620px;
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
.panel-body { flex: 1; padding: 14px 16px; overflow-y: auto; }
.form-item { margin-bottom: 14px; }
.form-label {
  display: block;
  font-size: 13px;
  color: #3b4a5f;
  margin-bottom: 6px;
}
.form-label.required::before { content: '* '; color: #ef4444; }
.form-row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.panel-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 16px;
  border-top: 1px solid #eef2f8;
}

/* 详情 */
.detail-sub { margin: 16px 0 8px; color: #1f2d3d; }
.detail-text {
  white-space: pre-wrap;
  color: #606266;
  background: #f8fafc;
  border-radius: 6px;
  padding: 12px;
  margin: 0;
  line-height: 1.7;
}

@media (max-width: 1200px) {
  .topic-layout { grid-template-columns: 1fr; }
  .topic-form-panel { min-height: 0; }
}
@media (max-width: 900px) {
  .stat-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-row { flex-wrap: wrap; }
  .filter-item { flex: 1 1 220px; min-width: 0; }
  .filter-item.grow { flex: 1 1 220px; }
  .filter-item :deep(.el-input), .filter-item :deep(.el-select) { width: 100%; }
  .filter-actions { margin-left: 0; }
}
@media (max-width: 600px) {
  .stat-cards { grid-template-columns: minmax(0, 1fr); }
  .stat-card { min-height: 70px; align-items: center; }
  .filter-item { flex-basis: 100%; }
  .pagination-bar { flex-wrap: wrap; gap: 10px; }
  .pager { margin-left: 0; max-width: 100%; overflow-x: auto; }
}

/* 题目表单弹窗: 内容区自管滚动 */
:deep(.el-dialog__body) { padding-top: 6px; }
:deep(.el-dialog .panel-body) { max-height: 62vh; overflow-y: auto; }
:deep(.el-dialog .panel-footer) { padding-top: 12px; }
</style>
