<template>
  <div class="student-info-page">
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

    <!-- 筛选区（两行 + 右下按钮行） -->
    <div class="filter-card">
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">学生姓名</span>
          <el-input v-model="query.name" placeholder="请输入学生姓名" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">学号</span>
          <el-input v-model="query.no" placeholder="请输入学号" clearable class="filter-input" />
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
      </div>
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">班级</span>
          <el-select v-model="query.klass" class="filter-select" placeholder="全部班级">
            <el-option label="全部班级" value="" />
            <el-option v-for="k in klassOptions" :key="k" :label="k" :value="k" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">年级</span>
          <el-select v-model="query.grade" class="filter-select">
            <el-option label="2024届" value="2024届" />
            <el-option label="2023届" value="2023届" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">学生状态</span>
          <el-select v-model="query.status" class="filter-select" placeholder="全部状态">
            <el-option label="全部状态" value="" />
            <el-option label="在校" value="在校" />
            <el-option label="休学" value="休学" />
            <el-option label="毕业" value="毕业" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">是否选题</span>
          <el-select v-model="query.selected" class="filter-select" placeholder="全部状态">
            <el-option label="全部状态" value="" />
            <el-option label="已选题" value="已选题" />
            <el-option label="未选题" value="未选题" />
          </el-select>
        </div>
      </div>
      <div class="filter-row btn-row">
        <el-button type="primary" @click="onSearch"><el-icon><Search /></el-icon>&nbsp;查询</el-button>
        <el-button class="reset-btn" @click="onReset"><el-icon><Refresh /></el-icon>&nbsp;重置</el-button>
      </div>
    </div>

    <!-- 两栏：表格 + 新增表单 -->
    <div class="main-cols">
      <div class="table-card">
        <div class="toolbar">
          <div class="toolbar-left">
            <el-button type="primary" @click="onCreate"><el-icon><Plus /></el-icon>&nbsp;新增学生</el-button>
            <el-button class="ghost-btn" @click="onImport"><el-icon><Upload /></el-icon>&nbsp;批量导入</el-button>
            <el-button class="ghost-btn" @click="onBatchEnable"><el-icon class="ic-blue"><Check /></el-icon>&nbsp;批量启用</el-button>
            <el-button class="ghost-btn" @click="onBatchDisable"><el-icon class="ic-red"><svg viewBox="0 0 24 24" fill="currentColor" v-html="CIRCLE_PAUSE_ICON" /></el-icon>&nbsp;批量禁用</el-button>
            <el-button class="ghost-btn" @click="onBatchDelete"><el-icon><Delete /></el-icon>&nbsp;批量删除</el-button>
          </div>
          <div class="toolbar-right">
            <el-button class="ghost-btn" @click="onExport"><el-icon><Download /></el-icon>&nbsp;导出数据</el-button>
            <el-button class="ghost-btn" @click="onBatchSetting"><el-icon><Setting /></el-icon>&nbsp;批量设置</el-button>
          </div>
        </div>

        <el-table :data="pagedRows" class="st-table" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="36" />
          <el-table-column prop="index" label="序号" min-width="44" />
          <el-table-column prop="no" label="学号" min-width="70" />
          <el-table-column prop="name" label="学生姓名" min-width="72" />
          <el-table-column prop="college" label="学院" min-width="108" show-overflow-tooltip />
          <el-table-column prop="major" label="专业" min-width="150" show-overflow-tooltip />
          <el-table-column prop="klass" label="班级" min-width="72" />
          <el-table-column prop="grade" label="年级" min-width="62" />
          <el-table-column label="学生状态" min-width="78" align="center">
            <template #default="{ row }">
              <span class="status-pill" :class="row.statusClass">{{ row.status }}</span>
            </template>
          </el-table-column>
          <el-table-column label="是否选题" min-width="78" align="center">
            <template #default="{ row }">
              <span class="sel-pill" :class="row.selected === '已选题' ? 'sp-yes' : 'sp-no'">{{ row.selected }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="teacher" label="指导教师" min-width="72" />
          <el-table-column prop="phone" label="联系电话" min-width="102" show-overflow-tooltip />
          <el-table-column label="操作" min-width="126" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="onView(row)">查看</el-button>
              <el-button link type="primary" size="small" @click="onEdit(row)">编辑</el-button>
              <el-button v-if="row.status !== '休学'" link type="danger" size="small" @click="onToggle(row, false)">禁用</el-button>
              <el-button v-else link type="success" size="small" @click="onToggle(row, true)">启用</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-bar">
          <span class="page-total">共 {{ total }} 条数据</span>
          <div class="pager-group">
            <el-select v-model="pageSize" class="page-size">
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

    </div>

    <!-- 新增/编辑学生弹窗（屏幕居中） -->
    <el-dialog
      v-model="panelVisible"
      :title="editMode ? '编辑学生' : '新增学生'"
      width="600px"
      align-center
      class="st-dialog"
    >
      <el-form label-position="left" label-width="90px" class="st-form">
        <el-form-item required>
          <template #label>学号</template>
          <el-input v-model="form.no" placeholder="请输入学号" />
        </el-form-item>
        <el-form-item required>
          <template #label>姓名</template>
          <el-input v-model="form.name" placeholder="请输入学生姓名" />
        </el-form-item>
        <el-form-item required>
          <template #label>学院</template>
          <el-select v-model="form.college" placeholder="请选择学院" style="width: 100%">
            <el-option v-for="c in collegeOptions" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item required>
          <template #label>专业</template>
          <el-select v-model="form.major" placeholder="请选择专业" style="width: 100%">
            <el-option v-for="m in majorOptions" :key="m" :label="m" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item required>
          <template #label>班级</template>
          <el-select v-model="form.klass" placeholder="请选择班级" style="width: 100%">
            <el-option v-for="k in klassOptions" :key="k" :label="k" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item required>
          <template #label>年级</template>
          <el-select v-model="form.grade" style="width: 100%">
            <el-option label="2024届" value="2024届" />
            <el-option label="2023届" value="2023届" />
          </el-select>
        </el-form-item>
        <el-form-item required>
          <template #label>性别</template>
          <el-radio-group v-model="form.gender">
            <el-radio value="男">男</el-radio>
            <el-radio value="女">女</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item required>
          <template #label>联系电话</template>
          <el-input v-model="form.phone" placeholder="请输入联系电话" />
        </el-form-item>
        <el-form-item>
          <template #label>邮箱</template>
          <el-input v-model="form.email" placeholder="请输入邮箱" />
        </el-form-item>
        <el-form-item>
          <template #label>学生状态</template>
          <el-radio-group v-model="form.status">
            <el-radio value="在校">在校</el-radio>
            <el-radio value="休学">休学</el-radio>
            <el-radio value="毕业">毕业</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item>
          <template #label>备注</template>
          <el-input
            v-model="form.remark" type="textarea" :rows="3" placeholder="请输入备注信息"
            maxlength="200" show-word-limit
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="onClose">取消</el-button>
          <el-button type="primary" @click="onSave">保存</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 查看弹窗 -->
    <el-dialog v-model="viewVisible" :title="`${detail?.name}（${detail?.no}）`" width="560px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="学院">{{ detail?.college }}</el-descriptions-item>
        <el-descriptions-item label="专业">{{ detail?.major }}</el-descriptions-item>
        <el-descriptions-item label="班级">{{ detail?.klass }}</el-descriptions-item>
        <el-descriptions-item label="年级">{{ detail?.grade }}</el-descriptions-item>
        <el-descriptions-item label="学生状态">{{ detail?.status }}</el-descriptions-item>
        <el-descriptions-item label="是否选题">{{ detail?.selected }}</el-descriptions-item>
        <el-descriptions-item label="指导教师">{{ detail?.teacher }}</el-descriptions-item>
        <el-descriptions-item label="联系电话">{{ detail?.phone }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { STAT_ICONS } from '@/components/statIcons'

const CAP_ICON = '<path d="M12 3 1.5 8.2 12 13.4l8.6-4.6v5.8h2V8.2L12 3zm-6.5 9v4.2c0 1.5 2.9 3 6.5 3s6.5-1.5 6.5-3V12l-6.5 3.5L5.5 12z"/>'
/* 批量禁用：红色描边圆圈 + 暂停条 */
const CIRCLE_PAUSE_ICON = '<path fill-rule="evenodd" d="M12 2.6a9.4 9.4 0 1 0 0 18.8 9.4 9.4 0 0 0 0-18.8zm0 2a7.4 7.4 0 1 1 0 14.8 7.4 7.4 0 0 1 0-14.8zM9.4 8.2h2.1v7.6H9.4zM12.5 8.2h2.1v7.6h-2.1z"/>'
/* 休学/异常：红色人像 + 感叹号 */
const USER_ALERT_ICON = '<circle cx="9" cy="7.5" r="4"/><path d="M9 12.5c-3.9 0-7 2-7 4.5V20h10v-1h4v-3.5c0-.7-.3-1.3-.8-1.8-1.2-1.1-3.5-1.7-6.2-1.7z"/><path d="M19.2 5h2v6h-2zm0 8h2v2h-2z"/>'

/* 趋势色遵循设计稿：涨为红、跌为绿，"较去年"前缀灰 */
const statCards = [
  { label: '学生总数', value: 356, icon: STAT_ICONS.users, tileBg: '#d7f0fe', iconColor: '#2563eb', trend: '+12%', trendColor: '#ef4444', arrow: '↗' },
  { label: '在校学生', value: 328, icon: CAP_ICON, tileBg: '#ddfef0', iconColor: '#22c55e', ring: 92, ringColor: '#22c55e', ringBig: true, trend: '+8%', trendColor: '#ef4444', arrow: '↗' },
  { label: '毕业学生', value: 28, icon: CAP_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b', ring: 8, ringColor: '#f59e0b', trend: '+27%', trendColor: '#ef4444', arrow: '↗' },
  { label: '休学/异常', value: 4, icon: USER_ALERT_ICON, tileBg: '#fee7ed', iconColor: '#ef4444', trend: '-33%', trendColor: '#22c55e', arrow: '↘' },
]

const collegeOptions = ['计算机学院', '人工智能学院', '电子信息学院', '管理学院', '经济管理学院', '数学与统计学院']
const majorOptions = ['计算机科学与技术', '软件工程', '人工智能', '网络工程', '电子信息工程', '信息管理与信息系统', '数据科学与大数据技术', '信息安全', '应用统计学', '智能科学与技术']
const klassOptions = ['计科2101', '软工2101', '人工2101', '网工2101', '电信2101', '信管2101', '数科2101', '信安2101', '应统2101', '智能2101']

const STATUS_CLASS: Record<string, string> = {
  在校: 'st-on',
  休学: 'st-rest',
  毕业: 'st-grad',
}

interface StRow {
  no: string
  name: string
  college: string
  major: string
  klass: string
  grade: string
  status: string
  selected: string
  teacher: string
  phone: string
}

const RAW_ROWS: StRow[] = [
  { no: '2021001', name: '张三', college: '计算机学院', major: '计算机科学与技术', klass: '计科2101', grade: '2024届', status: '在校', selected: '已选题', teacher: '李老师', phone: '13800138000' },
  { no: '2021002', name: '李四', college: '计算机学院', major: '软件工程', klass: '软工2101', grade: '2024届', status: '在校', selected: '已选题', teacher: '王老师', phone: '13900139001' },
  { no: '2021003', name: '王五', college: '人工智能学院', major: '人工智能', klass: '人工2101', grade: '2024届', status: '在校', selected: '未选题', teacher: '-', phone: '13800139002' },
  { no: '2021004', name: '赵六', college: '计算机学院', major: '网络工程', klass: '网工2101', grade: '2024届', status: '在校', selected: '已选题', teacher: '陈老师', phone: '13900139003' },
  { no: '2021005', name: '孙七', college: '电子信息学院', major: '电子信息工程', klass: '电信2101', grade: '2024届', status: '在校', selected: '已选题', teacher: '刘老师', phone: '13800139004' },
  { no: '2021006', name: '周八', college: '管理学院', major: '信息管理与信息系统', klass: '信管2101', grade: '2024届', status: '在校', selected: '未选题', teacher: '-', phone: '13900139005' },
  { no: '2021007', name: '吴九', college: '经济管理学院', major: '数据科学与大数据技术', klass: '数科2101', grade: '2024届', status: '休学', selected: '未选题', teacher: '-', phone: '13800139006' },
  { no: '2021008', name: '郑十', college: '计算机学院', major: '信息安全', klass: '信安2101', grade: '2024届', status: '在校', selected: '已选题', teacher: '何老师', phone: '13900139007' },
  { no: '2021009', name: '刘一', college: '数学与统计学院', major: '应用统计学', klass: '应统2101', grade: '2024届', status: '在校', selected: '已选题', teacher: '宋老师', phone: '13800139008' },
  { no: '2021010', name: '陈二', college: '人工智能学院', major: '智能科学与技术', klass: '智能2101', grade: '2024届', status: '毕业', selected: '已选题', teacher: '高老师', phone: '13900139009' },
]

const rows = ref<StRow[]>([...RAW_ROWS])
const total = 356
const page = ref(1)
const pageSize = ref(10)
const selected = ref<StRow[]>([])

const query = reactive({ name: '', no: '', college: '', major: '', klass: '', grade: '2024届', status: '', selected: '' })

const filtered = computed(() => {
  return rows.value.filter((r) => {
    if (query.name && !r.name.includes(query.name)) return false
    if (query.no && !r.no.includes(query.no)) return false
    if (query.college && r.college !== query.college) return false
    if (query.major && r.major !== query.major) return false
    if (query.klass && r.klass !== query.klass) return false
    if (query.status && r.status !== query.status) return false
    if (query.selected && r.selected !== query.selected) return false
    return true
  })
})

const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const slice = filtered.value.slice(start, start + pageSize.value)
  return slice.map((r, i) => ({ ...r, index: start + i + 1, statusClass: STATUS_CLASS[r.status] || 'st-on' }))
})

const form = reactive({
  no: '', name: '', college: '', major: '', klass: '', grade: '2024届',
  gender: '男', phone: '', email: '', status: '在校', remark: '',
})

const viewVisible = ref(false)
const detail = ref<StRow | null>(null)
/* 右侧新增/编辑表单弹窗：默认隐藏，点"新增学生"或行内"编辑"时居中显示 */
const panelVisible = ref(false)
const editMode = ref(false)
const editingRow = ref<StRow | null>(null)

function onSearch() {
  page.value = 1
}

function onReset() {
  query.name = ''
  query.no = ''
  query.college = ''
  query.major = ''
  query.klass = ''
  query.grade = '2024届'
  query.status = ''
  query.selected = ''
  page.value = 1
}

function onSelectionChange(sel: StRow[]) {
  selected.value = sel
}

function onCreate() {
  onClose()
  panelVisible.value = true
}

function onView(row: StRow) {
  detail.value = row
  viewVisible.value = true
}

function onEdit(row: StRow) {
  form.no = row.no
  form.name = row.name
  form.college = row.college
  form.major = row.major
  form.klass = row.klass
  form.grade = row.grade
  form.phone = row.phone
  form.status = row.status
  editMode.value = true
  editingRow.value = row
  panelVisible.value = true
}

function onToggle(row: StRow, enable: boolean) {
  ElMessageBox.confirm(`确定${enable ? '启用' : '禁用'}学生「${row.name}」吗？`, enable ? '启用' : '禁用', { type: enable ? 'success' : 'warning' })
    .then(() => {
      row.status = enable ? '在校' : '休学'
      ElMessage.success(enable ? '已启用' : '已禁用')
    })
    .catch(() => {})
}

function onImport() {
  ElMessage.info('批量导入功能演示')
}

function onBatchEnable() {
  if (!selected.value.length) return ElMessage.warning('请先勾选学生')
  selected.value.forEach((s) => { s.status = '在校' })
  ElMessage.success('批量启用成功')
}

function onBatchDisable() {
  if (!selected.value.length) return ElMessage.warning('请先勾选学生')
  ElMessageBox.confirm(`确定批量禁用 ${selected.value.length} 名学生吗？`, '批量禁用', { type: 'warning' })
    .then(() => {
      selected.value.forEach((s) => { s.status = '休学' })
      ElMessage.success('批量禁用成功')
    })
    .catch(() => {})
}

function onBatchDelete() {
  if (!selected.value.length) return ElMessage.warning('请先勾选学生')
  ElMessageBox.confirm(`确定批量删除 ${selected.value.length} 名学生吗？`, '批量删除', { type: 'error' })
    .then(() => {
      const del = new Set(selected.value)
      rows.value = rows.value.filter((r) => !del.has(r))
      ElMessage.success('批量删除成功')
    })
    .catch(() => {})
}

function onBatchSetting() {
  if (!selected.value.length) return ElMessage.warning('请先勾选学生')
  ElMessage.info('批量设置功能演示')
}

function onExport() {
  ElMessage.success('导出成功')
}

function onClose() {
  Object.assign(form, { no: '', name: '', college: '', major: '', klass: '', grade: '2024届', gender: '男', phone: '', email: '', status: '在校', remark: '' })
  editMode.value = false
  editingRow.value = null
  panelVisible.value = false
}

function onSave() {
  if (!form.no.trim()) return ElMessage.warning('请输入学号')
  if (!form.name.trim()) return ElMessage.warning('请输入姓名')
  if (!form.college) return ElMessage.warning('请选择学院')
  if (!form.major) return ElMessage.warning('请选择专业')
  if (!form.klass) return ElMessage.warning('请选择班级')
  if (!form.phone.trim()) return ElMessage.warning('请输入联系电话')
  const payload = {
    no: form.no,
    name: form.name,
    college: form.college,
    major: form.major,
    klass: form.klass,
    grade: form.grade,
    status: form.status,
    phone: form.phone,
  }
  if (editMode.value && editingRow.value) {
    Object.assign(editingRow.value, payload)
  } else {
    rows.value.unshift({ ...payload, selected: '未选题', teacher: '-' })
  }
  ElMessage.success('保存成功')
  onClose()
}
</script>

<style scoped>
/* ===== 统计卡（4 张，通栏） ===== */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 12px;
}
.stat-card {
  position: relative;
  background: #fff;
  border-radius: 10px;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  min-height: 104px;
}
.stat-icon {
  width: 58px;
  height: 58px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-icon svg { width: 30px; height: 30px; }
.stat-info { flex: 1; min-width: 0; padding-right: 40px; }
.stat-label {
  font-size: 14px;
  color: #8a97ad;
}
.stat-num {
  font-size: 30px;
  font-weight: 700;
  color: #1f2d3d;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
  line-height: 1.15;
  margin-top: 3px;
}
.stat-trend {
  font-size: 13px;
  margin-top: 5px;
}
.trend-prefix {
  color: #8a97ad;
}
.stat-ring {
  --pct: 0;
  --ring-color: #f59e0b;
  position: absolute;
  right: 16px;
  top: 50%;
  transform: translateY(-50%);
  width: 48px;
  height: 48px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(closest-side, #fff 74%, transparent 75% 100%),
    conic-gradient(var(--ring-color) calc(var(--pct) * 1%), #eef1f6 0);
}
.stat-ring.big { width: 62px; height: 62px; }
.stat-ring span {
  font-size: 12px;
  font-weight: 600;
  color: #2c3e5d;
}
.stat-ring.big span { font-size: 13px; }

/* ===== 筛选区（两行 + 右下按钮行） ===== */
.filter-card {
  background: #fff;
  border-radius: 10px;
  padding: 14px 20px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  margin-bottom: 12px;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 28px;
}
.filter-row + .filter-row { margin-top: 12px; }
.filter-row.btn-row { justify-content: flex-end; margin-top: 12px; gap: 12px; }
.filter-item {
  display: flex;
  align-items: center;
  gap: 10px;
}
.filter-label {
  font-size: 14px;
  font-weight: 600;
  color: #1f2d3d;
  white-space: nowrap;
}
.filter-input { width: 172px; }
.filter-select { width: 150px; }
.filter-card :deep(.el-input__wrapper) { padding: 5px 12px; }
.filter-card :deep(.el-select__wrapper) { min-height: 34px; }
.btn-row :deep(.el-button) {
  height: 36px;
  padding: 0 22px;
  font-size: 14px;
  border-radius: 6px;
}
.reset-btn { border: 1px solid #dcdfe6; color: #2c3e5d; }

/* ===== 表格卡 ===== */
.table-card {
  flex: 1;
  min-width: 0;
  background: #fff;
  border-radius: 10px;
  padding: 12px 18px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.toolbar-left,
.toolbar-right { display: flex; gap: 10px; }
.toolbar :deep(.el-button) {
  height: 38px;
  padding: 0 16px;
  font-size: 14px;
  border-radius: 6px;
}
.ghost-btn { border: 1px solid #dfe4ee; color: #2c3e5d; font-weight: 500; }
.ic-blue { color: #1878fd; font-size: 17px; }
.ic-red { color: #e83a3a; font-size: 17px; }

.st-table :deep(thead th) {
  background: #eaf3fe;
  color: #2c3e5d;
  font-weight: 600;
  font-size: 13px;
  padding: 8px 0;
  border-bottom: none;
}
.st-table :deep(thead th .cell) { white-space: nowrap; }
.st-table :deep(td .cell) { white-space: nowrap; }
.st-table :deep(td) {
  font-size: 13px;
  color: #3b4a5f;
  padding: 7px 0;
}
.st-table :deep(.cell) { padding: 0 6px; }
.st-table :deep(.el-button + .el-button) { margin-left: 8px; }
.st-table :deep(.el-button.is-link) { font-size: 13px; }

/* 状态/选题药丸 */
.status-pill,
.sel-pill {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 12px;
  line-height: 18px;
}
.st-on { color: #27ae60; background: #e4f7ec; }
.st-rest { color: #e67e22; background: #fdf1e3; }
.st-grad { color: #1878fd; background: #e3effe; }
.sp-yes { color: #27ae60; background: #e4f7ec; }
.sp-no { color: #e74c3c; background: #fdeaea; }

/* 分页 */
.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 4px 0;
}
.pager-group {
  display: flex;
  align-items: center;
}
.page-total {
  font-size: 13.5px;
  color: #2c3e5d;
  margin-right: 16px;
}
.page-size { width: 104px; margin-right: 14px; }
/* 未激活页码：白底描边（与设计稿一致） */
.pager :deep(.el-pager li) {
  background: #fff;
  border: 1px solid #e4e9f2;
}
.pager :deep(.el-pager li.is-active) { border-color: #1878fd; }
.pager :deep(.btn-prev),
.pager :deep(.btn-next) { background: #f5f7fb; }

/* 新增/编辑学生弹窗 */
.st-form :deep(.el-form-item) {
  margin-bottom: 14px;
}
.st-form :deep(.el-form-item__label) {
  font-size: 13.5px;
  font-weight: 600;
  color: #1f2d3d;
  white-space: nowrap;
  padding-right: 8px;
}
.st-form :deep(.el-radio-group) { flex-wrap: nowrap; }
.st-form :deep(.el-radio__label) { font-size: 13.5px; color: #2c3e5d; }
.st-form :deep(.el-textarea) { width: 100%; }
.st-form :deep(.el-textarea__inner) { resize: none; }
.st-form :deep(.el-textarea .el-input__count) {
  background: transparent;
  bottom: 4px;
  right: 10px;
  color: #a8b3c5;
}
.dialog-footer {
  display: flex;
  gap: 12px;
}
.dialog-footer :deep(.el-button) {
  flex: 1;
  height: 40px;
  font-size: 15px;
  border-radius: 6px;
}
</style>
