<template>
  <div class="materials-page">
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

    <!-- 筛选区（两行字段 + 右下角按钮行） -->
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
          <span class="filter-label">学生姓名</span>
          <el-input v-model="query.student" placeholder="请输入学生姓名" clearable class="filter-input" />
        </div>
      </div>
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">材料类型</span>
          <el-select v-model="query.materialType" class="filter-select" placeholder="全部类型">
            <el-option label="全部类型" value="" />
            <el-option label="毕业设计" value="毕业设计" />
            <el-option label="毕业论文" value="毕业论文" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">提交状态</span>
          <el-select v-model="query.status" class="filter-select" placeholder="全部状态">
            <el-option label="全部状态" value="" />
            <el-option label="已通过" value="已通过" />
            <el-option label="审核中" value="审核中" />
            <el-option label="未通过" value="未通过" />
            <el-option label="未提交" value="未提交" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">指导教师</span>
          <el-select v-model="query.teacher" class="filter-select" placeholder="全部教师">
            <el-option label="全部教师" value="" />
            <el-option v-for="t in teacherOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">提交时间</span>
          <el-date-picker
            v-model="query.dateRange" type="daterange" range-separator="至"
            start-placeholder="开始日期" end-placeholder="结束日期"
            value-format="YYYY-MM-DD" class="filter-date"
          />
        </div>
      </div>
      <div class="filter-row btn-row">
        <el-button type="primary" @click="onSearch"><el-icon><Search /></el-icon>&nbsp;查询</el-button>
        <el-button class="reset-btn" @click="onReset"><el-icon><Refresh /></el-icon>&nbsp;重置</el-button>
      </div>
    </div>

    <!-- 表格卡：页签 + 工具按钮 + 表格 + 分页 -->
    <div class="table-card">
      <div class="tab-bar">
        <div class="tabs">
          <div
            v-for="t in tabs" :key="t.name"
            class="tab" :class="{ active: activeTab === t.name }"
            @click="activeTab = t.name"
          >{{ t.name }}（{{ t.count }}）</div>
        </div>
        <div class="tab-actions">
          <el-button class="ghost-btn" @click="onExport"><el-icon><Download /></el-icon>&nbsp;导出数据</el-button>
          <el-button class="ghost-btn" @click="onBatchReview"><el-icon><Setting /></el-icon>&nbsp;批量审核</el-button>
        </div>
      </div>

      <el-table :data="pagedRows" class="mat-table" @selection-change="onSelectionChange">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="index" label="序号" width="48" />
        <el-table-column prop="student" label="学生姓名" width="78" />
        <el-table-column prop="studentNo" label="学号" width="88" />
        <el-table-column prop="college" label="学院" width="100" />
        <el-table-column prop="major" label="专业" width="134" />
        <el-table-column label="材料类型" width="84" align="center">
          <template #default="{ row }">
            <span class="type-pill" :class="row.typeClass">{{ row.materialType }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="fileName" label="文件名称" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="file-name">{{ row.fileName }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="fileSize" label="文件大小" width="70" align="center" />
        <el-table-column prop="submitTime" label="提交时间" width="126" />
        <el-table-column label="审核状态" width="84" align="center">
          <template #default="{ row }">
            <span class="status-tag" :class="row.statusClass">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column label="评审意见" min-width="110">
          <template #default="{ row }">
            <span class="opinion" :class="{ dash: !row.opinion }">{{ row.opinion || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="118" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="viewDetail(row)">查看</el-button>
            <el-button link type="primary" size="small" @click="onDownload(row)">下载</el-button>
            <el-button link type="primary" size="small" @click="onReview(row)">审核</el-button>
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
            v-model:current-page="page" :page-size="pageSize" :total="total" :pager-count="7"
            background layout="prev, pager, next, jumper" class="pager"
          />
        </div>
      </div>
    </div>

    <!-- 查看详情弹窗 -->
    <el-dialog v-model="detailVisible" :title="detail?.fileName" width="620px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="学生姓名">{{ detail?.student }}</el-descriptions-item>
        <el-descriptions-item label="学号">{{ detail?.studentNo }}</el-descriptions-item>
        <el-descriptions-item label="学院">{{ detail?.college }}</el-descriptions-item>
        <el-descriptions-item label="专业">{{ detail?.major }}</el-descriptions-item>
        <el-descriptions-item label="材料类型">{{ detail?.materialType }}</el-descriptions-item>
        <el-descriptions-item label="文件大小">{{ detail?.fileSize }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ detail?.submitTime }}</el-descriptions-item>
        <el-descriptions-item label="审核状态">{{ detail?.status }}</el-descriptions-item>
        <el-descriptions-item label="评审意见" :span="2">{{ detail?.opinion || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { STAT_ICONS } from '@/components/statIcons'

const CHECK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.2L6.4 11.8l1.5-1.5 2.9 2.9 5.3-5.3 1.5 1.5-6.8 6.8z"/>'
const CLOCK_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm.9-12.9h-1.8v5.2l4.4 2.6.9-1.5-3.5-2.1V7.1z"/>'
const CROSS_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm3.5 13.1-1.4 1.4L12 14.4l-2.1 2.1-1.4-1.4 2.1-2.1-2.1-2.1 1.4-1.4 2.1 2.1 2.1-2.1 1.4 1.4-2.1 2.1 2.1 2.1z"/>'
const LAYERS_ICON = '<path d="M12 3 2.5 8 12 13l9.5-5L12 3zm0 11.6L4.9 9.9l-2.4 1.3L12 16.5l9.5-5.3-2.4-1.3L12 14.6zm0 4L4.9 13.9l-2.4 1.3L12 20.5l9.5-5.3-2.4-1.3L12 18.6z"/>'

/* 趋势色遵循设计稿：涨为红、跌为绿，"较去年"前缀灰 */
const statCards = [
  { label: '材料提交总数', value: 186, icon: STAT_ICONS.doc, tileBg: '#d7f0fe', iconColor: '#2563eb', trend: '+18%', trendColor: '#ef4444', arrow: '↗' },
  { label: '已通过', value: 112, icon: CHECK_ICON, tileBg: '#ddfef0', iconColor: '#22c55e', ring: 60, ringColor: '#22c55e', ringBig: true, trend: '+12%', trendColor: '#ef4444', arrow: '↗' },
  { label: '审核中', value: 36, icon: CLOCK_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b', ring: 19, ringColor: '#f59e0b', trend: '-5%', trendColor: '#22c55e', arrow: '↘' },
  { label: '未通过', value: 24, icon: CROSS_ICON, tileBg: '#fee7ed', iconColor: '#ef4444', ring: 13, ringColor: '#ef4444', trend: '+6%', trendColor: '#ef4444', arrow: '↗' },
  { label: '未提交', value: 14, icon: LAYERS_ICON, tileBg: '#edeafd', iconColor: '#8b5cf6', ring: 8, ringColor: '#8b5cf6', trend: '-8%', trendColor: '#22c55e', arrow: '↘' },
]

const collegeOptions = ['计算机学院', '人工智能学院', '电子信息学院', '管理学院', '经济管理学院', '数学与统计学院']
const majorOptions = ['计算机科学与技术', '软件工程', '人工智能', '网络工程', '电子信息工程', '信息管理与信息系统', '数据科学与大数据技术', '信息安全', '应用统计学']
const teacherOptions = ['李老师', '王老师', '张老师', '陈老师', '刘老师', '黄老师', '林老师', '何老师', '宋老师', '高老师']

const tabs = [
  { name: '全部', count: 186 },
  { name: '已通过', count: 112 },
  { name: '审核中', count: 36 },
  { name: '未通过', count: 24 },
  { name: '未提交', count: 14 },
]
const activeTab = ref('全部')

interface MatRow {
  student: string
  studentNo: string
  college: string
  major: string
  materialType: string
  fileName: string
  fileSize: string
  submitTime: string
  status: string
  opinion: string
}

const RAW_ROWS: MatRow[] = [
  { student: '张三', studentNo: '2021001', college: '计算机学院', major: '计算机科学与技术', materialType: '毕业设计', fileName: '基于大模型的智能问答系统研究.zip', fileSize: '12.6MB', submitTime: '2024-05-20 14:23', status: '已通过', opinion: '' },
  { student: '李四', studentNo: '2021002', college: '计算机学院', major: '软件工程', materialType: '毕业论文', fileName: '基于SpringBoot的校园二手交易平台设计.docx', fileSize: '8.4MB', submitTime: '2024-05-18 16:20', status: '审核中', opinion: '论文格式需调整' },
  { student: '王五', studentNo: '2021003', college: '人工智能学院', major: '人工智能', materialType: '毕业设计', fileName: '基于深度学习的图像识别算法研究.zip', fileSize: '15.2MB', submitTime: '2024-05-17 11:12', status: '未通过', opinion: '研究内容不够深入，请补充实验。' },
  { student: '赵六', studentNo: '2021004', college: '计算机学院', major: '软件工程', materialType: '毕业论文', fileName: '面向高校的毕业设计管理系统设计.docx', fileSize: '10.8MB', submitTime: '2024-05-16 10:36', status: '已通过', opinion: '' },
  { student: '孙七', studentNo: '2021005', college: '计算机学院', major: '网络工程', materialType: '毕业设计', fileName: '基于Vue3的在线学习平台前端设计.zip', fileSize: '9.1MB', submitTime: '2024-05-16 09:20', status: '审核中', opinion: '缺少实验数据' },
  { student: '周八', studentNo: '2021006', college: '电子信息学院', major: '电子信息工程', materialType: '毕业论文', fileName: '基于物联网的智能家居系统设计.docx', fileSize: '11.3MB', submitTime: '2024-05-15 15:48', status: '已通过', opinion: '' },
  { student: '吴九', studentNo: '2021007', college: '管理学院', major: '信息管理与信息系统', materialType: '毕业设计', fileName: '基于大数据的高校教学质量分析.zip', fileSize: '13.7MB', submitTime: '2024-05-14 16:12', status: '已通过', opinion: '' },
  { student: '郑十', studentNo: '2021008', college: '经济管理学院', major: '数据科学与大数据技术', materialType: '毕业论文', fileName: '基于Python的金融数据分析与可视化.docx', fileSize: '7.8MB', submitTime: '2024-05-14 10:25', status: '未通过', opinion: '创新性不足，需进一步完善。' },
  { student: '刘一', studentNo: '2021009', college: '计算机学院', major: '信息安全', materialType: '毕业设计', fileName: '基于区块链的文件存证系统研究.zip', fileSize: '14.5MB', submitTime: '2024-05-13 14:22', status: '已通过', opinion: '' },
  { student: '陈二', studentNo: '2021010', college: '数学与统计学院', major: '应用统计学', materialType: '毕业论文', fileName: '基于机器学习的学生成绩预测模型.docx', fileSize: '6.9MB', submitTime: '2024-05-12 11:36', status: '审核中', opinion: '数据分析部分需补充图表说明。' },
]

const rows = ref<MatRow[]>([...RAW_ROWS])
const total = 186
const page = ref(1)
const pageSize = ref(10)
const selected = ref<MatRow[]>([])

const query = reactive({
  year: '2024届',
  college: '',
  major: '',
  student: '',
  materialType: '',
  status: '',
  teacher: '',
  dateRange: null as [string, string] | null,
})

const filtered = computed(() => {
  return rows.value.filter((r) => {
    if (activeTab.value !== '全部' && r.status !== activeTab.value) return false
    if (query.college && r.college !== query.college) return false
    if (query.major && r.major !== query.major) return false
    if (query.student && !r.student.includes(query.student)) return false
    if (query.materialType && r.materialType !== query.materialType) return false
    if (query.status && r.status !== query.status) return false
    if (query.dateRange && query.dateRange[0] && r.submitTime.slice(0, 10) < query.dateRange[0]) return false
    if (query.dateRange && query.dateRange[1] && r.submitTime.slice(0, 10) > query.dateRange[1]) return false
    return true
  })
})

const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const slice = filtered.value.slice(start, start + pageSize.value)
  return slice.map((r, i) => ({
    ...r,
    index: start + i + 1,
    typeClass: r.materialType === '毕业设计' ? 'tp-design' : 'tp-thesis',
    statusClass: r.status === '审核中' ? 's-pending' : r.status === '已通过' ? 's-passed' : r.status === '未通过' ? 's-failed' : 's-missing',
  }))
})

function onSearch() {
  page.value = 1
}

function onReset() {
  query.year = '2024届'
  query.college = ''
  query.major = ''
  query.student = ''
  query.materialType = ''
  query.status = ''
  query.teacher = ''
  query.dateRange = null
  page.value = 1
}

function onSelectionChange(sel: MatRow[]) {
  selected.value = sel
}

const detailVisible = ref(false)
const detail = ref<MatRow | null>(null)

function viewDetail(row: MatRow) {
  detail.value = row
  detailVisible.value = true
}

function onDownload(row: MatRow) {
  ElMessage.success(`「${row.fileName}」下载中`)
}

function onReview(row: MatRow) {
  ElMessageBox.confirm(`审核「${row.student}」提交的《${row.fileName}》吗？`, '审核确认', { type: 'success', confirmButtonText: '通过', cancelButtonText: '不通过' })
    .then(() => {
      row.status = '已通过'
      row.opinion = ''
      ElMessage.success('审核通过')
    })
    .catch(() => {})
}

function onBatchReview() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选要审核的材料')
    return
  }
  ElMessageBox.confirm(`确定批量审核选中的 ${selected.value.length} 份材料吗？`, '批量审核', { type: 'info' })
    .then(() => {
      selected.value.forEach((s) => {
        if (s.status === '审核中') s.status = '已通过'
      })
      ElMessage.success('批量审核完成')
    })
    .catch(() => {})
}

function onExport() {
  ElMessage.success('导出成功')
}
</script>

<style scoped>
/* 统计卡（标签在数值上方） */
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
  padding: 10px 10px;
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
.stat-info { flex: 1; min-width: 0; padding-right: 30px; }
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
  gap: 14px;
}
.filter-row + .filter-row { margin-top: 8px; }
.filter-row.btn-row { justify-content: flex-end; margin-top: 10px; }
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
.filter-input { width: 170px; }
.filter-select { width: 128px; }
.filter-select.narrow { width: 100px; }
.filter-date { width: 250px; }
.reset-btn { border: 1px solid #dcdfe6; color: #2c3e5d; }

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
  padding: 8px 2px 9px;
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
.ghost-btn { border: 1px solid #dcdfe6; color: #2c3e5d; }

.mat-table :deep(thead th) {
  background: #f0f6ff;
  color: #2c3e5d;
  font-weight: 600;
  font-size: 12px;
  padding: 6px 0;
}
.mat-table :deep(td) {
  font-size: 12px;
  color: #3b4a5f;
  padding: 5px 0;
}
.mat-table :deep(.cell) { padding: 0 6px; }
.mat-table :deep(.el-button + .el-button) { margin-left: 6px; }

.file-name { color: #1f2d3d; font-weight: 500; }
.opinion {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #5a6b80;
}
.opinion.dash { color: #c2cbd8; }

/* 材料类型药丸 */
.type-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.tp-design { color: #1878fd; background: #e3effe; }
.tp-thesis { color: #7c3aed; background: #ede9fe; }

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
.s-missing { color: #ee6d7a; background: #fdeef1; }

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
</style>
