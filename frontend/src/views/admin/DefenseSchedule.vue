<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { defenseApi } from '@/api/defense'
import { academicYearApi, userAdminApi } from '@/api/common'
import { progressApi } from '@/api/business'

const sessions = ref<any[]>([])
const page = ref(1)
const pageSize = 20
const total = ref(0)
const effectiveTotal = ref(0)
let listSequence = 0
const rescheduleVisible = ref(false)
const rescheduling = ref(false)
const changeForm = reactive({ startsAt: '', endsAt: '', reason: '' })
const stats = ref<any>({})
const query = reactive({ academic_year: '', stage: '', status: '' })
const years = ref<string[]>([])
const activeYear = ref('')
const createVisible = ref(false)
const detail = ref<any | null>(null)
const detailVisible = ref(false)
const createForm = reactive({
  academic_year: '', stage: 'MIDTERM', title: '',
  date: '' as any, startTime: '09:00', endTime: '12:00',
  location: '', online_link: '', capacity: 10,
})

const STATUS_TYPE: Record<string, string> = {
  DRAFT: 'info', READY: 'warning', PUBLISHED: 'success', IN_PROGRESS: 'primary', FINISHED: 'info', CANCELLED: 'danger',
}

const STATUS_LABEL: Record<string, string> = {
  DRAFT: '草稿', READY: '待发布', PUBLISHED: '已发布', IN_PROGRESS: '进行中', FINISHED: '已结束', CANCELLED: '已取消',
}

async function load() {
  const sequence = ++listSequence
  const res = await defenseApi.adminList({ ...query, page: page.value, page_size: pageSize })
  if (sequence !== listSequence) return
  sessions.value = res.data.items
  total.value = res.data.total
  effectiveTotal.value = res.data.effective_total
}

function search() {
  page.value = 1
  load()
}

async function loadStats() {
  const { statsApi } = await import('@/api/defense')
  const res = await statsApi.defenseStats()
  stats.value = res.data
}

function openCreate() {
  Object.assign(createForm, { academic_year: query.academic_year || activeYear.value,
    stage: 'MIDTERM', title: '', date: '', startTime: '09:00', endTime: '12:00', location: '', online_link: '', capacity: 10 })
  createVisible.value = true
}

async function create() {
  if (!createForm.date) { ElMessage.warning('请选择日期'); return }
  const date = typeof createForm.date === 'string' ? createForm.date : createForm.date.toISOString().slice(0, 10)
  await defenseApi.create({
    academic_year: createForm.academic_year, stage: createForm.stage, title: createForm.title,
    starts_at: `${date}T${createForm.startTime}:00`, ends_at: `${date}T${createForm.endTime}:00`,
    location: createForm.location, online_link: createForm.online_link, capacity: createForm.capacity,
  })
  ElMessage.success('场次草稿已创建')
  createVisible.value = false
  load()
}

async function openDetail(s: any) {
  const res = await defenseApi.detail(s.id)
  detail.value = res.data
  detailVisible.value = true
}

async function validate(s?: any) {
  const res = await defenseApi.validate((s || detail.value).id)
  const conflicts = res.data.conflicts
  if (!conflicts.length) ElMessage.success('校验通过, 无冲突')
  else {
    ElMessageBox.alert(conflicts.map((c: any) => `【${c.type_cn}】${c.detail}`).join('\n'), `发现 ${conflicts.length} 处冲突`, { type: 'warning' })
  }
}

async function publish(s?: any) {
  const target = s || detail.value
  await ElMessageBox.confirm('发布后将通知全部学生与评委; 发布前会再次校验冲突。确认发布?', '发布场次', { type: 'warning' })
  try {
    await defenseApi.publish(target.id)
    ElMessage.success('已发布并通知')
    refreshDetailAndList(target.id)
  } catch { /* 冲突信息已由拦截器提示 */ }
}

async function startSession(s?: any) {
  const target = s || detail.value
  await ElMessageBox.confirm(`确认开始「${target.title || '场次#' + target.id}」? 开始后会通知全部学生与评委。`, '开始答辩', { type: 'warning' })
  await defenseApi.start(target.id)
  ElMessage.success('答辩已开始')
  refreshDetailAndList(target.id)
}

async function finishSession(s?: any) {
  const target = s || detail.value
  await ElMessageBox.confirm('确认结束该场次? 结束后不可再开始, 之后可汇总评分。', '结束答辩', { type: 'warning' })
  await defenseApi.finish(target.id)
  ElMessage.success('答辩已结束')
  refreshDetailAndList(target.id)
}

async function cancelSession(s: any) {
  const { value } = await ElMessageBox.prompt('取消原因', '取消场次', { inputPattern: /.+/, inputErrorMessage: '必填' })
  await defenseApi.cancel(s.id, value)
  ElMessage.success('已取消')
  refreshDetailAndList(s.id)
}

const picker = ref<'students' | 'examiner' | null>(null)
const keyword = ref('')
const candidatePage = ref(1)
const candidateSize = 8
const candidateTotal = ref(0)
type Candidate = { id: number; name: string; number: string; alreadyAdded: boolean }
const candidates = ref<Candidate[]>([])
const candidateLoading = ref(false)
const savingMembers = ref(false)
const selectedCycles = ref<number[]>([])
const selectedTeacher = ref<number | null>(null)
const examinerRole = ref('EXAMINER')
let candidateRequest = 0

async function loadCandidates() {
  const mode = picker.value
  if (!mode || !detail.value) return
  const request = ++candidateRequest
  const session = detail.value
  const paging = { page: candidatePage.value, page_size: candidateSize }
  candidateLoading.value = true
  candidates.value = []
  try {
    const res = mode === 'students'
      ? await progressApi.adminList({ ...paging, academic_year: session.academic_year, active_cycles_only: true, student: keyword.value.trim() })
      : await userAdminApi.list('TEACHER', { ...paging, status: 1, keyword: keyword.value.trim() })
    if (request !== candidateRequest) return
    candidateTotal.value = res.data.total
    candidates.value = res.data.items.map((row: any) => mode === 'students'
      ? { id: row.cycle_id, name: row.student_name, number: row.student_no, alreadyAdded: session.assignments.some((a: any) => a.cycle_id === row.cycle_id) }
      : { id: row.profile_id, name: row.real_name, number: row.teacher_no, alreadyAdded: session.examiners.some((e: any) => e.teacher_id === row.profile_id) })
  } finally {
    if (request === candidateRequest) candidateLoading.value = false
  }
}

function openPicker(mode: 'students' | 'examiner') {
  picker.value = mode
  keyword.value = ''
  candidatePage.value = 1
  candidateTotal.value = 0
  selectedCycles.value = []
  selectedTeacher.value = null
  examinerRole.value = detail.value.examiners.some((e: any) => e.role === 'CHAIR') ? 'EXAMINER' : 'CHAIR'
  loadCandidates()
}

function searchCandidates() {
  candidatePage.value = 1
  loadCandidates()
}

async function saveMembers() {
  if (savingMembers.value) return
  const mode = picker.value
  const sessionId = detail.value.id
  savingMembers.value = true
  try {
    if (mode === 'students' && selectedCycles.value.length) {
      await defenseApi.addAssignments(sessionId, selectedCycles.value)
      ElMessage.success('学生已排入场次')
    } else if (mode === 'examiner' && selectedTeacher.value) {
      await defenseApi.addExaminer(sessionId, { teacher_id: selectedTeacher.value, examiner_role: examinerRole.value })
      ElMessage.success('评委已加入场次')
    } else return
    picker.value = null
    await refreshDetailAndList(sessionId)
  } finally {
    savingMembers.value = false
  }
}

async function removeExaminer(ex: any) {
  await ElMessageBox.confirm(`确认移除评委「${ex.teacher_name}」？`, '移除评委')
  await defenseApi.removeExaminer(detail.value.id, ex.id)
  refreshDetailAndList(detail.value.id)
}

async function markSeat(a: any, status: string) {
  let remark
  if (status === 'POSTPONED' || status === 'ABSENT') {
    const r = await ElMessageBox.prompt('原因(延期/缺席必填)', '标记', { inputPattern: /.+/, inputErrorMessage: '必填' })
    remark = r.value
  }
  await defenseApi.setSeat(a.id, { seat_status: status, remark })
  refreshDetailAndList(detail.value.id)
}

function openReschedule() {
  changeForm.startsAt = detail.value.starts_at
  changeForm.endsAt = detail.value.ends_at
  changeForm.reason = ''
  rescheduleVisible.value = true
}

async function reschedule() {
  if (!changeForm.startsAt || !changeForm.endsAt || changeForm.startsAt >= changeForm.endsAt) {
    ElMessage.warning('请填写开始和结束时间，结束时间须晚于开始时间')
    return
  }
  if (!changeForm.reason.trim()) {
    ElMessage.warning('请填写改期原因')
    return
  }
  const s = detail.value
  rescheduling.value = true
  try {
    await defenseApi.patch(s.id, {
      starts_at: changeForm.startsAt, ends_at: changeForm.endsAt,
      revision: s.revision, change_reason: changeForm.reason.trim(),
    })
    rescheduleVisible.value = false
    ElMessage.success('已改期并通知')
    await refreshDetailAndList(s.id)
  } finally {
    rescheduling.value = false
  }
}

async function refreshDetailAndList(id: number) {
  const res = await defenseApi.detail(id)
  detail.value = res.data
  await Promise.all([load(), loadStats()])
}

onMounted(async () => {
  const { data } = await academicYearApi.list()
  years.value = data.years
  activeYear.value = data.active_year
  query.academic_year = data.active_year
  createForm.academic_year = data.active_year
  await Promise.all([load(), loadStats()])
})
</script>

<template>
  <div class="page-wrap">
    <!-- 统计 -->
    <div class="stat-cards">
      <div class="stat-card" v-for="c in [
        { label: '有效场次', value: effectiveTotal },
        { label: '已排学生', value: stats.assigned_total ?? '—' },
        { label: '待评审席位', value: stats.pending_review ?? '—' },
        { label: '未分配评委场次', value: stats.sessions_no_examiner ?? '—' },
        { label: '延期人数', value: stats.postponed ?? '—' },
        { label: '归档完成率', value: stats.archive_rate != null ? stats.archive_rate + '%' : '—' },
      ]" :key="c.label">
        <div class="stat-label">{{ c.label }}</div><div class="stat-num">{{ c.value }}</div>
      </div>
    </div>

    <div class="table-card">
      <div class="tab-bar">
        <div class="tabs"><div class="tab active">答辩排期</div></div>
        <div class="tab-actions"><el-button type="primary" @click="openCreate">+ 新建场次</el-button></div>
      </div>

      <div class="filter-row">
        <el-select v-model="query.academic_year" style="width: 140px">
          <el-option v-for="year in years" :key="year" :label="year" :value="year" />
        </el-select>
        <el-select v-model="query.stage" placeholder="全部阶段" clearable style="width: 130px">
          <el-option label="开题答辩" value="OPENING" /><el-option label="中期答辩" value="MIDTERM" /><el-option label="终期答辩" value="FINAL" />
        </el-select>
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 130px">
          <el-option v-for="(cn, k) in STATUS_LABEL" :key="k" :label="cn" :value="k" />
        </el-select>
        <el-button type="primary" @click="search">查询</el-button>
      </div>

      <el-empty v-if="!sessions.length" description="当前学年暂无场次" />
      <!-- 手机宽度: 日期分组卡片 -->
      <div class="session-cards">
        <div v-for="s in sessions" :key="s.id" class="session-item" @click="openDetail(s)">
          <div class="s-date">
            <div class="d">{{ s.starts_at?.slice(5, 10) }}</div>
            <div class="t">{{ s.starts_at?.slice(11, 16) }}</div>
          </div>
          <div class="s-main">
            <div class="s-title">
              <el-tag size="small">{{ s.stage_cn }}</el-tag>
              <b>{{ s.title || '未命名场次' }}</b>
              <el-tag size="small" :type="(STATUS_TYPE[s.status] as any)">{{ s.status_cn }}</el-tag>
            </div>
            <div class="s-meta">
              {{ s.is_online ? '线上' : s.location }} · 评委 {{ s.examiner_count }} 人 · 学生 {{ s.assignment_count }}/{{ s.capacity }}
              <span v-if="s.changes?.length" class="changed">· 已改期 {{ s.changes.length }} 次</span>
            </div>
          </div>
          <el-button size="small" text type="primary" @click.stop="validate(s)">校验</el-button>
          <el-button v-if="s.allowed_actions.includes('publish')" size="small" type="primary" @click.stop="publish(s)">发布</el-button>
          <el-button v-if="s.status === 'PUBLISHED'" size="small" type="success" @click.stop="startSession(s)">开始</el-button>
          <el-button v-if="s.status === 'IN_PROGRESS'" size="small" @click.stop="finishSession(s)">结束</el-button>
        </div>
      </div>
    </div>

    <el-pagination v-if="total > pageSize" v-model:current-page="page" :page-size="pageSize" :total="total"
                   layout="prev, pager, next, total" @current-change="load" />

    <!-- 场次详情抽屉 -->
    <el-drawer v-model="detailVisible" size="min(680px, 100vw)" :title="detail?.title || '场次详情'">
      <template v-if="detail">
        <div class="drawer-meta">
          <el-tag>{{ detail.stage_cn }}</el-tag>
          <el-tag :type="(STATUS_TYPE[detail.status] as any)">{{ detail.status_cn }}</el-tag>
          <span>{{ detail.starts_at?.replace('T', ' ').slice(0, 16) }} ~ {{ detail.ends_at?.replace('T', ' ').slice(0, 16) }}</span>
          <span>{{ detail.is_online ? '线上' : detail.location }}</span>
        </div>
        <div v-if="detail.online_link">线上链接：<el-link type="primary" :href="detail.online_link">{{ detail.online_link }}</el-link></div>

        <div class="block-title">评委（{{ detail.examiners?.length || 0 }}）</div>
        <div class="member-rows">
          <span v-for="e in detail.examiners" :key="e.teacher_id" class="ex-item">
            <el-tag effect="plain">{{ e.teacher_name }}（{{ e.role_cn }}）</el-tag>
            <el-button v-if="e.removable" size="small" text type="danger" @click="removeExaminer(e)">移除</el-button>
          </span>
          <span v-if="!detail.examiners?.length" class="muted">未分配评委</span>
        </div>

        <div class="block-title">学生（{{ detail.assignments?.length || 0 }}/{{ detail.capacity }}）</div>
        <el-table :data="detail.assignments" size="small">
          <el-table-column prop="student_no" label="学号" width="90" />
          <el-table-column prop="student_name" label="姓名" width="80" />
          <el-table-column prop="topic_title" label="课题" min-width="150" show-overflow-tooltip />
          <el-table-column label="席位" width="90">
            <template #default="{ row }"><el-tag size="small">{{ row.seat_status_cn }}</el-tag></template>
          </el-table-column>
          <el-table-column label="结论" width="90">
            <template #default="{ row }">{{ row.result_status_cn || '—' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="150">
            <template #default="{ row }">
              <el-button v-if="['IN_PROGRESS', 'FINISHED'].includes(detail.status) && row.seat_actions.includes('attend')" size="small" text type="success" @click="markSeat(row, 'ATTENDED')">到场</el-button>
              <el-button v-if="detail.status === 'FINISHED' && row.seat_actions.includes('absent')" size="small" text type="danger" @click="markSeat(row, 'ABSENT')">缺席</el-button>
              <el-button v-if="['PUBLISHED', 'IN_PROGRESS', 'FINISHED'].includes(detail.status) && row.seat_actions.includes('postpone')" size="small" text type="warning" @click="markSeat(row, 'POSTPONED')">延期</el-button>
              <el-button v-if="['PUBLISHED', 'IN_PROGRESS', 'FINISHED'].includes(detail.status) && row.seat_actions.includes('reset')" size="small" text @click="markSeat(row, 'SCHEDULED')">恢复排期</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="detail.changes?.length" class="block-title">改期记录</div>
        <el-timeline v-if="detail.changes?.length">
          <el-timeline-item v-for="(c, i) in detail.changes" :key="i"
                            :timestamp="c.after_starts_at?.replace('T', ' ').slice(0, 16)">
            调整前 {{ c.before_starts_at?.replace('T', ' ').slice(0, 16) }} → 原因: {{ c.reason }}
          </el-timeline-item>
        </el-timeline>

        <div class="drawer-actions">
          <el-button @click="validate()">校验冲突</el-button>
          <el-button v-if="detail.roster_editable" @click="openPicker('examiner')">添加评委</el-button>
          <el-button v-if="detail.roster_editable" :disabled="detail.assignment_count >= detail.capacity" @click="openPicker('students')">排入学生</el-button>
          <el-button v-if="detail.status === 'PUBLISHED'" type="warning" @click="openReschedule">改期</el-button>
          <el-button v-if="detail.allowed_actions.includes('publish')" type="primary" @click="publish()">发布场次</el-button>
          <el-button v-if="detail.status === 'PUBLISHED'" type="success" @click="startSession()">开始答辩</el-button>
          <el-button v-if="detail.status === 'IN_PROGRESS'" @click="finishSession()">结束答辩</el-button>
          <el-button v-if="detail.allowed_actions.includes('cancel')" type="danger" plain @click="cancelSession(detail)">取消场次</el-button>
        </div>
      </template>
    </el-drawer>

    <el-dialog :model-value="!!picker" :title="picker === 'students' ? '排入学生' : '添加评委'"
               width="min(480px, 94vw)" @close="picker = null">
      <div class="person-search">
        <el-input v-model="keyword" clearable :placeholder="picker === 'students' ? '输入姓名或学号' : '输入姓名或工号'"
                  @keyup.enter="searchCandidates" @clear="searchCandidates" />
        <el-button @click="searchCandidates">查询</el-button>
      </div>
      <div v-loading="candidateLoading" class="person-list">
        <label v-for="person in candidates" :key="person.id || person.number" class="person-option">
          <input v-if="picker === 'students'" v-model="selectedCycles" type="checkbox" :value="person.id"
                 :disabled="!person.id || person.alreadyAdded || (!selectedCycles.includes(person.id) && selectedCycles.length >= detail.capacity - detail.assignment_count)" />
          <input v-else v-model="selectedTeacher" type="radio" :value="person.id" :disabled="!person.id || person.alreadyAdded" />
          <span>{{ person.name }}（{{ person.number }}）</span><small v-if="person.alreadyAdded">已加入</small>
        </label>
        <el-empty v-if="!candidateLoading && !candidates.length" description="未找到匹配人员" :image-size="42" />
      </div>
      <el-pagination v-if="candidateTotal > candidateSize" v-model:current-page="candidatePage" :page-size="candidateSize"
                     :total="candidateTotal" layout="prev, pager, next" :pager-count="5" @current-change="loadCandidates" />
      <div v-if="picker === 'examiner' && detail" class="role-select">
        <span>评委角色</span>
        <el-select v-model="examinerRole" aria-label="评委角色" style="width: 150px">
          <el-option label="主席" value="CHAIR" :disabled="detail.examiners.some((e: any) => e.role === 'CHAIR')" />
          <el-option label="评委" value="EXAMINER" /><el-option label="评阅人" value="REVIEWER" />
        </el-select>
      </div>
      <template #footer>
        <el-button @click="picker = null">取消</el-button>
        <el-button type="primary" :loading="savingMembers"
                   :disabled="picker === 'students' ? !selectedCycles.length : !selectedTeacher"
                   @click="saveMembers">{{ picker === 'students' ? '确认排入' : '确认添加' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rescheduleVisible" title="答辩改期" width="min(520px, 94vw)">
      <el-form label-width="90px">
        <el-form-item label="开始时间">
          <el-date-picker v-model="changeForm.startsAt" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
        </el-form-item>
        <el-form-item label="结束时间">
          <el-date-picker v-model="changeForm.endsAt" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
        </el-form-item>
        <el-form-item label="改期原因"><el-input v-model="changeForm.reason" type="textarea" maxlength="500" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rescheduleVisible = false">取消</el-button>
        <el-button type="primary" :loading="rescheduling" @click="reschedule">保存并通知</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="createVisible" title="新建答辩场次" width="min(520px, 94vw)">
      <el-form label-width="90px">
        <el-form-item label="学年"><el-input v-model="createForm.academic_year" style="width: 200px" /></el-form-item>
        <el-form-item label="阶段">
          <el-select v-model="createForm.stage" style="width: 200px">
            <el-option label="开题答辩" value="OPENING" /><el-option label="中期答辩" value="MIDTERM" /><el-option label="终期答辩" value="FINAL" />
          </el-select>
        </el-form-item>
        <el-form-item label="场次名称"><el-input v-model="createForm.title" placeholder="如: 中期答辩第2场" /></el-form-item>
        <el-form-item label="日期"><el-date-picker v-model="createForm.date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item label="时间">
          <el-time-select v-model="createForm.startTime" start="08:00" step="00:30" end="20:00" style="width: 120px" />
          <span style="margin: 0 8px">至</span>
          <el-time-select v-model="createForm.endTime" :start="createForm.startTime" step="00:30" end="22:00" style="width: 120px" />
        </el-form-item>
        <el-form-item label="地点"><el-input v-model="createForm.location" placeholder="线下教室; 线上场留空并填链接" /></el-form-item>
        <el-form-item label="线上链接"><el-input v-model="createForm.online_link" placeholder="可选" /></el-form-item>
        <el-form-item label="容量"><el-input-number v-model="createForm.capacity" :min="1" :max="200" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="create">创建草稿</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.stat-cards { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; margin-bottom: 18px; }
.stat-card { background: #fff; border-radius: 10px; padding: 14px 18px; box-shadow: 0 1px 4px rgba(30, 60, 114, 0.06); }
.stat-label { font-size: 12px; color: #909399; }
.stat-num { font-size: 22px; font-weight: 600; color: #1878fd; margin-top: 4px; }
.filter-row { display: flex; gap: 12px; margin-bottom: 14px; }
.session-cards { display: flex; flex-direction: column; gap: 10px; }
.session-item { display: flex; align-items: center; gap: 14px; padding: 12px 16px; border: 1px solid #ebeef5; border-radius: 10px; cursor: pointer; }
.session-item:hover { border-color: #1878fd; }
.s-date { text-align: center; color: #1878fd; min-width: 52px; }
.s-date .d { font-size: 16px; font-weight: 700; }
.s-date .t { font-size: 12px; }
.s-main { flex: 1; }
.s-title { display: flex; gap: 8px; align-items: center; font-size: 14px; }
.s-meta { font-size: 12px; color: #909399; margin-top: 4px; }
.s-meta .changed { color: #e6a23c; }
.drawer-meta { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; margin-bottom: 8px; }
.block-title { font-weight: 600; font-size: 14px; margin: 18px 0 10px; }
.member-rows { font-size: 13px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.ex-item { display: inline-flex; align-items: center; gap: 4px; }
.muted { color: #909399; font-size: 12px; }
.person-search, .role-select { display: flex; align-items: center; gap: 10px; }
.person-list { min-height: 78px; margin: 12px 0; }
.person-option { display: flex; align-items: center; gap: 8px; padding: 7px 0; cursor: pointer; }
.person-option small { color: var(--el-text-color-secondary); }
.role-select { margin-top: 12px; }
.drawer-actions { margin-top: 22px; display: flex; flex-wrap: wrap; gap: 6px; }
@media (max-width: 900px) { .stat-cards { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 640px) { .stat-cards { grid-template-columns: repeat(2, 1fr); } }
</style>
