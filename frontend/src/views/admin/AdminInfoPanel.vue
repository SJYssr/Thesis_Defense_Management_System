<template>
  <div class="admin-info-page">
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
        <div class="filter-item">
          <span class="filter-label">管理员姓名</span>
          <el-input v-model="query.name" placeholder="请输入管理员姓名" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">账号</span>
          <el-input v-model="query.username" placeholder="请输入账号" clearable class="filter-input" />
        </div>
        <div class="filter-item">
          <span class="filter-label">角色</span>
          <el-select v-model="query.role" class="filter-select" placeholder="全部角色">
            <el-option label="全部角色" value="" />
            <el-option v-for="r in roleOptions" :key="r" :label="r" :value="r" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">状态</span>
          <el-select v-model="query.status" class="filter-select narrow" placeholder="全部状态">
            <el-option label="全部状态" value="" />
            <el-option label="启用" value="启用" />
            <el-option label="禁用" value="禁用" />
          </el-select>
        </div>
        <div class="filter-actions">
          <el-button type="primary" @click="onSearch"><el-icon><Search /></el-icon>&nbsp;查询</el-button>
          <el-button class="reset-btn" @click="onReset"><el-icon><Refresh /></el-icon>&nbsp;重置</el-button>
        </div>
      </div>
    </div>

    <!-- 两栏：表格 + 新增表单 -->
    <div class="main-cols">
      <!-- 左：表格卡 -->
      <div class="table-card">
        <div class="toolbar">
          <el-button type="primary" @click="onCreate"><el-icon><Plus /></el-icon>&nbsp;新增管理员</el-button>
          <el-button class="ghost-btn check" @click="onBatchEnable"><el-icon><CircleCheck /></el-icon>&nbsp;批量启用</el-button>
          <el-button class="ghost-btn pause" @click="onBatchDisable"><el-icon><VideoPause /></el-icon>&nbsp;批量禁用</el-button>
          <el-button class="ghost-btn" @click="onBatchDelete"><el-icon><Delete /></el-icon>&nbsp;批量删除</el-button>
        </div>

        <el-table :data="pagedRows" class="adm-table" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="38" />
          <el-table-column prop="index" label="序号" width="44" />
          <el-table-column prop="username" label="用户名" width="86" />
          <el-table-column prop="name" label="姓名" width="76" />
          <el-table-column label="角色" width="88" align="center">
            <template #default="{ row }">
              <span class="role-pill" :class="row.roleClass">{{ row.role }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="dept" label="所属部门" width="92" />
          <el-table-column prop="phone" label="手机号" width="106" />
          <el-table-column label="状态" width="68" align="center">
            <template #default="{ row }">
              <span class="status-tag" :class="row.status === '启用' ? 's-on' : 's-off'">{{ row.status }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="lastLogin" label="最后登录时间" width="122" />
          <el-table-column label="操作" width="118" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="onEdit(row)">编辑</el-button>
              <el-button v-if="row.status === '启用'" link type="primary" size="small" @click="onToggle(row, false)">禁用</el-button>
              <el-button v-else link type="primary" size="small" @click="onToggle(row, true)">启用</el-button>
              <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
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

      <!-- 右：新增管理员表单 -->
      <div class="form-card">
        <div class="form-head">
          <span class="form-title">新增管理员</span>
          <el-icon class="close-btn" @click="onClose"><Close /></el-icon>
        </div>
        <el-form label-position="left" label-width="66px" class="adm-form">
          <el-form-item required>
            <template #label>用户名</template>
            <el-input v-model="form.username" placeholder="请输入用户名" />
          </el-form-item>
          <el-form-item required>
            <template #label>密码</template>
            <el-input v-model="form.password" type="password" suffix-icon="View" placeholder="请输入密码" />
          </el-form-item>
          <el-form-item required>
            <template #label>确认密码</template>
            <el-input v-model="form.confirm" type="password" suffix-icon="View" placeholder="请再次输入密码" />
          </el-form-item>
          <el-form-item required>
            <template #label>姓名</template>
            <el-input v-model="form.name" placeholder="请输入姓名" />
          </el-form-item>
          <el-form-item required>
            <template #label>手机号</template>
            <el-input v-model="form.phone" placeholder="请输入手机号" />
          </el-form-item>
          <el-form-item required>
            <template #label>角色</template>
            <el-select v-model="form.role" placeholder="请选择角色" style="width: 100%">
              <el-option v-for="r in roleOptions" :key="r" :label="r" :value="r" />
            </el-select>
          </el-form-item>
          <el-form-item required>
            <template #label>所属部门</template>
            <el-select v-model="form.dept" placeholder="请选择部门" style="width: 100%">
              <el-option v-for="d in deptOptions" :key="d" :label="d" :value="d" />
            </el-select>
          </el-form-item>
          <el-form-item required>
            <template #label>状态</template>
            <el-radio-group v-model="form.status">
              <el-radio value="启用">启用</el-radio>
              <el-radio value="禁用">禁用</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item>
            <template #label>备注</template>
            <div class="remark-wrap">
              <el-input v-model="form.remark" type="textarea" :rows="3" placeholder="请输入备注信息" maxlength="200" />
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
  { label: '管理员总数', value: 12, icon: STAT_ICONS.users, tileBg: '#d7f0fe', iconColor: '#2563eb', trend: '+20%', trendColor: '#ef4444', arrow: '↗' },
  { label: '启用状态', value: 10, icon: SHIELD_ICON, tileBg: '#ddfef0', iconColor: '#22c55e', ring: 83, ringColor: '#22c55e', ringBig: true, trend: '+11%', trendColor: '#ef4444', arrow: '↗' },
  { label: '禁用状态', value: 2, icon: PAUSE_ICON, tileBg: '#fef5ea', iconColor: '#f59e0b', ring: 17, ringColor: '#f59e0b', trend: '-33%', trendColor: '#22c55e', arrow: '↘' },
  { label: '本月新增', value: 2, icon: USER_PLUS_ICON, tileBg: '#edeafd', iconColor: '#8b5cf6', trend: '+100%', trendColor: '#ef4444', arrow: '↗' },
]

const roleOptions = ['超级管理员', '管理员', '教务管理员', '学院管理员']
const deptOptions = ['信息中心', '教务处', '计算机学院', '人工智能学院', '软件工程学院']

const ROLE_CLASS: Record<string, string> = {
  超级管理员: 'r-super',
  管理员: 'r-admin',
  教务管理员: 'r-academic',
  学院管理员: 'r-college',
}

interface AdmRow {
  username: string
  name: string
  role: string
  dept: string
  phone: string
  status: string
  lastLogin: string
}

const RAW_ROWS: AdmRow[] = [
  { username: 'admin', name: '系统管理员', role: '超级管理员', dept: '信息中心', phone: '13800138000', status: '启用', lastLogin: '2024-05-20 14:30' },
  { username: 'zhangsan', name: '张三', role: '管理员', dept: '教务处', phone: '13900139001', status: '启用', lastLogin: '2024-05-19 10:25' },
  { username: 'lisi', name: '李四', role: '管理员', dept: '信息中心', phone: '13900139002', status: '启用', lastLogin: '2024-05-18 16:40' },
  { username: 'wangwu', name: '王五', role: '教务管理员', dept: '教务处', phone: '13900139003', status: '启用', lastLogin: '2024-05-17 11:20' },
  { username: 'zhaoliu', name: '赵六', role: '学院管理员', dept: '计算机学院', phone: '13900139004', status: '禁用', lastLogin: '2024-05-10 09:15' },
  { username: 'sunqi', name: '孙七', role: '学院管理员', dept: '人工智能学院', phone: '13900139005', status: '启用', lastLogin: '2024-05-16 15:30' },
  { username: 'zhouba', name: '周八', role: '教务管理员', dept: '教务处', phone: '13900139006', status: '启用', lastLogin: '2024-05-15 13:10' },
  { username: 'wujiu', name: '吴九', role: '管理员', dept: '信息中心', phone: '13900139007', status: '启用', lastLogin: '2024-05-14 17:05' },
  { username: 'zhengshi', name: '郑十', role: '学院管理员', dept: '软件工程学院', phone: '13900139008', status: '禁用', lastLogin: '2024-05-12 08:40' },
  { username: 'liuyi', name: '刘一', role: '管理员', dept: '信息中心', phone: '13900139009', status: '启用', lastLogin: '2024-05-11 19:20' },
]

const rows = ref<AdmRow[]>([...RAW_ROWS])
const total = 12
const page = ref(1)
const pageSize = ref(10)
const selected = ref<AdmRow[]>([])

const query = reactive({ name: '', username: '', role: '', status: '' })

const filtered = computed(() => {
  return rows.value.filter((r) => {
    if (query.name && !r.name.includes(query.name)) return false
    if (query.username && !r.username.includes(query.username)) return false
    if (query.role && r.role !== query.role) return false
    if (query.status && r.status !== query.status) return false
    return true
  })
})

const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value
  const slice = filtered.value.slice(start, start + pageSize.value)
  return slice.map((r, i) => ({ ...r, index: start + i + 1, roleClass: ROLE_CLASS[r.role] || 'r-admin' }))
})

const form = reactive({
  username: '',
  password: '',
  confirm: '',
  name: '',
  phone: '',
  role: '',
  dept: '',
  status: '启用',
  remark: '',
})

function onSearch() {
  page.value = 1
}

function onReset() {
  query.name = ''
  query.username = ''
  query.role = ''
  query.status = ''
  page.value = 1
}

function onSelectionChange(sel: AdmRow[]) {
  selected.value = sel
}

function onCreate() {
  Object.assign(form, { username: '', password: '', confirm: '', name: '', phone: '', role: '', dept: '', status: '启用', remark: '' })
  ElMessage.info('请在右侧填写新管理员信息')
}

function onEdit(row: AdmRow) {
  form.username = row.username
  form.name = row.name
  form.phone = row.phone
  form.role = row.role
  form.dept = row.dept
  form.status = row.status
  ElMessage.success('已载入信息，请在右侧编辑')
}

function onToggle(row: AdmRow, enable: boolean) {
  ElMessageBox.confirm(`确定${enable ? '启用' : '禁用'}账号「${row.username}」吗？`, enable ? '启用' : '禁用', { type: enable ? 'success' : 'warning' })
    .then(() => {
      row.status = enable ? '启用' : '禁用'
      ElMessage.success(enable ? '已启用' : '已禁用')
    })
    .catch(() => {})
}

function onDelete(row: AdmRow) {
  ElMessageBox.confirm(`确定删除账号「${row.username}（${row.name}）」吗？`, '删除', { type: 'error' })
    .then(() => {
      rows.value = rows.value.filter((r) => r !== row)
      ElMessage.success('已删除')
    })
    .catch(() => {})
}

function onBatchEnable() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选账号')
    return
  }
  selected.value.forEach((s) => { s.status = '启用' })
  ElMessage.success('批量启用成功')
}

function onBatchDisable() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选账号')
    return
  }
  ElMessageBox.confirm(`确定批量禁用 ${selected.value.length} 个账号吗？`, '批量禁用', { type: 'warning' })
    .then(() => {
      selected.value.forEach((s) => { s.status = '禁用' })
      ElMessage.success('批量禁用成功')
    })
    .catch(() => {})
}

function onBatchDelete() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选账号')
    return
  }
  ElMessageBox.confirm(`确定批量删除 ${selected.value.length} 个账号吗？`, '批量删除', { type: 'error' })
    .then(() => {
      const del = new Set(selected.value)
      rows.value = rows.value.filter((r) => !del.has(r))
      ElMessage.success('批量删除成功')
    })
    .catch(() => {})
}

function onClose() {
  Object.assign(form, { username: '', password: '', confirm: '', name: '', phone: '', role: '', dept: '', status: '启用', remark: '' })
}

function onSave() {
  if (!form.username.trim()) return ElMessage.warning('请输入用户名')
  if (!form.password) return ElMessage.warning('请输入密码')
  if (form.password !== form.confirm) return ElMessage.warning('两次输入的密码不一致')
  if (!form.name.trim()) return ElMessage.warning('请输入姓名')
  if (!form.phone.trim()) return ElMessage.warning('请输入手机号')
  if (!form.role) return ElMessage.warning('请选择角色')
  if (!form.dept) return ElMessage.warning('请选择部门')
  rows.value.unshift({
    username: form.username,
    name: form.name,
    role: form.role,
    dept: form.dept,
    phone: form.phone,
    status: form.status,
    lastLogin: '-',
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
.filter-label {
  font-size: 13px;
  color: #3b4a5f;
  white-space: nowrap;
}
.filter-input { width: 168px; }
.filter-select { width: 116px; }
.filter-select.narrow { width: 100px; }
.filter-actions { margin-left: auto; display: flex; gap: 8px; }
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
  gap: 8px;
  margin-bottom: 10px;
}
.ghost-btn { border: 1px solid #dcdfe6; color: #2c3e5d; }
.ghost-btn.check { color: #22c55e; }
.ghost-btn.pause { color: #ef4444; }

.adm-table :deep(thead th) {
  background: #f0f6ff;
  color: #2c3e5d;
  font-weight: 600;
  font-size: 12px;
  padding: 6px 0;
}
.adm-table :deep(td) {
  font-size: 12px;
  color: #3b4a5f;
  padding: 5px 0;
}
.adm-table :deep(.cell) { padding: 0 6px; }
.adm-table :deep(.el-button + .el-button) { margin-left: 6px; }

/* 角色药丸 */
.role-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.r-super { color: #e74c3c; background: #fdeaea; }
.r-admin { color: #1878fd; background: #e3effe; }
.r-academic { color: #e67e22; background: #fdf1e3; }
.r-college { color: #7c3aed; background: #ede9fe; }

/* 状态药丸 */
.status-tag {
  display: inline-block;
  padding: 2px 10px;
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
  padding: 14px 16px;
  box-shadow: 0 1px 4px rgba(15, 46, 93, 0.06);
  display: flex;
  flex-direction: column;
}
.form-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid #eef2f8;
  margin-bottom: 12px;
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
.adm-form { flex: 1; }
.adm-form :deep(.el-form-item) {
  margin-bottom: 14px;
}
.adm-form :deep(.el-form-item__label) {
  font-size: 13px;
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
  padding-top: 10px;
  border-top: 1px solid #eef2f8;
}
</style>
