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
          <div class="stat-trend"><span class="trend-prefix">较去年</span> <span :style="{ color: c.trendColor }">{{ c.trend }} {{ c.arrow }}</span></div>
        </div>
        <div v-if="c.ring !== undefined" class="stat-ring" :class="{ big: c.ringBig }" :style="{ '--pct': c.ring, '--ring-color': c.ringColor }">
          <span>{{ c.ring }}%</span>
        </div>
      </div>
    </div>

    <!-- 筛选区 -->
    <div class="filter-card">
      <div class="filter-row">
        <div class="filter-item grow">
          <span class="filter-label">公告标题</span>
          <el-input v-model="query.keyword" placeholder="请输入公告标题" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">公告类型</span>
          <el-select v-model="query.type" class="filter-select" placeholder="全部类型">
            <el-option label="全部类型" value="" />
            <el-option label="系统通知" value="系统通知" />
            <el-option label="教学安排" value="教学安排" />
            <el-option label="评优评奖" value="评优评奖" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">发布状态</span>
          <el-select v-model="query.status" class="filter-select" placeholder="全部状态">
            <el-option label="全部状态" value="" />
            <el-option label="已发布" value="已发布" />
            <el-option label="草稿" value="草稿" />
            <el-option label="已下线" value="已下线" />
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
        <el-button type="primary" @click="onSearch"><el-icon><Search /></el-icon>&nbsp;查询</el-button>
        <el-button class="reset-btn" @click="onReset"><el-icon><Refresh /></el-icon>&nbsp;重置</el-button>
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
            <el-button class="ghost-btn" @click="onBatchDelete"><el-icon><Delete /></el-icon>&nbsp;批量删除</el-button>
          </div>
        </div>

        <el-table :data="pagedRows" class="ann-table" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="38" />
          <el-table-column prop="index" label="序号" width="44" />
          <el-table-column prop="title" label="公告标题" min-width="170" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="row-title">{{ row.title }}</span>
            </template>
          </el-table-column>
          <el-table-column label="公告类型" width="82" align="center">
            <template #default="{ row }">
              <span class="type-pill" :class="row.typeClass">{{ row.type }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="publisher" label="发布人" width="72" align="center" />
          <el-table-column prop="publishTime" label="发布时间" width="118" />
          <el-table-column prop="expireTime" label="有效期至" width="96" />
          <el-table-column prop="views" label="浏览次数" width="76" align="center" />
          <el-table-column label="状态" width="70" align="center">
            <template #default="{ row }">
              <span class="status-tag" :class="row.statusClass">{{ row.status }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="126" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="onView(row)">查看</el-button>
              <el-button link type="primary" size="small" @click="onEdit(row)">编辑</el-button>
              <el-button v-if="row.status === '已发布' || row.forceOffline" link type="danger" size="small" @click="onOffline(row)">下线</el-button>
              <el-button v-else link type="primary" size="small" @click="onPublish(row)">发布</el-button>
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
              v-model:current-page="page" :page-size="pageSize" :total="total"
              background layout="prev, pager, next, jumper" class="pager"
            />
          </div>
        </div>
      </div>

      <!-- 右：公告详情表单 -->
      <div class="detail-card">
        <div class="list-title">
          <svg class="doc-icon" viewBox="0 0 24 24" fill="currentColor" v-html="DOC_ICON" />
          <span>公告详情</span>
        </div>
        <el-form label-position="left" label-width="82px" class="detail-form">
          <el-form-item required>
            <template #label>公告标题</template>
            <el-input v-model="form.title" placeholder="请输入公告标题" maxlength="100">
              <template #suffix><span class="counter">{{ form.title.length }}/100</span></template>
            </el-input>
          </el-form-item>
          <el-form-item required>
            <template #label>公告类型</template>
            <el-select v-model="form.type" placeholder="请选择公告类型" style="width: 100%">
              <el-option label="系统通知" value="系统通知" />
              <el-option label="教学安排" value="教学安排" />
              <el-option label="评优评奖" value="评优评奖" />
            </el-select>
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
          <el-form-item required>
            <template #label>发布时间</template>
            <el-date-picker
              v-model="form.publishTime" type="datetime" format="YYYY-MM-DD HH:mm"
              value-format="YYYY-MM-DD HH:mm" placeholder="请选择日期时间" style="width: 100%"
            />
          </el-form-item>
          <el-form-item>
            <template #label>有效期至</template>
            <el-date-picker
              v-model="form.expireTime" type="date" format="YYYY-MM-DD"
              value-format="YYYY-MM-DD" placeholder="请选择日期" style="width: 100%"
            />
          </el-form-item>
          <el-form-item required>
            <template #label>发布范围</template>
            <el-checkbox-group v-model="form.scope">
              <el-checkbox label="全体学生" />
              <el-checkbox label="全体教师" />
              <el-checkbox label="全体管理员" />
            </el-checkbox-group>
          </el-form-item>
        </el-form>
        <div class="detail-footer">
          <el-button class="ghost-btn" @click="onCancel">取消</el-button>
          <el-button type="primary" @click="onSubmit">立即发布</el-button>
        </div>
      </div>
    </div>

    <!-- 查看弹窗 -->
    <el-dialog v-model="viewVisible" :title="viewing?.title" width="560px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="公告类型">{{ viewing?.type }}</el-descriptions-item>
        <el-descriptions-item label="发布人">{{ viewing?.publisher }}</el-descriptions-item>
        <el-descriptions-item label="发布时间">{{ viewing?.publishTime }}</el-descriptions-item>
        <el-descriptions-item label="有效期至">{{ viewing?.expireTime }}</el-descriptions-item>
        <el-descriptions-item label="浏览次数">{{ viewing?.views }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ viewing?.status }}</el-descriptions-item>
      </el-descriptions>
      <p class="view-content">各学院、各位指导教师及2024届毕业生：请按学校毕业设计工作安排认真推进各阶段任务，具体要求见附件。</p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const DOC_ICON = '<path d="M7 2h7l5 5v15H7V2zm7 1.5V8h4.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M9.5 12h6M9.5 15.5h6M9.5 19h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
const SEND_ICON = '<path d="M2.5 20.5 21.5 12 2.5 3.5v6.5L16 12 2.5 14v6.5z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm.9-12.9h-1.8v5.2l4.4 2.6.9-1.5-3.5-2.1V7.1z"/>'
const CROSS_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm3.5 13.1-1.4 1.4L12 14.4l-2.1 2.1-1.4-1.4 2.1-2.1-2.1-2.1 1.4-1.4 2.1 2.1 2.1-2.1 1.4 1.4-2.1 2.1 2.1 2.1z"/>'

/* 趋势色遵循设计稿：涨为红、跌为绿，"较去年"前缀灰 */
const statCards = [
  { label: '公告总数', value: 24, icon: DOC_ICON, tileBg: '#d7f0fe', iconColor: '#2563eb', trend: '+20%', trendColor: '#ef4444', arrow: '↑' },
  { label: '已发布', value: 20, icon: SEND_ICON, tileBg: '#ddfef0', iconColor: '#22c55e', ring: 83, ringColor: '#22c55e', ringBig: true, trend: '+15%', trendColor: '#ef4444', arrow: '↑' },
  { label: '草稿', value: 2, icon: CLOCK_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b', ring: 8, ringColor: '#f59e0b', trend: '-33%', trendColor: '#22c55e', arrow: '↓' },
  { label: '已下线', value: 2, icon: CROSS_ICON, tileBg: '#fee7ed', iconColor: '#ef4444', ring: 8, ringColor: '#ef4444', trend: '-33%', trendColor: '#22c55e', arrow: '↓' },
]

const TYPE_CLASS: Record<string, string> = {
  系统通知: 'tp-system',
  教学安排: 'tp-teach',
  评优评奖: 'tp-award',
}

interface AnnRow {
  forceOffline?: boolean
  title: string
  type: string
  publisher: string
  publishTime: string
  expireTime: string
  views: number
  status: string
}

const RAW_ROWS: AnnRow[] = [
  { title: '关于2024届毕业设计工作安排的通知', type: '系统通知', publisher: '管理员', publishTime: '2024-03-01 14:30', expireTime: '2024-06-30', views: 326, status: '已发布' },
  { title: '毕业设计中期检查安排', type: '教学安排', publisher: '管理员', publishTime: '2024-02-20 10:15', expireTime: '2024-05-31', views: 248, status: '已发布' },
  { title: '关于答辩论文格式要求的说明', type: '教学安排', publisher: '管理员', publishTime: '2024-02-15 09:20', expireTime: '2024-04-30', views: 189, status: '已发布' },
  { title: '毕业设计优秀作品评选通知', type: '评优评奖', publisher: '管理员', publishTime: '2024-01-28 16:40', expireTime: '2024-05-20', views: 312, status: '已发布' },
  { title: '系统维护通知', type: '系统通知', publisher: '管理员', publishTime: '2024-01-20 11:00', expireTime: '2024-01-25', views: 156, status: '已下线' },
  { title: '关于延期开题报告提交的通知', type: '教学安排', publisher: '管理员', publishTime: '2024-05-10 08:30', expireTime: '2024-06-10', views: 98, status: '草稿' },
  { title: '毕业答辩工作流程说明', type: '教学安排', publisher: '管理员', publishTime: '2024-05-05 10:25', expireTime: '2024-06-30', views: 210, status: '已发布' },
  { title: '优秀指导教师评选通知', type: '评优评奖', publisher: '管理员', publishTime: '2024-04-28 15:10', expireTime: '2024-06-15', views: 176, status: '已发布' },
  { title: '关于毕业材料提交的补充说明', type: '教学安排', publisher: '管理员', publishTime: '2024-04-15 09:50', expireTime: '2024-06-20', views: 134, status: '已发布' },
  { title: '清明节放假安排通知', type: '系统通知', publisher: '管理员', publishTime: '2024-04-03 14:20', expireTime: '2024-04-07', views: 265, status: '已下线', forceOffline: true },
]

const rows = ref<AnnRow[]>([...RAW_ROWS])
const total = 24
const page = ref(1)
const pageSize = ref(10)
const selected = ref<AnnRow[]>([])

const query = reactive({
  keyword: '',
  type: '',
  status: '',
  dateRange: null as [string, string] | null,
})

const filtered = computed(() => {
  return rows.value.filter((r) => {
    if (query.keyword && !r.title.includes(query.keyword)) return false
    if (query.type && r.type !== query.type) return false
    if (query.status && r.status !== query.status) return false
    if (query.dateRange && query.dateRange[0] && r.publishTime.slice(0, 10) < query.dateRange[0]) return false
    if (query.dateRange && query.dateRange[1] && r.publishTime.slice(0, 10) > query.dateRange[1]) return false
    return true
  })
})

const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const slice = filtered.value.slice(start, start + pageSize.value)
  return slice.map((r, i) => ({
    ...r,
    index: start + i + 1,
    typeClass: TYPE_CLASS[r.type] || 'tp-system',
    statusClass: r.status === '已发布' ? 's-published' : r.status === '草稿' ? 's-draft' : 's-offline',
  }))
})

const form = reactive({
  title: '',
  type: '',
  content: '',
  publishTime: '2024-05-20 14:30',
  expireTime: '',
  scope: ['全体学生'] as string[],
})

function onSearch() {
  page.value = 1
}

function onReset() {
  query.keyword = ''
  query.type = ''
  query.status = ''
  query.dateRange = null
  page.value = 1
}

function onSelectionChange(sel: AnnRow[]) {
  selected.value = sel
}

const viewVisible = ref(false)
const viewing = ref<AnnRow | null>(null)

function onView(row: AnnRow) {
  viewing.value = row
  viewVisible.value = true
}

function onEdit(row: AnnRow) {
  form.title = row.title
  form.type = row.type
  form.content = ''
  form.publishTime = row.publishTime
  form.expireTime = row.expireTime
  ElMessage.success('已载入公告，请在右侧编辑')
}

function onCreate() {
  form.title = ''
  form.type = ''
  form.content = ''
  form.publishTime = '2024-05-20 14:30'
  form.expireTime = ''
  form.scope = ['全体学生']
  ElMessage.info('请在右侧填写新公告')
}

function onPublish(row: AnnRow) {
  ElMessageBox.confirm(`确定发布「${row.title}」吗？`, '发布确认', { type: 'info' })
    .then(() => {
      row.status = '已发布'
      ElMessage.success('公告已发布')
    })
    .catch(() => {})
}

function onOffline(row: AnnRow) {
  ElMessageBox.confirm(`确定下线「${row.title}」吗？`, '下线确认', { type: 'warning' })
    .then(() => {
      row.status = '已下线'
      ElMessage.success('公告已下线')
    })
    .catch(() => {})
}

function onBatchDelete() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选要删除的公告')
    return
  }
  ElMessageBox.confirm(`确定删除选中的 ${selected.value.length} 条公告吗？`, '批量删除', { type: 'warning' })
    .then(() => {
      const del = new Set(selected.value)
      rows.value = rows.value.filter((r) => !del.has(r))
      ElMessage.success('批量删除成功')
    })
    .catch(() => {})
}

function onCancel() {
  form.title = ''
  form.type = ''
  form.content = ''
  form.expireTime = ''
}

function onSubmit() {
  if (!form.title.trim()) {
    ElMessage.warning('请输入公告标题')
    return
  }
  if (!form.type) {
    ElMessage.warning('请选择公告类型')
    return
  }
  if (!form.content.trim()) {
    ElMessage.warning('请输入公告内容')
    return
  }
  rows.value.unshift({
    title: form.title,
    type: form.type,
    publisher: '管理员',
    publishTime: form.publishTime || '2024-05-20 14:30',
    expireTime: form.expireTime || '2024-06-30',
    views: 0,
    status: '已发布',
  })
  ElMessage.success('公告发布成功')
  onCancel()
}
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

/* 类型药丸 */
.type-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.tp-system { color: #1878fd; background: #e3effe; }
.tp-teach { color: #27ae60; background: #e4f7ec; }
.tp-award { color: #e67e22; background: #fdf1e3; }

/* 状态药丸 */
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
}
</style>
