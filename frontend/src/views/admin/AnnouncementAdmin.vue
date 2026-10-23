<template>
  <div class="ann-page">
    <!-- 统计卡 -->
    <div class="stat-cards">
      <div v-for="c in statCards" :key="c.label" class="stat-card">
        <div class="stat-icon" :style="{ background: c.tileBg, color: c.iconColor }">
          <svg viewBox="0 0 24 24" fill="currentColor" v-html="c.icon" />
        </div>
        <div class="stat-info">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-num">{{ c.value }}</div>
          <div class="stat-trend"><span class="trend-prefix">{{ c.hint }}</span></div>
        </div>
        <div v-if="c.ring !== undefined" class="stat-ring" :class="{ big: c.ringBig }" :style="{ '--pct': c.ring, '--ring-color': c.ringColor }">
          <span>{{ c.ring }}%</span>
        </div>
      </div>
    </div>

    <!-- 筛选区: 后端 /admin/announcements 只有 page / page_size / status 三个参数 -->
    <div class="filter-card">
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">发布状态</span>
          <el-select v-model="query.status" class="filter-select" placeholder="全部状态">
            <el-option label="全部状态" value="" />
            <el-option label="已发布" value="PUBLISHED" />
            <el-option label="草稿" value="DRAFT" />
            <el-option label="已下线" value="OFFLINE" />
          </el-select>
        </div>
        <el-button type="primary" @click="onSearch"><el-icon><Search /></el-icon>&nbsp;查询</el-button>
        <el-button class="reset-btn" @click="onReset"><el-icon><Refresh /></el-icon>&nbsp;重置</el-button>
        <span class="filter-hint">后端公告列表只支持按状态筛选(参数 page/page_size/status), 标题检索与发布时间区间未提供, 故不设这两个输入框</span>
      </div>
    </div>

    <!-- 两栏：列表 + 详情表单 -->
    <div class="main-cols">
      <!-- 左：公告列表 -->
      <div class="table-card">
        <div class="list-head">
          <div class="list-title">
            <svg class="horn-icon" viewBox="0 0 24 24" fill="currentColor">
              <path d="M3 10v4a1 1 0 0 0 1 1h2l4 4.5V5.5L6 10H4a1 1 0 0 0-1 0zm12.5-2.8a6 6 0 0 1 0 9.6l-1.2-1.6a4 4 0 0 0 0-6.4l1.2-1.6zM18 4a9 9 0 0 1 0 16l-1-1.7a7 7 0 0 0 0-12.6L18 4z" transform="translate(-1 0) scale(.96)"/>
            </svg>
            <span>公告列表</span>
          </div>
          <div class="list-actions">
            <el-button type="primary" @click="onCreate"><el-icon><Plus /></el-icon>&nbsp;新增公告</el-button>
            <el-button class="ghost-btn" :disabled="!selected.length" @click="onBatchOffline"><el-icon><Delete /></el-icon>&nbsp;批量下线</el-button>
          </div>
        </div>

        <el-table v-loading="loading" :data="rows" class="ann-table" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="38" />
          <el-table-column prop="index" label="序号" width="44" />
          <el-table-column prop="title" label="公告标题" min-width="170" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="row-title">{{ row.title }}</span>
            </template>
          </el-table-column>
          <el-table-column label="优先级" width="72" align="center">
            <template #default="{ row }">
              <span class="status-tag" :class="row.priorityClass">{{ row.priorityCn }}</span>
            </template>
          </el-table-column>
          <el-table-column label="发布人" width="86" align="center">
            <template #default="{ row }">{{ row.publisher_name || '—' }}</template>
          </el-table-column>
          <el-table-column label="发布时间" width="124">
            <template #default="{ row }">{{ fmtTime(row.publish_time) }}</template>
          </el-table-column>
          <el-table-column label="有效期至" width="100">
            <template #default="{ row }">{{ fmtDate(row.expire_time) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="72" align="center">
            <template #default="{ row }">
              <span class="status-tag" :class="row.statusClass">{{ row.statusCn }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="onView(row)">查看</el-button>
              <el-button link type="primary" size="small" @click="onEdit(row)">编辑</el-button>
              <el-button v-if="row.status === 'DRAFT'" link type="primary" size="small" @click="onPublish(row)">发布</el-button>
              <el-button v-if="row.status === 'OFFLINE'" link type="success" size="small" @click="onRelist(row)">重新上线</el-button>
              <el-button v-if="row.status === 'PUBLISHED'" link type="danger" size="small" @click="onOffline(row)">下线</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-bar">
          <span class="page-total">共 {{ total }} 条数据</span>
          <div class="pager-group">
            <el-select v-model="pageSize" size="small" class="page-size" @change="onPageSizeChange">
              <el-option label="10条/页" :value="10" />
              <el-option label="20条/页" :value="20" />
            </el-select>
            <el-pagination
              v-model:current-page="page" :page-size="pageSize" :total="total"
              background layout="prev, pager, next, jumper" class="pager" @current-change="load"
            />
          </div>
        </div>
      </div>

      <!-- 右：公告详情表单 -->
      <div class="detail-card">
        <div class="list-title">
          <svg class="doc-icon" viewBox="0 0 24 24" fill="currentColor" v-html="DOC_ICON" />
          <span>{{ editingId ? '编辑公告' : '新增公告' }}</span>
        </div>
        <el-form label-position="left" label-width="82px" class="detail-form">
          <el-form-item required>
            <template #label>公告标题</template>
            <el-input v-model="form.title" placeholder="请输入公告标题" maxlength="255">
              <template #suffix><span class="counter">{{ form.title.length }}/255</span></template>
            </el-input>
          </el-form-item>
          <el-form-item required>
            <template #label>优先级</template>
            <el-select v-model="form.priority" placeholder="请选择优先级" style="width: 100%">
              <el-option label="普通" value="NORMAL" />
              <el-option label="重要" value="IMPORTANT" />
              <el-option label="紧急" value="URGENT" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <template #label>保存状态</template>
            <el-radio-group v-model="form.status">
              <el-radio value="DRAFT" :disabled="draftDisabled">草稿（DRAFT）</el-radio>
              <el-radio value="PUBLISHED">立即发布（PUBLISHED）</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item required>
            <template #label>公告内容</template>
            <div class="editor">
              <div class="editor-toolbar">
                <span class="et-item">正文 ▾</span>
                <span class="et-item"><b>B</b></span>
                <span class="et-item"><i>I</i></span>
                <span class="et-item"><u>U</u></span>
                <span class="et-sep" />
                <span class="et-item">•≡</span>
                <span class="et-item">1≡</span>
                <span class="et-sep" />
                <span class="et-item">🔗</span>
                <span class="et-item">🖼</span>
              </div>
              <el-input
                v-model="form.content" type="textarea" :rows="5"
                placeholder="请输入公告内容..." maxlength="5000"
              />
              <div class="editor-counter">{{ form.content.length }}/5000</div>
            </div>
          </el-form-item>
          <el-form-item>
            <template #label>有效期至</template>
            <el-date-picker
              v-model="form.expireTime" type="date" format="YYYY-MM-DD"
              value-format="YYYY-MM-DD" placeholder="留空表示长期有效" style="width: 100%"
            />
          </el-form-item>
          <el-form-item v-if="editingId">
            <template #label>发布时间</template>
            <span class="readonly-value">{{ fmtTime(editingPublishTime) }}（由后端状态机写入, 不在此处编辑）</span>
          </el-form-item>
        </el-form>
        <div class="form-hint">
          已发布 / 已下线的公告不能退回草稿（后端 40003）: 要撤下用列表里的「下线」, 下线后可用「重新上线」恢复。
        </div>
        <div class="detail-footer">
          <el-button class="ghost-btn" @click="onCancel">取消</el-button>
          <el-button type="primary" :disabled="form.status === 'DRAFT' && draftDisabled" @click="onSubmit(form.status)">
            {{ submitLabel }}
          </el-button>
        </div>
      </div>
    </div>

    <!-- 查看弹窗 -->
    <el-dialog v-model="viewVisible" :title="viewing?.title" width="560px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="优先级">{{ viewing?.priorityCn }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ viewing?.statusCn }}</el-descriptions-item>
        <el-descriptions-item label="发布人">{{ viewing?.publisher_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="发布时间">{{ fmtTime(viewing?.publish_time) }}</el-descriptions-item>
        <el-descriptions-item label="有效期至">{{ fmtDate(viewing?.expire_time) }}</el-descriptions-item>
      </el-descriptions>
      <p class="view-content">{{ viewing?.content }}</p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 公告管理: 已接线后端 /admin/announcements(GET/POST/PUT/DELETE)。
 * 契约要点:
 *  - 列表参数只有 page / page_size / status(DRAFT|PUBLISHED|OFFLINE), 返回 {items,total};
 *  - POST/PUT 接受 status: 'DRAFT' | 'PUBLISHED'(缺省 PUBLISHED);
 *  - PUT 不允许把已发布改回草稿(40003), DELETE = 下线(transition offline), 不是物理删除;
 *  - 后端公告模型只有 title/content/priority/expire_time/status/publisher_name/publish_time,
 *    所以页面不展示 type(公告类型) / views(阅读量) / scope(发布范围) 这些后端不存在的字段。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { announcementApi, type AnnouncementPayload } from '@/api/common'

const DOC_ICON = '<path d="M7 2h7l5 5v15H7V2zm7 1.5V8h4.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M9.5 12h6M9.5 15.5h6M9.5 19h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
const SEND_ICON = '<path d="M2.5 20.5 21.5 12 2.5 3.5v6.5L16 12 2.5 14v6.5z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm.9-12.9h-1.8v5.2l4.4 2.6.9-1.5-3.5-2.1V7.1z"/>'
const CROSS_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm3.5 13.1-1.4 1.4L12 14.4l-2.1 2.1-1.4-1.4 2.1-2.1-2.1-2.1 1.4-1.4 2.1 2.1 2.1-2.1 1.4 1.4-2.1 2.1 2.1 2.1z"/>'

const STATUS_CN: Record<string, string> = { DRAFT: '草稿', PUBLISHED: '已发布', OFFLINE: '已下线' }
const STATUS_CLASS: Record<string, string> = { DRAFT: 's-draft', PUBLISHED: 's-published', OFFLINE: 's-offline' }
const PRIORITY_CN: Record<string, string> = { NORMAL: '普通', IMPORTANT: '重要', URGENT: '紧急' }
const PRIORITY_CLASS: Record<string, string> = { NORMAL: 'p-normal', IMPORTANT: 'p-important', URGENT: 'p-urgent' }

interface AnnRow {
  id: number
  title: string
  content: string
  priority: string
  status: string
  publisher_name: string | null
  publish_time: string | null
  expire_time: string | null
  index?: number
  statusCn?: string
  statusClass?: string
  priorityCn?: string
  priorityClass?: string
}

const rows = ref<AnnRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const selected = ref<AnnRow[]>([])

const query = reactive({ status: '' })
const counts = reactive({ all: 0, published: 0, draft: 0, offline: 0 })

const fmtTime = (v?: string | null) => (v ? v.replace('T', ' ').slice(0, 16) : '—')
const fmtDate = (v?: string | null) => (v ? v.slice(0, 10) : '—')

/* 统计卡全部来自后端计数(不再写死"较去年 +20%"这类假趋势) */
const statCards = computed(() => {
  const base = counts.all || 0
  const pct = (n: number) => (base ? Math.round((n / base) * 100) : 0)
  return [
    { label: '公告总数', value: base, hint: '全部状态合计', icon: DOC_ICON, tileBg: '#d7f0fe', iconColor: '#2563eb' },
    { label: '已发布', value: counts.published, hint: `占比 ${pct(counts.published)}%`, icon: SEND_ICON, tileBg: '#ddfef0', iconColor: '#22c55e', ring: pct(counts.published), ringColor: '#22c55e', ringBig: true },
    { label: '草稿', value: counts.draft, hint: `占比 ${pct(counts.draft)}%`, icon: CLOCK_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b', ring: pct(counts.draft), ringColor: '#f59e0b' },
    { label: '已下线', value: counts.offline, hint: `占比 ${pct(counts.offline)}%`, icon: CROSS_ICON, tileBg: '#fee7ed', iconColor: '#ef4444', ring: pct(counts.offline), ringColor: '#ef4444' },
  ]
})

async function load() {
  loading.value = true
  try {
    const res = await announcementApi.adminList({
      page: page.value, page_size: pageSize.value, status: query.status || undefined,
    })
    const data: any = res.data ?? {}
    const start = (page.value - 1) * pageSize.value
    rows.value = ((data.items ?? []) as AnnRow[]).map((r, i) => ({
      ...r,
      index: start + i + 1,
      statusCn: STATUS_CN[r.status] || r.status,
      statusClass: STATUS_CLASS[r.status] || 's-offline',
      priorityCn: PRIORITY_CN[r.priority] || r.priority,
      priorityClass: PRIORITY_CLASS[r.priority] || 'p-normal',
    }))
    total.value = data.total ?? 0
  } finally { loading.value = false }
}

async function loadCounts() {
  const statuses = ['', 'PUBLISHED', 'DRAFT', 'OFFLINE']
  const results = await Promise.all(statuses.map((s) =>
    announcementApi.adminList({ page: 1, page_size: 1, status: s || undefined })))
  counts.all = (results[0].data as any)?.total ?? 0
  counts.published = (results[1].data as any)?.total ?? 0
  counts.draft = (results[2].data as any)?.total ?? 0
  counts.offline = (results[3].data as any)?.total ?? 0
}

async function refresh() { await Promise.all([load(), loadCounts()]) }

function onSearch() { page.value = 1; load() }
function onReset() { query.status = ''; page.value = 1; load() }
function onPageSizeChange() { page.value = 1; load() }
function onSelectionChange(sel: AnnRow[]) { selected.value = sel }

async function confirmAction(message: string, title: string, type: 'warning' | 'info' = 'warning') {
  try {
    await ElMessageBox.confirm(message, title, { type, confirmButtonText: '确定', cancelButtonText: '取消' })
    return true
  } catch { return false }
}

/** payload 统一构造: PUT 是全量更新(title/content 必填), 只改字段状态时也要带上正文 */
function payload(status: 'DRAFT' | 'PUBLISHED', row?: AnnRow): AnnouncementPayload {
  const source = row
    ? { title: row.title, content: row.content, priority: row.priority, expireTime: fmtDate(row.expire_time) === '—' ? '' : fmtDate(row.expire_time) }
    : { title: form.title, content: form.content, priority: form.priority, expireTime: form.expireTime }
  return {
    title: source.title.trim(),
    content: source.content.trim(),
    priority: source.priority as AnnouncementPayload['priority'],
    // expire_time 后端是 datetime: 传日期就落在当天 00:00, 这里补成当日 23:59:59 表示"有效到当天结束"
    expire_time: source.expireTime ? `${source.expireTime}T23:59:59` : null,
    status,
  }
}

async function onPublish(row: AnnRow) {
  if (!(await confirmAction(`确定发布「${row.title}」吗？发布后学生端立即可见。`, '发布确认', 'info'))) return
  await announcementApi.update(row.id, payload('PUBLISHED', row))
  ElMessage.success('公告已发布')
  await refresh()
}

async function onRelist(row: AnnRow) {
  if (!(await confirmAction(`确定重新上线「${row.title}」吗？上线后学生端可再次看到。`, '上线确认', 'info'))) return
  await announcementApi.update(row.id, payload('PUBLISHED', row))
  ElMessage.success('公告已重新上线')
  await refresh()
}

async function onOffline(row: AnnRow) {
  if (!(await confirmAction(`确定下线「${row.title}」吗？下线后不再对学生可见，可重新上线。`, '下线确认'))) return
  // DELETE 的语义是 transition(offline), 不是物理删除
  await announcementApi.remove(row.id)
  ElMessage.success('公告已下线')
  await refresh()
}

async function onBatchOffline() {
  if (!selected.value.length) return
  const targets = selected.value.filter((r) => r.status !== 'OFFLINE')
  if (!targets.length) { ElMessage.warning('选中的公告都已下线'); return }
  if (!(await confirmAction(`确定下线选中的 ${targets.length} 条公告吗？下线后不再对学生可见，可重新上线。`, '批量下线'))) return
  for (const r of targets) await announcementApi.remove(r.id)
  ElMessage.success(`已下线 ${targets.length} 条公告`)
  await refresh()
}

/* ---------- 右侧表单 ---------- */
const form = reactive({
  title: '',
  content: '',
  priority: 'NORMAL' as 'NORMAL' | 'IMPORTANT' | 'URGENT',
  expireTime: '',
  /** 草稿开关(DRAFT/PUBLISHED 单选): 后端 POST/PUT 接受这两个值, 缺省 PUBLISHED */
  status: 'PUBLISHED' as 'DRAFT' | 'PUBLISHED',
})
const editingId = ref<number | null>(null)
const editingStatus = ref('')
const editingPublishTime = ref<string | null>(null)

/** 只有新建或编辑草稿时才能存草稿: 已发布→草稿 后端返回 40003, 已下线→草稿 同样不在状态机转移表里 */
const draftDisabled = computed(() => !!editingId.value && editingStatus.value !== 'DRAFT')

/** 底部主按钮文案跟随草稿开关; 已发布/已下线只能存回发布态(40003) */
const submitLabel = computed(() => (form.status === 'DRAFT'
  ? '保存为草稿'
  : editingStatus.value === 'PUBLISHED' ? '保存' : '保存并发布'))

function resetForm() {
  form.title = ''
  form.content = ''
  form.priority = 'NORMAL'
  form.expireTime = ''
  form.status = 'PUBLISHED'
  editingId.value = null
  editingStatus.value = ''
  editingPublishTime.value = null
}

function onCreate() {
  resetForm()
  ElMessage.info('请在右侧填写新公告')
}

function onEdit(row: AnnRow) {
  form.title = row.title
  form.content = row.content
  form.priority = (row.priority === 'IMPORTANT' || row.priority === 'URGENT' ? row.priority : 'NORMAL')
  form.expireTime = fmtDate(row.expire_time) === '—' ? '' : fmtDate(row.expire_time)
  // 已发布/已下线只能保存为发布态(→草稿会 40003); 草稿默认停在草稿
  form.status = row.status === 'DRAFT' ? 'DRAFT' : 'PUBLISHED'
  editingId.value = row.id
  editingStatus.value = row.status
  editingPublishTime.value = row.publish_time
  ElMessage.success('已载入公告，请在右侧编辑')
}

function onCancel() { resetForm() }

async function onSubmit(status: 'DRAFT' | 'PUBLISHED') {
  if (form.title.trim().length < 2) { ElMessage.warning('公告标题至少 2 个字'); return }
  if (form.content.trim().length < 2) { ElMessage.warning('公告内容至少 2 个字'); return }
  if (editingId.value && status === 'DRAFT' && editingStatus.value !== 'DRAFT') {
    ElMessage.warning('已发布/已下线的公告不能退回草稿，请改用「下线」')
    return
  }
  const body = payload(status)
  if (editingId.value) {
    await announcementApi.update(editingId.value, body)
    ElMessage.success(status === 'DRAFT' ? '草稿已保存' : '公告已更新')
  } else {
    await announcementApi.create(body)
    ElMessage.success(status === 'DRAFT' ? '草稿已保存' : '公告已发布')
  }
  resetForm()
  await refresh()
}

/* 查看弹窗 */
const viewVisible = ref(false)
const viewing = ref<AnnRow | null>(null)
function onView(row: AnnRow) {
  viewing.value = row
  viewVisible.value = true
}

onMounted(() => { load(); loadCounts() })
</script>

<style scoped>
/* 统计卡（4 张） */
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
  padding: 10px 12px;
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
.stat-info { flex: 1; min-width: 0; padding-right: 32px; }
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
  right: 12px;
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
.stat-ring.big { width: 48px; height: 48px; right: 10px; }
.stat-ring span {
  font-size: 9.5px;
  font-weight: 600;
  color: #2c3e5d;
}

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
  gap: 12px;
  flex-wrap: wrap;
}
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
.filter-input { width: 190px; }
.filter-select { width: 118px; }
.filter-date { width: 240px; }
.filter-hint {
  font-size: 11.5px;
  color: #8a97ad;
}
.reset-btn { border: 1px solid #dcdfe6; color: #2c3e5d; }

/* 两栏 */
.main-cols {
  display: flex;
  gap: 12px;
  align-items: stretch;
}
.table-card {
  flex: 1;
  min-width: 0;
  background: #fff;
  border-radius: 8px;
  padding: 8px 14px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0 10px;
}
.list-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
}
.horn-icon { width: 18px; height: 18px; color: #1878fd; }
.doc-icon { width: 17px; height: 17px; color: #1878fd; }
.list-actions { display: flex; gap: 8px; }
.ghost-btn { border: 1px solid #dcdfe6; color: #2c3e5d; }

.ann-table :deep(thead th) {
  background: #f0f6ff;
  color: #2c3e5d;
  font-weight: 600;
  font-size: 12px;
  padding: 6px 0;
}
.ann-table :deep(td) {
  font-size: 12px;
  color: #3b4a5f;
  padding: 5px 0;
}
.ann-table :deep(.cell) { padding: 0 6px; }
.ann-table :deep(.el-button + .el-button) { margin-left: 6px; }
.row-title { color: #1f2d3d; font-weight: 500; }

/* 状态 / 优先级药丸 */
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.s-published { color: #27ae60; background: #e4f7ec; }
.s-draft { color: #e67e22; background: #fdf1e3; }
.s-offline { color: #8a97ad; background: #eef1f5; }
.p-normal { color: #5a6b80; background: #eef2f8; }
.p-important { color: #1878fd; background: #e3effe; }
.p-urgent { color: #ef4444; background: #fee7ed; }

/* 分页 */
.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 4px 2px;
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

/* 右侧详情表单 */
.detail-card {
  width: 368px;
  flex-shrink: 0;
  background: #fff;
  border-radius: 8px;
  padding: 14px 16px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  display: flex;
  flex-direction: column;
}
.detail-form { flex: 1; }
.detail-form :deep(.el-form-item__label) {
  font-size: 13px;
  color: #3b4a5f;
  font-weight: 500;
  white-space: nowrap;
}
.detail-form :deep(.el-checkbox) {
  margin-right: 10px;
  height: auto;
}
.detail-form :deep(.el-checkbox__label) {
  font-size: 12px;
  padding-left: 5px;
}
.counter {
  font-size: 11px;
  color: #97a3b4;
}
.readonly-value {
  font-size: 12px;
  color: #6b7b90;
  line-height: 1.6;
}
.form-hint {
  font-size: 11.5px;
  color: #b07b2c;
  background: #fdf5e8;
  border: 1px solid #f4e3c4;
  border-radius: 6px;
  padding: 6px 8px;
  line-height: 1.6;
  margin-bottom: 8px;
}
.editor {
  width: 100%;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;
}
.editor-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  background: #f7f9fc;
  border-bottom: 1px solid #e4e9f0;
  font-size: 11px;
  color: #5a6b80;
}
.et-item { cursor: pointer; white-space: nowrap; }
.et-sep {
  width: 1px;
  height: 12px;
  background: #dde5f0;
}
.editor :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  resize: none;
  font-size: 12.5px;
}
.editor-counter {
  text-align: right;
  font-size: 11px;
  color: #97a3b4;
  padding: 3px 8px;
  background: #fafbfd;
  border-top: 1px solid #eef2f8;
}
.detail-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 6px;
  border-top: 1px solid #eef2f8;
}

.view-content {
  margin: 14px 0 0;
  line-height: 1.8;
  color: #4a5a70;
  background: #f8fafc;
  border-radius: 6px;
  padding: 12px;
  white-space: pre-wrap;
}
</style>
