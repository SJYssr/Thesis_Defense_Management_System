<template>
  <div class="teacher-info-page">
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

    <!-- 筛选区（两行 + 右下角按钮） -->
    <div class="filter-card">
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">教师姓名</span>
          <el-input v-model="query.name" placeholder="请输入教师姓名" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">工号</span>
          <el-input v-model="query.no" placeholder="请输入工号" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">所属学院</span>
          <el-select v-model="query.college" class="filter-select" placeholder="全部学院">
            <el-option label="全部学院" value="" />
            <el-option v-for="c in collegeOptions" :key="c" :label="c" :value="c" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">职称</span>
          <el-select v-model="query.title" class="filter-select" placeholder="全部职称">
            <el-option label="全部职称" value="" />
            <el-option label="教授" value="教授" />
            <el-option label="副教授" value="副教授" />
            <el-option label="讲师" value="讲师" />
          </el-select>
        </div>
      </div>
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">专业</span>
          <el-select v-model="query.major" class="filter-select" placeholder="全部专业">
            <el-option label="全部专业" value="" />
            <el-option v-for="m in majorOptions" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">状态</span>
          <el-select v-model="query.status" class="filter-select narrow" placeholder="全部状态">
            <el-option label="全部状态" value="" />
            <el-option label="在职" value="在职" />
            <el-option label="离职" value="离职" />
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
            <el-button type="primary" @click="onCreate"><el-icon><Plus /></el-icon>&nbsp;新增教师</el-button>
            <el-button class="ghost-btn check" @click="onBatchEnable"><el-icon><CircleCheck /></el-icon>&nbsp;批量启用</el-button>
            <el-button class="ghost-btn pause" @click="onBatchDisable"><el-icon><Remove /></el-icon>&nbsp;批量禁用</el-button>
            <el-button class="ghost-btn" @click="onBatchDelete"><el-icon><Delete /></el-icon>&nbsp;批量删除</el-button>
          </div>
          <div class="toolbar-right">
            <el-button class="ghost-btn" @click="onExport"><el-icon><Upload /></el-icon>&nbsp;导出数据</el-button>
            <el-button class="ghost-btn" @click="onBatchSetting"><el-icon><Setting /></el-icon>&nbsp;批量设置</el-button>
          </div>
        </div>

        <el-table :data="pagedRows" class="tc-table" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="34" />
          <el-table-column prop="index" label="序号" width="34" />
          <el-table-column prop="name" label="教师姓名" width="64" />
          <el-table-column prop="no" label="工号" width="72" />
          <el-table-column prop="college" label="所属学院" width="98" show-overflow-tooltip />
          <el-table-column prop="major" label="所属专业" width="128" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="major-text">{{ row.major }}</span>
            </template>
          </el-table-column>
          <el-table-column label="职称" width="60" align="center">
            <template #default="{ row }">
              <span class="title-pill" :class="row.titleClass">{{ row.title }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="phone" label="联系方式" width="88" show-overflow-tooltip />
          <el-table-column label="状态" width="52" align="center">
            <template #default="{ row }">
              <span class="status-tag" :class="row.status === '在职' ? 's-on' : 's-off'">{{ row.status }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="topicCount" label="题目数量" width="56" align="center" />
          <el-table-column prop="studentCount" label="指导学生数" width="68" align="center" />
          <el-table-column label="操作" width="114" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="onView(row)">查看</el-button>
              <el-button link type="primary" size="small" @click="onEdit(row)">编辑</el-button>
              <el-button v-if="row.status === '在职'" link type="danger" size="small" @click="onToggle(row, false)">禁用</el-button>
              <el-button v-else link type="success" size="small" @click="onToggle(row, true)">启用</el-button>
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
              v-model:current-page="page" :page-size="pageSize" :total="total" :pager-count="5"
              background layout="prev, pager, next, jumper" class="pager"
            />
          </div>
        </div>
      </div>

      <!-- 右：新增教师表单 -->
      <div class="form-card">
        <div class="form-head">
          <span class="form-title">新增教师</span>
          <el-icon class="close-btn" @click="onClose"><Close /></el-icon>
        </div>
        <el-form label-position="left" label-width="66px" class="tc-form">
          <el-form-item required>
            <template #label>教师姓名</template>
            <el-input v-model="form.name" size="small" placeholder="请输入教师姓名" />
          </el-form-item>
          <el-form-item required>
            <template #label>工号</template>
            <el-input v-model="form.no" size="small" placeholder="请输入工号" />
          </el-form-item>
          <el-form-item required>
            <template #label>所属学院</template>
            <el-select v-model="form.college" size="small" placeholder="请选择学院" style="width: 100%">
              <el-option v-for="c in collegeOptions" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item required>
            <template #label>所属专业</template>
            <el-select v-model="form.major" size="small" placeholder="请选择专业" style="width: 100%">
              <el-option v-for="m in majorOptions" :key="m" :label="m" :value="m" />
            </el-select>
          </el-form-item>
          <el-form-item required>
            <template #label>职称</template>
            <el-select v-model="form.title" size="small" placeholder="请选择职称" style="width: 100%">
              <el-option label="教授" value="教授" />
              <el-option label="副教授" value="副教授" />
              <el-option label="讲师" value="讲师" />
            </el-select>
          </el-form-item>
          <el-form-item required>
            <template #label>手机号</template>
            <el-input v-model="form.phone" size="small" placeholder="请输入手机号" />
          </el-form-item>
          <el-form-item>
            <template #label>邮箱</template>
            <el-input v-model="form.email" size="small" placeholder="请输入邮箱" />
          </el-form-item>
          <el-form-item required>
            <template #label>账号</template>
            <el-input v-model="form.account" size="small" placeholder="请输入登录账号" />
          </el-form-item>
          <el-form-item required>
            <template #label>密码</template>
            <el-input v-model="form.password" size="small" type="password" suffix-icon="View" placeholder="请输入密码" />
          </el-form-item>
          <el-form-item required>
            <template #label>确认密码</template>
            <el-input v-model="form.confirm" size="small" type="password" suffix-icon="View" placeholder="请再次输入密码" />
          </el-form-item>
          <el-form-item required>
            <template #label>状态</template>
            <el-radio-group v-model="form.status" size="small">
              <el-radio value="在职">在职</el-radio>
              <el-radio value="离职">离职</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item>
            <template #label>备注</template>
            <div class="remark-wrap">
              <el-input v-model="form.remark" size="small" type="textarea" :rows="2" placeholder="请输入备注信息" maxlength="200" />
              <div class="remark-counter">{{ form.remark.length }}/200</div>
            </div>
          </el-form-item>
        </el-form>
        <div class="form-footer">
          <el-button class="ghost-btn" @click="onClose">取消</el-button>
          <el-button type="primary" @click="onSave">保存</el-button>
        </div>
      </div>
    </div>

    <!-- 查看弹窗 -->
    <el-dialog v-model="viewVisible" :title="`${detail?.name}（${detail?.no}）`" width="560px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="所属学院">{{ detail?.college }}</el-descriptions-item>
        <el-descriptions-item label="所属专业">{{ detail?.major }}</el-descriptions-item>
        <el-descriptions-item label="职称">{{ detail?.title }}</el-descriptions-item>
        <el-descriptions-item label="联系方式">{{ detail?.phone }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ detail?.status }}</el-descriptions-item>
        <el-descriptions-item label="题目数量">{{ detail?.topicCount }}</el-descriptions-item>
        <el-descriptions-item label="指导学生数">{{ detail?.studentCount }}</el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button v-if="detail" type="danger" plain @click="onDelete(detail)">删除该教师</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { STAT_ICONS } from '@/components/statIcons'

const SHIELD_ICON = '<path d="M12 2 4 5v6c0 5 3.4 9.3 8 11 4.6-1.7 8-6 8-11V5l-8-3zm-1.2 13.6-3-3 1.4-1.4 1.6 1.6 5-5 1.4 1.4-6.4 6.4z"/>'
const PAUSE_ICON = '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zM10 8h2.4v8H10V8zm3.6 0H16v8h-2.4V8z"/>'
const USER_PLUS_ICON = '<path d="M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 2c-3.9 0-7 2-7 4.5V20h10v-1h4v-3.5c0-.7-.3-1.3-.8-1.8-1.2-1.1-3.5-1.7-6.2-1.7zm10 2v-3h-2v3h-3v2h3v3h2v-3h3v-2h-3z" transform="translate(-1 0)"/>'

/* 趋势色遵循设计稿：涨为红、跌为绿，"较去年"前缀灰 */
const statCards = [
  { label: '教师总数', value: 56, icon: STAT_ICONS.person, tileBg: '#d7f0fe', iconColor: '#2563eb', trend: '+12%', trendColor: '#ef4444', arrow: '↗' },
  { label: '在职教师', value: 50, icon: SHIELD_ICON, tileBg: '#ddfef0', iconColor: '#22c55e', ring: 89, ringColor: '#22c55e', ringBig: true, trend: '+9%', trendColor: '#ef4444', arrow: '↗' },
  { label: '离职教师', value: 4, icon: PAUSE_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b', ring: 7, ringColor: '#f59e0b', trend: '-33%', trendColor: '#22c55e', arrow: '↘' },
  { label: '本月新增', value: 3, icon: USER_PLUS_ICON, tileBg: '#edeafd', iconColor: '#8b5cf6', trend: '+200%', trendColor: '#ef4444', arrow: '↗' },
]

const collegeOptions = ['计算机学院', '人工智能学院', '电子信息学院', '管理学院', '经济管理学院', '数学与统计学院']
const majorOptions = ['计算机科学与技术', '软件工程', '人工智能', '网络工程', '电子信息工程', '信息管理与信息系统', '数据科学与大数据技术', '信息安全', '应用统计学', '智能科学与技术']

const TITLE_CLASS: Record<string, string> = {
  教授: 't-prof',
  副教授: 't-assoc',
  讲师: 't-lect',
}

interface TcRow {
  name: string
  no: string
  college: string
  major: string
  title: string
  phone: string
  status: string
  topicCount: number
  studentCount: number
}

const RAW_ROWS: TcRow[] = [
  { name: '李老师', no: 'T2024001', college: '计算机学院', major: '计算机科学与技术', title: '教授', phone: '13800138000', status: '在职', topicCount: 8, studentCount: 15 },
  { name: '王老师', no: 'T2024002', college: '计算机学院', major: '软件工程', title: '副教授', phone: '13900139001', status: '在职', topicCount: 6, studentCount: 12 },
  { name: '张老师', no: 'T2024003', college: '人工智能学院', major: '人工智能', title: '讲师', phone: '13900139002', status: '在职', topicCount: 5, studentCount: 10 },
  { name: '陈老师', no: 'T2024004', college: '计算机学院', major: '网络工程', title: '副教授', phone: '13800138003', status: '在职', topicCount: 7, studentCount: 14 },
  { name: '刘老师', no: 'T2024005', college: '电子信息学院', major: '电子信息工程', title: '讲师', phone: '13900139004', status: '在职', topicCount: 4, studentCount: 8 },
  { name: '黄老师', no: 'T2024006', college: '管理学院', major: '信息管理与信息系统', title: '副教授', phone: '13800138005', status: '在职', topicCount: 5, studentCount: 11 },
  { name: '林老师', no: 'T2024007', college: '经济管理学院', major: '数据科学与大数据技术', title: '讲师', phone: '13900139006', status: '在职', topicCount: 3, studentCount: 6 },
  { name: '何老师', no: 'T2024008', college: '计算机学院', major: '信息安全', title: '教授', phone: '13800138007', status: '在职', topicCount: 6, studentCount: 13 },
  { name: '宋老师', no: 'T2024009', college: '数学与统计学院', major: '应用统计学', title: '讲师', phone: '13900139008', status: '离职', topicCount: 2, studentCount: 4 },
  { name: '高老师', no: 'T2024010', college: '人工智能学院', major: '智能科学与技术', title: '副教授', phone: '13900139009', status: '在职', topicCount: 4, studentCount: 9 },
]

const rows = ref<TcRow[]>([...RAW_ROWS])
const total = 56
const page = ref(1)
const pageSize = ref(10)
const selected = ref<TcRow[]>([])

const query = reactive({ name: '', no: '', college: '', title: '', major: '', status: '' })

const filtered = computed(() => {
  return rows.value.filter((r) => {
    if (query.name && !r.name.includes(query.name)) return false
    if (query.no && !r.no.includes(query.no)) return false
    if (query.college && r.college !== query.college) return false
    if (query.title && r.title !== query.title) return false
    if (query.major && r.major !== query.major) return false
    if (query.status && r.status !== query.status) return false
    return true
  })
})

const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const slice = filtered.value.slice(start, start + pageSize.value)
  return slice.map((r, i) => ({ ...r, index: start + i + 1, titleClass: TITLE_CLASS[r.title] || 't-lect' }))
})

const form = reactive({
  name: '', no: '', college: '', major: '', title: '', phone: '',
  email: '', account: '', password: '', confirm: '', status: '在职', remark: '',
})

const viewVisible = ref(false)
const detail = ref<TcRow | null>(null)

function onSearch() {
  page.value = 1
}

function onReset() {
  query.name = ''
  query.no = ''
  query.college = ''
  query.title = ''
  query.major = ''
  query.status = ''
  page.value = 1
}

function onSelectionChange(sel: TcRow[]) {
  selected.value = sel
}

function onCreate() {
  onClose()
  ElMessage.info('请在右侧填写新教师信息')
}

function onView(row: TcRow) {
  detail.value = row
  viewVisible.value = true
}

function onEdit(row: TcRow) {
  form.name = row.name
  form.no = row.no
  form.college = row.college
  form.major = row.major
  form.title = row.title
  form.phone = row.phone
  form.status = row.status
  ElMessage.success('已载入信息，请在右侧编辑')
}

function onToggle(row: TcRow, enable: boolean) {
  ElMessageBox.confirm(`确定${enable ? '启用' : '禁用'}教师「${row.name}」吗？`, enable ? '启用' : '禁用', { type: enable ? 'success' : 'warning' })
    .then(() => {
      row.status = enable ? '在职' : '离职'
      ElMessage.success(enable ? '已启用' : '已禁用')
    })
    .catch(() => {})
}

function onDelete(row: TcRow) {
  ElMessageBox.confirm(`确定删除教师「${row.name}（${row.no}）」吗？`, '删除', { type: 'error' })
    .then(() => {
      rows.value = rows.value.filter((r) => r !== row)
      ElMessage.success('已删除')
    })
    .catch(() => {})
}

function onBatchEnable() {
  if (!selected.value.length) return ElMessage.warning('请先勾选教师')
  selected.value.forEach((s) => { s.status = '在职' })
  ElMessage.success('批量启用成功')
}

function onBatchDisable() {
  if (!selected.value.length) return ElMessage.warning('请先勾选教师')
  ElMessageBox.confirm(`确定批量禁用 ${selected.value.length} 位教师吗？`, '批量禁用', { type: 'warning' })
    .then(() => {
      selected.value.forEach((s) => { s.status = '离职' })
      ElMessage.success('批量禁用成功')
    })
    .catch(() => {})
}

function onBatchDelete() {
  if (!selected.value.length) return ElMessage.warning('请先勾选教师')
  ElMessageBox.confirm(`确定批量删除 ${selected.value.length} 位教师吗？`, '批量删除', { type: 'error' })
    .then(() => {
      const del = new Set(selected.value)
      rows.value = rows.value.filter((r) => !del.has(r))
      ElMessage.success('批量删除成功')
    })
    .catch(() => {})
}

function onBatchSetting() {
  if (!selected.value.length) return ElMessage.warning('请先勾选教师')
  ElMessage.info('批量设置功能演示')
}

function onExport() {
  ElMessage.success('导出成功')
}

function onClose() {
  Object.assign(form, { name: '', no: '', college: '', major: '', title: '', phone: '', email: '', account: '', password: '', confirm: '', status: '在职', remark: '' })
}

function onSave() {
  if (!form.name.trim()) return ElMessage.warning('请输入教师姓名')
  if (!form.no.trim()) return ElMessage.warning('请输入工号')
  if (!form.college) return ElMessage.warning('请选择学院')
  if (!form.major) return ElMessage.warning('请选择专业')
  if (!form.title) return ElMessage.warning('请选择职称')
  if (!form.phone.trim()) return ElMessage.warning('请输入手机号')
  if (!form.account.trim()) return ElMessage.warning('请输入登录账号')
  if (!form.password) return ElMessage.warning('请输入密码')
  if (form.password !== form.confirm) return ElMessage.warning('两次输入的密码不一致')
  rows.value.unshift({
    name: form.name,
    no: form.no,
    college: form.college,
    major: form.major,
    title: form.title,
    phone: form.phone,
    status: form.status,
    topicCount: 0,
    studentCount: 0,
  })
  ElMessage.success('保存成功')
  onClose()
}
</script>

<style scoped>
/* 统计卡（4 张） */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 10px;
}
.stat-card {
  position: relative;
  background: #fff;
  border-radius: 8px;
  padding: 9px 12px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  min-height: 72px;
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
  padding: 9px 14px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  margin-bottom: 10px;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.filter-row + .filter-row { margin-top: 5px; }
.filter-row.btn-row { justify-content: flex-end; margin-top: 6px; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }
.filter-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-label {
  font-size: 13px;
  color: #3b4a5f;
  white-space: nowrap;
}
.filter-input { width: 160px; }
.filter-select { width: 116px; }
.filter-select.narrow { width: 100px; }
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
  padding: 10px 14px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.toolbar-left,
.toolbar-right { display: flex; gap: 8px; }
.ghost-btn { border: 1px solid #dcdfe6; color: #2c3e5d; }
.ghost-btn.check { color: #22c55e; }
.ghost-btn.pause { color: #ef4444; }

.tc-table :deep(thead th) {
  background: #f0f6ff;
  color: #2c3e5d;
  font-weight: 600;
  font-size: 11px;
  padding: 6px 0;
}
.tc-table :deep(thead th .cell) {
  white-space: nowrap;
  font-size: 11px;
}
.tc-table :deep(td .cell) { white-space: nowrap; }
.tc-table :deep(td) {
  font-size: 12px;
  color: #3b4a5f;
  padding: 5px 0;
}
.tc-table :deep(.cell) { padding: 0 5px; }
.tc-table :deep(.el-button + .el-button) { margin-left: 5px; }
.major-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }

/* 职称药丸 */
.title-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.t-prof { color: #7c3aed; background: #ede9fe; }
.t-assoc { color: #1878fd; background: #e3effe; }
.t-lect { color: #e67e22; background: #fdf1e3; }

/* 状态药丸 */
.status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.s-on { color: #27ae60; background: #e4f7ec; }
.s-off { color: #ee6d7a; background: #fdeef1; }

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

/* 右侧新增表单 */
.form-card {
  width: 340px;
  flex-shrink: 0;
  background: #fff;
  border-radius: 8px;
  padding: 10px 16px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  display: flex;
  flex-direction: column;
}
.form-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 4px;
  border-bottom: 1px solid #eef2f8;
  margin-bottom: 4px;
}
.form-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
}
.close-btn {
  font-size: 16px;
  color: #97a3b4;
  cursor: pointer;
}
.tc-form { flex: 1; }
.tc-form :deep(.el-form-item) {
  margin-bottom: 5px;
}
.tc-form :deep(.el-form-item__label) {
  line-height: 22px;
  font-size: 12px;
}
.tc-form :deep(.el-input__wrapper),
.tc-form :deep(.el-select__wrapper) {
  min-height: 22px;
}
.tc-form :deep(.el-form-item__label) {
  font-size: 12.5px;
  color: #3b4a5f;
  white-space: nowrap;
}
.remark-wrap {
  width: 100%;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;
}
.remark-wrap :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  resize: none;
  font-size: 12.5px;
}
.remark-counter {
  text-align: right;
  font-size: 11px;
  color: #97a3b4;
  padding: 3px 8px;
  background: #fafbfd;
  border-top: 1px solid #eef2f8;
}
.form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 4px;
  border-top: 1px solid #eef2f8;
}
</style>
