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
          <div class="stat-trend" :style="{ color: c.trendColor }">{{ c.trend }} {{ c.arrow }}</div>
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
          <el-select v-model="query.college" class="filter-select">
            <el-option label="全部学院" value="" />
            <el-option v-for="c in collegeOptions" :key="c" :label="c" :value="c" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">所属专业</span>
          <el-select v-model="query.major" class="filter-select">
            <el-option label="全部专业" value="" />
            <el-option v-for="m in majorOptions" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">指导教师</span>
          <el-input v-model="query.teacher" placeholder="请输入教师姓名" clearable class="filter-input narrow" />
        </div>
      </div>
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">题目类型</span>
          <el-select v-model="query.type" class="filter-select">
            <el-option label="全部类型" value="" />
            <el-option v-for="t in typeOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">题目状态</span>
          <el-select v-model="query.status" class="filter-select">
            <el-option label="全部状态" value="" />
            <el-option label="已发布" value="已发布" />
            <el-option label="待发布" value="待发布" />
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

      <el-table :data="pagedRows" class="topic-table" @selection-change="onSelectionChange">
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
        <el-table-column prop="publishTime" label="发布时间" width="102" align="center" sortable />
        <el-table-column label="操作" width="138" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
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
          background layout="prev, pager, next, jumper" class="pager"
        />
      </div>
    </div>

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑题目' : '新增题目'" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="题目名称" required>
          <el-input v-model="form.title" placeholder="请输入题目名称" />
        </el-form-item>
        <el-form-item label="所属学院">
          <el-select v-model="form.college" style="width: 100%">
            <el-option v-for="c in collegeOptions" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属专业">
          <el-select v-model="form.major" style="width: 100%">
            <el-option v-for="m in majorOptions" :key="m" :label="m" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="题目类型" required>
          <el-select v-model="form.type" style="width: 100%">
            <el-option v-for="t in typeOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="指导教师" required>
          <el-select v-model="form.teacher" style="width: 100%">
            <el-option v-for="t in teacherOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="预计人数">
          <el-input-number v-model="form.expect" :min="1" :max="5" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveForm">确定</el-button>
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
      <p class="detail-text">{{ detailDescription }}</p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { STAT_ICONS } from '@/components/statIcons'

const CHECK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.2L6.4 11.8l1.5-1.5 2.9 2.9 5.3-5.3 1.5 1.5-6.8 6.8z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 10.4V6.5h-2v7.1l5.2 3-1-1.7-2.2-1.3V12.4z" transform="translate(0 .5)"/>'
const LAYERS_ICON = '<path d="M12 3 2.5 8 12 13l9.5-5L12 3zm0 11.6L4.9 9.9l-2.4 1.3L12 16.5l9.5-5.3-2.4-1.3L12 14.6zm0 4L4.9 13.9l-2.4 1.3L12 20.5l9.5-5.3-2.4-1.3L12 18.6z"/>'

const statCards = [
  { label: '题目总数', value: 320, icon: STAT_ICONS.doc, color: '#2563eb', bg: '#d7f0fe', trend: '较去年 +15%', trendColor: '#1878fd', arrow: '↗' },
  { label: '已发布题目', value: 286, icon: CHECK_ICON, color: '#22c55e', bg: '#ddfef0', trend: '较去年 +12%', trendColor: '#1878fd', arrow: '↗' },
  { label: '待发布题目', value: 34, icon: CLOCK_ICON, color: '#f59e0b', bg: '#fef5ea', trend: '较去年 -8%', trendColor: '#ef4444', arrow: '↘' },
  { label: '关联教师数', value: 56, icon: STAT_ICONS.person, color: '#ef4444', bg: '#fee7ed', trend: '较去年 +8%', trendColor: '#1878fd', arrow: '↗' },
  { label: '涉及专业数', value: 12, icon: LAYERS_ICON, color: '#8b5cf6', bg: '#edeafd', trend: '较去年 +0%', trendColor: '#8a97ad', arrow: '→' },
]

const typeOptions = ['软件开发', '硬件设计', '算法研究', '应用研究', '其他']
const teacherOptions = ['王教授', '李教授', '张教授', '刘教授', '陈教授', '赵教授']
const collegeOptions = ['计算机学院', '电子信息学院', '机械工程学院', '经济管理学院', '艺术设计学院']
const majorOptions = ['计算机科学与技术', '软件工程', '电子信息工程', '机械设计制造及其自动化', '会计学', '视觉传达设计']

const TYPE_CLASS: Record<string, string> = {
  软件开发: 't-blue',
  硬件设计: 't-cyan',
  算法研究: 't-green',
  应用研究: 't-purple',
  其他: 't-gray',
}

interface TopicRow {
  title: string
  college: string
  major: string
  type: string
  teacher: string
  expect: number
  chosen: number
  status: string
  publishTime: string
}

const RAW_ROWS: TopicRow[] = [
  { title: '基于深度学习的图像识别系统设计与实现', college: '计算机学院', major: '计算机科学与技术', type: '软件开发', teacher: '王教授', expect: 3, chosen: 2, status: '已发布', publishTime: '2024-03-15' },
  { title: '智能校园信息管理系统设计与实现', college: '计算机学院', major: '软件工程', type: '软件开发', teacher: '李教授', expect: 3, chosen: 2, status: '已发布', publishTime: '2024-03-15' },
  { title: '基于物联网的智能家居控制系统设计', college: '电子信息学院', major: '电子信息工程', type: '硬件设计', teacher: '刘教授', expect: 3, chosen: 1, status: '已发布', publishTime: '2024-03-14' },
  { title: '智能交通信号灯优化算法研究', college: '电子信息学院', major: '电子信息工程', type: '算法研究', teacher: '张教授', expect: 2, chosen: 1, status: '已发布', publishTime: '2024-03-14' },
  { title: '移动应用开发实践平台设计与实现', college: '计算机学院', major: '软件工程', type: '软件开发', teacher: '王教授', expect: 3, chosen: 0, status: '待发布', publishTime: '2024-03-13' },
  { title: '企业财务数据分析系统设计与实现', college: '经济管理学院', major: '会计学', type: '应用研究', teacher: '陈教授', expect: 3, chosen: 2, status: '已发布', publishTime: '2024-03-13' },
  { title: '智能校园能耗监测系统设计与实现', college: '计算机学院', major: '软件工程', type: '软件开发', teacher: '李教授', expect: 3, chosen: 2, status: '已发布', publishTime: '2024-03-12' },
  { title: '基于区块链的分布式存储系统研究', college: '计算机学院', major: '计算机科学与技术', type: '算法研究', teacher: '赵教授', expect: 2, chosen: 0, status: '待发布', publishTime: '2024-03-12' },
  { title: '数控机床刀具磨损监测系统设计', college: '机械工程学院', major: '机械设计制造及其自动化', type: '硬件设计', teacher: '刘教授', expect: 3, chosen: 1, status: '已发布', publishTime: '2024-03-11' },
  { title: '非遗文化视觉传达设计与推广研究', college: '艺术设计学院', major: '视觉传达设计', type: '应用研究', teacher: '陈教授', expect: 2, chosen: 1, status: '已发布', publishTime: '2024-03-11' },
]

const rows = ref<TopicRow[]>([...RAW_ROWS])
const total = 320
const page = ref(1)
const pageSize = ref(10)
const selected = ref<(TopicRow & { index?: number })[]>([])

const query = reactive({
  keyword: '',
  college: '',
  major: '',
  teacher: '',
  type: '',
  status: '',
  dateRange: null as [string, string] | null,
})

const filtered = computed(() => {
  return rows.value.filter((r) => {
    if (query.keyword && !r.title.includes(query.keyword)) return false
    if (query.college && r.college !== query.college) return false
    if (query.major && r.major !== query.major) return false
    if (query.teacher && !r.teacher.includes(query.teacher)) return false
    if (query.type && r.type !== query.type) return false
    if (query.status && r.status !== query.status) return false
    if (query.dateRange && query.dateRange[0] && r.publishTime < query.dateRange[0]) return false
    if (query.dateRange && query.dateRange[1] && r.publishTime > query.dateRange[1]) return false
    return true
  })
})

const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const slice = filtered.value.slice(start, start + pageSize.value)
  return slice.map((r, i) => ({
    ...r,
    index: start + i + 1,
    typeClass: TYPE_CLASS[r.type] || 't-gray',
    statusClass: r.status === '待发布' ? 's-pending' : 's-published',
  }))
})

function onSearch() {
  page.value = 1
}

function onReset() {
  query.keyword = ''
  query.college = ''
  query.major = ''
  query.teacher = ''
  query.type = ''
  query.status = ''
  query.dateRange = null
  page.value = 1
}

function onSelectionChange(sel: TopicRow[]) {
  selected.value = sel
}

const dialogVisible = ref(false)
const editing = ref(false)
const editIndex = ref(-1)
const form = reactive({ title: '', college: '计算机学院', major: '计算机科学与技术', type: '软件开发', teacher: '王教授', expect: 3 })

function openCreate() {
  editing.value = false
  form.title = ''
  form.college = '计算机学院'
  form.major = '计算机科学与技术'
  form.type = '软件开发'
  form.teacher = '王教授'
  form.expect = 3
  dialogVisible.value = true
}

function openEdit(row: TopicRow) {
  editing.value = true
  editIndex.value = rows.value.indexOf(row)
  form.title = row.title
  form.college = row.college
  form.major = row.major
  form.type = row.type
  form.teacher = row.teacher
  form.expect = row.expect
  dialogVisible.value = true
}

function saveForm() {
  if (!form.title.trim()) {
    ElMessage.warning('请输入题目名称')
    return
  }
  if (editing.value && editIndex.value >= 0) {
    const row = rows.value[editIndex.value]
    row.title = form.title
    row.college = form.college
    row.major = form.major
    row.type = form.type
    row.teacher = form.teacher
    row.expect = form.expect
    ElMessage.success('题目已更新')
  } else {
    rows.value.push({
      title: form.title,
      college: form.college,
      major: form.major,
      type: form.type,
      teacher: form.teacher,
      expect: form.expect,
      chosen: 0,
      status: '待发布',
      publishTime: '2024-03-15',
    })
    ElMessage.success('题目新增成功')
  }
  dialogVisible.value = false
}

const detailVisible = ref(false)
const detail = ref<TopicRow | null>(null)
const detailDescription = '基于实际业务场景，综合运用所学专业知识完成系统的分析、设计与实现，并撰写符合规范的设计文档。'

function viewDetail(row: TopicRow) {
  detail.value = row
  detailVisible.value = true
}

function onDelete(row: TopicRow) {
  ElMessageBox.confirm(`确定删除题目「${row.title}」吗？`, '提示', { type: 'warning' })
    .then(() => {
      rows.value = rows.value.filter((r) => r !== row)
      ElMessage.success('删除成功')
    })
    .catch(() => {})
}

function onBatchPublish() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选要发布的题目')
    return
  }
  const count = selected.value.filter((s) => s.status === '待发布').length
  selected.value.forEach((s) => { s.status = '已发布' })
  ElMessage.success(count ? `已发布 ${count} 个题目` : '所选题目均已发布')
}

function onBatchDelete() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选要删除的题目')
    return
  }
  ElMessageBox.confirm(`确定删除选中的 ${selected.value.length} 个题目吗？`, '提示', { type: 'warning' })
    .then(() => {
      const del = new Set(selected.value)
      rows.value = rows.value.filter((r) => !del.has(r))
      ElMessage.success('批量删除成功')
    })
    .catch(() => {})
}

function onExport() {
  ElMessage.success('导出成功')
}
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
</style>
