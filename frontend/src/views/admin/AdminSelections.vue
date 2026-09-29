<template>
  <div class="selections-page">
    <!-- 统计卡 -->
    <div class="stat-cards">
      <div v-for="c in statCards" :key="c.label" class="stat-card">
        <div class="stat-icon" :style="{ background: c.tileBg, color: c.iconColor }">
          <svg viewBox="0 0 24 24" fill="currentColor" v-html="c.icon" />
        </div>
        <div class="stat-info">
          <div class="stat-num">{{ c.value }}</div>
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-trend" :style="{ color: c.trendColor }">{{ c.trend }} {{ c.arrow }}</div>
        </div>
        <div v-if="c.ring !== undefined" class="stat-ring" :class="{ big: c.ringBig }" :style="{ '--pct': c.ring, '--ring-color': c.ringColor }">
          <span>{{ c.ring }}%</span>
        </div>
      </div>
    </div>

    <!-- 筛选区 -->
    <div class="filter-card">
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">学年</span>
          <el-select v-model="query.year" class="filter-select narrow">
            <el-option label="2024届" value="2024届" />
            <el-option label="2023届" value="2023届" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">学院</span>
          <el-select v-model="query.college" class="filter-select" placeholder="全部学院">
            <el-option label="全部学院" value="" />
            <el-option v-for="c in collegeOptions" :key="c" :label="c" :value="c" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">专业</span>
          <el-select v-model="query.major" class="filter-select" placeholder="全部专业">
            <el-option label="全部专业" value="" />
            <el-option v-for="m in majorOptions" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <div class="filter-item grow">
          <span class="filter-label">题目名称</span>
          <el-input v-model="query.keyword" placeholder="请输入题目名称" clearable class="filter-input" />
        </div>
      </div>
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">学生姓名</span>
          <el-input v-model="query.student" placeholder="请输入学生姓名" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">学号</span>
          <el-input v-model="query.studentNo" placeholder="请输入学号" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">指导教师</span>
          <el-select v-model="query.teacher" class="filter-select" placeholder="全部教师">
            <el-option label="全部教师" value="" />
            <el-option v-for="t in teacherOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">审核状态</span>
          <el-select v-model="query.status" class="filter-select" placeholder="全部状态">
            <el-option label="全部状态" value="" />
            <el-option label="待审核" value="待审核" />
            <el-option label="已通过" value="已通过" />
            <el-option label="未通过" value="未通过" />
            <el-option label="撤回" value="撤回" />
          </el-select>
        </div>
      </div>
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">申请时间</span>
          <el-date-picker
            v-model="query.dateRange" type="daterange" range-separator="至"
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

    <!-- 表格卡：状态页签 + 工具按钮 + 表格 -->
    <div class="table-card">
      <div class="tab-bar">
        <div class="tabs">
          <div
            v-for="t in tabs" :key="t.name"
            class="tab" :class="{ active: activeTab === t.name }"
            @click="activeTab = t.name"
          >{{ t.name }} ({{ t.count }})</div>
        </div>
        <div class="tab-actions">
          <el-button class="ghost-btn" @click="onExport"><el-icon><Download /></el-icon>&nbsp;导出数据</el-button>
          <el-button class="ghost-btn" @click="onBatchReview"><el-icon><Setting /></el-icon>&nbsp;批量审核</el-button>
        </div>
      </div>

      <el-table :data="pagedRows" class="sel-table" @selection-change="onSelectionChange">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="index" label="序号" width="48" align="center" />
        <el-table-column prop="title" label="题目名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="topic-title">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column label="学生信息" width="108" align="center">
          <template #default="{ row }">
            <div class="stu-cell">
              <span class="stu-name">{{ row.student }}</span>
              <span class="stu-no">{{ row.studentNo }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="college" label="所属学院" width="102" align="center" />
        <el-table-column prop="major" label="所属专业" width="128" align="center" />
        <el-table-column prop="teacher" label="指导教师" width="82" align="center" />
        <el-table-column label="申请时间" width="104" align="center" sortable sort-by="applyDate">
          <template #default="{ row }">
            <div class="time-cell">
              <span>{{ row.applyDate }}</span>
              <span>{{ row.applyTime }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="审核状态" width="86" align="center">
          <template #default="{ row }">
            <span class="status-tag" :class="row.statusClass">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column label="审核意见" min-width="170">
          <template #default="{ row }">
            <span class="opinion" :class="{ dash: !row.opinion }">{{ row.opinion || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="128" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="viewDetail(row)">查看</el-button>
            <el-button v-if="row.status === '已通过'" link type="danger" size="small" @click="onWithdraw(row)">撤回</el-button>
            <el-button v-else link type="primary" size="small" @click="onReview(row)">审核</el-button>
            <el-dropdown trigger="hover" class="more-drop">
              <el-button link type="primary" size="small">更多<el-icon><ArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="onDelete(row)">删除记录</el-dropdown-item>
                  <el-dropdown-item @click="onRemark()">备注</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
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
          v-model:current-page="page" :page-size="pageSize" :total="total" :pager-count="5"
          background layout="prev, pager, next, jumper" class="pager"
        />
      </div>
    </div>

    <!-- 查看详情 -->
    <el-dialog v-model="detailVisible" :title="detail?.title" width="620px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="学生姓名">{{ detail?.student }}</el-descriptions-item>
        <el-descriptions-item label="学号">{{ detail?.studentNo }}</el-descriptions-item>
        <el-descriptions-item label="所属学院">{{ detail?.college }}</el-descriptions-item>
        <el-descriptions-item label="所属专业">{{ detail?.major }}</el-descriptions-item>
        <el-descriptions-item label="指导教师">{{ detail?.teacher }}</el-descriptions-item>
        <el-descriptions-item label="审核状态">{{ detail?.status }}</el-descriptions-item>
        <el-descriptions-item label="申请时间" :span="2">{{ detail?.applyDate }} {{ detail?.applyTime }}</el-descriptions-item>
        <el-descriptions-item label="审核意见" :span="2">{{ detail?.opinion || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { STAT_ICONS } from '@/components/statIcons'

const CHECK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.2L6.4 11.8l1.5-1.5 2.9 2.9 5.3-5.3 1.5 1.5-6.8 6.8z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 10.9V6.5h-2v6.4l5 2.9 1-1.7-4-2.3z"/>'
const CROSS_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm3.5 13.1-1.4 1.4L12 14.4l-2.1 2.1-1.4-1.4 2.1-2.1-2.1-2.1 1.4-1.4 2.1 2.1 2.1-2.1 1.4 1.4-2.1 2.1 2.1 2.1z"/>'
const LAYERS_ICON = '<path d="M12 3 2.5 8 12 13l9.5-5L12 3zm0 11.6L4.9 9.9l-2.4 1.3L12 16.5l9.5-5.3-2.4-1.3L12 14.6zm0 4L4.9 13.9l-2.4 1.3L12 20.5l9.5-5.3-2.4-1.3L12 18.6z"/>'

/* 趋势色遵循设计稿：涨为红、跌为绿 */
const statCards = [
  { label: '选题申请总数', value: 298, icon: STAT_ICONS.doc, tileBg: '#d7f0fe', iconColor: '#2563eb', trend: '较去年 +18%', trendColor: '#ef4444', arrow: '↗' },
  { label: '已通过', value: 186, icon: CHECK_ICON, tileBg: '#ddfef0', iconColor: '#22c55e', ring: 62, ringColor: '#22c55e', ringBig: true, trend: '较去年 +12%', trendColor: '#ef4444', arrow: '↗' },
  { label: '待审核', value: 56, icon: CLOCK_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b', ring: 19, ringColor: '#f59e0b', trend: '较去年 -8%', trendColor: '#22c55e', arrow: '↘' },
  { label: '未通过', value: 42, icon: CROSS_ICON, tileBg: '#fee7ed', iconColor: '#ef4444', ring: 14, ringColor: '#ef4444', trend: '较去年 +6%', trendColor: '#ef4444', arrow: '↗' },
  { label: '撤回/其他', value: 14, icon: LAYERS_ICON, tileBg: '#edeafd', iconColor: '#8b5cf6', ring: 5, ringColor: '#8b5cf6', trend: '较去年 -3%', trendColor: '#22c55e', arrow: '↘' },
]

const collegeOptions = ['计算机学院', '人工智能学院', '电子信息学院', '机械工程学院', '经济管理学院', '艺术设计学院']
const majorOptions = ['计算机科学与技术', '软件工程', '人工智能', '物联网工程', '机械设计制造及其自动化', '金融学', '视觉传达设计']
const teacherOptions = ['李老师', '王老师', '张老师', '刘老师', '陈老师']

const tabs = [
  { name: '全部', count: 298 },
  { name: '待审核', count: 56 },
  { name: '已通过', count: 186 },
  { name: '未通过', count: 42 },
  { name: '撤回', count: 14 },
]
const activeTab = ref('全部')

interface SelRow {
  title: string
  student: string
  studentNo: string
  college: string
  major: string
  teacher: string
  applyDate: string
  applyTime: string
  status: string
  opinion: string
}

const RAW_ROWS: SelRow[] = [
  { title: '基于大模型的智能问答系统设计与实现', student: '张三', studentNo: '2021001', college: '计算机学院', major: '计算机科学与技术', teacher: '李老师', applyDate: '2024-03-10', applyTime: '14:23', status: '待审核', opinion: '' },
  { title: '基于SpringBoot的校园二手交易平台设计', student: '李四', studentNo: '2021002', college: '计算机学院', major: '软件工程', teacher: '王老师', applyDate: '2024-03-10', applyTime: '10:15', status: '已通过', opinion: '选题方向明确，具有研究价值。' },
  { title: '基于深度学习的图像识别算法研究', student: '王五', studentNo: '2021003', college: '人工智能学院', major: '人工智能', teacher: '张老师', applyDate: '2024-03-09', applyTime: '16:42', status: '未通过', opinion: '研究内容过于宽泛，请进一步细化研究方向。' },
  { title: '面向高校的毕业设计管理系统设计与实现', student: '赵六', studentNo: '2021004', college: '计算机学院', major: '计算机科学与技术', teacher: '李老师', applyDate: '2024-03-09', applyTime: '09:30', status: '已通过', opinion: '选题贴合实际需求，工作量饱满。' },
  { title: '基于微信小程序的校园导览系统设计', student: '孙七', studentNo: '2021005', college: '计算机学院', major: '软件工程', teacher: '王老师', applyDate: '2024-03-08', applyTime: '15:47', status: '已通过', opinion: '题目难度适中，符合毕业设计要求。' },
  { title: '企业人力资源管理系统设计与实现', student: '周八', studentNo: '2021006', college: '经济管理学院', major: '人力资源管理', teacher: '陈老师', applyDate: '2024-03-08', applyTime: '11:20', status: '已通过', opinion: '选题贴合专业培养目标，同意开展。' },
  { title: '基于机器学习的股票价格预测模型研究', student: '吴九', studentNo: '2021007', college: '经济管理学院', major: '金融学', teacher: '张老师', applyDate: '2024-03-07', applyTime: '17:05', status: '未通过', opinion: '数据获取方案不明确，建议补充数据来源说明。' },
  { title: '非遗文化数字化保护平台设计', student: '郑十', studentNo: '2021008', college: '艺术设计学院', major: '视觉传达设计', teacher: '陈老师', applyDate: '2024-03-07', applyTime: '10:12', status: '撤回', opinion: '' },
]

const rows = ref<SelRow[]>([...RAW_ROWS])
const total = 298
const page = ref(1)
const pageSize = ref(10)
const selected = ref<SelRow[]>([])

const query = reactive({
  year: '2024届',
  college: '',
  major: '',
  keyword: '',
  student: '',
  studentNo: '',
  teacher: '',
  status: '',
  dateRange: null as [string, string] | null,
})

const filtered = computed(() => {
  return rows.value.filter((r) => {
    if (activeTab.value !== '全部' && r.status !== activeTab.value) return false
    if (query.college && r.college !== query.college) return false
    if (query.major && r.major !== query.major) return false
    if (query.keyword && !r.title.includes(query.keyword)) return false
    if (query.student && !r.student.includes(query.student)) return false
    if (query.studentNo && !r.studentNo.includes(query.studentNo)) return false
    if (query.teacher && r.teacher !== query.teacher) return false
    if (query.status && r.status !== query.status) return false
    if (query.dateRange && query.dateRange[0] && r.applyDate < query.dateRange[0]) return false
    if (query.dateRange && query.dateRange[1] && r.applyDate > query.dateRange[1]) return false
    return true
  })
})

const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const slice = filtered.value.slice(start, start + pageSize.value)
  return slice.map((r, i) => ({
    ...r,
    index: start + i + 1,
    statusClass: r.status === '待审核' ? 's-pending' : r.status === '已通过' ? 's-passed' : r.status === '未通过' ? 's-failed' : 's-withdraw',
  }))
})

function onSearch() {
  page.value = 1
}

function onReset() {
  query.year = '2024届'
  query.college = ''
  query.major = ''
  query.keyword = ''
  query.student = ''
  query.studentNo = ''
  query.teacher = ''
  query.status = ''
  query.dateRange = null
  page.value = 1
}

function onSelectionChange(sel: SelRow[]) {
  selected.value = sel
}

const detailVisible = ref(false)
const detail = ref<SelRow | null>(null)

function viewDetail(row: SelRow) {
  detail.value = row
  detailVisible.value = true
}

function onReview(row: SelRow) {
  ElMessageBox.confirm(`审核通过「${row.student}」的选题申请《${row.title}》吗？`, '审核确认', { type: 'success', confirmButtonText: '通过', cancelButtonText: '不通过' })
    .then(() => {
      row.status = '已通过'
      row.opinion = '选题方向明确，具有研究价值。'
      ElMessage.success('审核通过')
    })
    .catch(() => {})
}

function onWithdraw(row: SelRow) {
  ElMessageBox.confirm(`确定撤回「${row.student}」的通过结果吗？撤回后将回到待审核。`, '撤回确认', { type: 'warning' })
    .then(() => {
      row.status = '撤回'
      ElMessage.success('已撤回')
    })
    .catch(() => {})
}

function onBatchReview() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选要审核的申请')
    return
  }
  ElMessageBox.confirm(`确定批量审核选中的 ${selected.value.length} 条申请吗？`, '批量审核', { type: 'info' })
    .then(() => {
      selected.value.forEach((s) => {
        if (s.status === '待审核') {
          s.status = '已通过'
          s.opinion = '选题方向明确，具有研究价值。'
        }
      })
      ElMessage.success('批量审核完成')
    })
    .catch(() => {})
}

function onDelete(row: SelRow) {
  rows.value = rows.value.filter((r) => r !== row)
  ElMessage.success('记录已删除')
}

function onRemark() {
  ElMessage.info('备注功能演示')
}

function onExport() {
  ElMessage.success('导出成功')
}
</script>

<style scoped>
/* 统计卡 */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}
.stat-card {
  position: relative;
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
.stat-info { flex: 1; min-width: 0; padding-right: 30px; }
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
.stat-ring {
  --pct: 0;
  --ring-color: #f59e0b;
  position: absolute;
  right: 10px;
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
.stat-ring.big { width: 48px; height: 48px; right: 8px; }
.stat-ring span {
  font-size: 9.5px;
  font-weight: 600;
  color: #5a6b80;
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
.filter-input { width: 170px; }
.filter-select { width: 128px; }
.filter-select.narrow { width: 100px; }
.filter-date { width: 250px; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }
.reset-btn { border: 1px solid #dcdfe6; color: #5a6b80; }

/* 表格卡 */
.table-card {
  background: #fff;
  border-radius: 8px;
  padding: 4px 14px 8px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.tab-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #ebeef5;
  padding: 6px 0 0;
}
.tabs {
  display: flex;
  gap: 26px;
}
.tab {
  position: relative;
  font-size: 13.5px;
  color: #5a6b80;
  padding: 10px 2px 11px;
  cursor: pointer;
}
.tab.active {
  color: #1878fd;
  font-weight: 500;
}
.tab.active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background: #1878fd;
  border-radius: 1px;
}
.tab-actions { display: flex; gap: 8px; }
.ghost-btn { border: 1px solid #dcdfe6; color: #3b4a5f; }

.sel-table :deep(thead th) {
  background: #f0f6ff;
  color: #3b4a5f;
  font-weight: 600;
  font-size: 12px;
  padding: 7px 0;
}
.sel-table :deep(td) {
  font-size: 12px;
  color: #3b4a5f;
  padding: 6px 0;
}
.sel-table :deep(.cell) { padding: 0 6px; }
.sel-table :deep(.el-button + .el-button) { margin-left: 6px; }
.more-drop { margin-left: 6px; vertical-align: middle; }

.stu-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.5;
}
.stu-name { color: #1f2d3d; font-weight: 600; }
.stu-no { color: #97a3b4; font-size: 11px; }
.time-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.5;
}
.time-cell span { white-space: nowrap; }
.topic-title { color: #1f2d3d; font-weight: 500; }
.opinion {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.5;
  color: #5a6b80;
  text-align: left;
}
.opinion.dash { color: #c2cbd8; }

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
</style>
