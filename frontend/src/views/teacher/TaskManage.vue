<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { taskApi } from '@/api/defense'
import type { StageTask, StageTaskAction, StageTaskVersion } from '@/types/api'

const tasks = ref<StageTask[]>([])
const versions = ref<StageTaskVersion[]>([])
const versionsVisible = ref(false)
const query = reactive({ academic_year: '2025-2026', stage: '', status: '' })
const dialogVisible = ref(false)
const editing = ref<any | null>(null)
const form = reactive({
  academic_year: '2025-2026', stage: 'OPENING', title: '', description: '',
  requirements: '', opens_at: '', due_at: '', late_policy: 'ALLOW', status: 'PUBLISHED',
  college_id: null as number | null, major_id: null as number | null, class_id: null as number | null,
})

async function load() {
  const res = await taskApi.list({ ...query, page: 1, page_size: 50 })
  tasks.value = res.data.items || []
}

function openCreate() {
  editing.value = null
  Object.assign(form, { stage: 'OPENING', title: '', description: '', requirements: '', opens_at: '', due_at: '', late_policy: 'ALLOW', status: 'PUBLISHED' })
  dialogVisible.value = true
}

function openEdit(t: any) {
  editing.value = t
  Object.assign(form, {
    academic_year: t.academic_year, stage: t.stage, title: t.title, description: t.description || '',
    requirements: (t.requirements || []).join('、'), opens_at: t.opens_at || '', due_at: t.due_at || '',
    late_policy: t.late_policy, status: t.status,
  })
  dialogVisible.value = true
}

async function save() {
  const base = {
    academic_year: form.academic_year, stage: form.stage, title: form.title, description: form.description,
    requirements: form.requirements.split(/[、,，]/).map((s: string) => s.trim()).filter(Boolean),
    opens_at: form.opens_at || null, due_at: form.due_at || null,
    late_policy: form.late_policy,
    college_id: form.college_id, major_id: form.major_id, class_id: form.class_id,
  }
  if (editing.value) {
    // P7-A(D59): PUT /stage-tasks/{id} 不再接受 status, 状态流转一律走 publish/close
    await taskApi.update(editing.value.id, base)
    ElMessage.success('任务已更新并留痕')
  } else {
    // P7-A: POST 的 status 缺省已由 PUBLISHED 改为 DRAFT, 必须显式传, 否则「直接发布」会静默变草稿
    await taskApi.create({ ...base, status: form.status })
    ElMessage.success(form.status === 'PUBLISHED' ? '任务已创建并发布' : '任务已保存为草稿')
  }
  dialogVisible.value = false
  load()
}

/** 按钮渲染唯一依据: 后端 allowed_actions(DRAFT→publish, PUBLISHED→close, CLOSED→[]) */
function canDo(row: StageTask, action: StageTaskAction) {
  return Array.isArray(row.allowed_actions) && row.allowed_actions.includes(action)
}

const STATUS_LABEL: Record<string, string> = { DRAFT: '草稿', PUBLISHED: '已发布', CLOSED: '已关闭' }
function statusLabel(status: string) { return STATUS_LABEL[status] || status }

async function publishTask(row: StageTask) {
  await ElMessageBox.confirm(`发布后学生端立即可见「${row.title}」并开始计时。`, '发布阶段任务',
    { confirmButtonText: '发布', cancelButtonText: '取消', type: 'warning' })
  await taskApi.publish(row.id)
  ElMessage.success('任务已发布')
  await load()
}

async function closeTask(row: StageTask) {
  await ElMessageBox.confirm(`关闭后「${row.title}」不再接收学生提交(已提交内容保留)。`, '关闭阶段任务',
    { confirmButtonText: '关闭', cancelButtonText: '取消', type: 'warning' })
  await taskApi.close(row.id)
  ElMessage.success('任务已关闭')
  await load()
}

async function showVersions(t: StageTask) {
  // P7-A: 该接口已返回 { items, page, page_size, total }, 不能再当数组用
  const res = await taskApi.versions(t.id, { page: 1, page_size: 20 })
  versions.value = res.data?.items ?? []
  versionsVisible.value = true
}

async function remind(t: StageTask) {
  await ElMessageBox.prompt('提醒内容', '提醒学生', { inputValue: `请于截止时间前完成「${t.title}」` })
}

onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <div class="table-card">
      <div class="tab-bar">
        <div class="tabs"><div class="tab active">指导任务</div></div>
        <div class="tab-actions">
          <el-button type="primary" @click="openCreate">+ 新建任务</el-button>
        </div>
      </div>

      <div class="filter-row">
        <el-select v-model="query.academic_year" style="width: 140px">
          <el-option label="2025-2026" value="2025-2026" />
          <el-option label="2024-2025" value="2024-2025" />
        </el-select>
        <el-select v-model="query.stage" placeholder="全部阶段" clearable style="width: 140px">
          <el-option v-for="s in ['SELECTION', 'OPENING', 'MIDTERM', 'FINAL', 'MATERIAL']" :key="s" :label="s" :value="s" />
        </el-select>
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px">
          <el-option label="已发布" value="PUBLISHED" /><el-option label="草稿" value="DRAFT" /><el-option label="已关闭" value="CLOSED" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
      </div>

      <el-empty v-if="!tasks.length" description="当前学年暂无任务，点击右上角新建" />
      <el-table v-else :data="tasks" class="mat-table">
        <el-table-column prop="title" label="任务名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="stage" label="阶段" width="100" />
        <el-table-column label="所需材料" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="r in row.requirements" :key="r" size="small" effect="plain" style="margin-right:6px">{{ r }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="开放/截止" width="220">
          <template #default="{ row }">
            <div class="time-cell">{{ (row.opens_at || '—').replace('T', ' ').slice(5, 16) }} ~</div>
            <div class="time-cell">{{ (row.due_at || '—').replace('T', ' ').slice(5, 16) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="补交" width="70">
          <template #default="{ row }">
            <el-tag size="small" :type="row.late_policy === 'ALLOW' ? 'success' : 'danger'">{{ row.late_policy === 'ALLOW' ? '允许' : '禁止' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'PUBLISHED' ? 'success' : row.status === 'CLOSED' ? 'info' : 'warning'">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="canDo(row, 'publish')" size="small" text type="success" @click="publishTask(row)">发布</el-button>
            <el-button v-if="canDo(row, 'close')" size="small" text type="danger" @click="closeTask(row)">关闭</el-button>
            <el-button size="small" text @click="showVersions(row)">修改历史</el-button>
            <el-button size="small" text type="warning" @click="remind(row)">提醒学生</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑任务(版本化留痕)' : '新建阶段任务'" width="560px">
      <el-form label-width="90px">
        <el-form-item label="学年"><el-input v-model="form.academic_year" style="width:200px" /></el-form-item>
        <el-form-item label="阶段">
          <el-select v-model="form.stage" style="width:200px">
            <el-option v-for="s in ['SELECTION', 'OPENING', 'MIDTERM', 'FINAL', 'MATERIAL']" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务名称"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="任务说明"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="所需材料"><el-input v-model="form.requirements" placeholder="用顿号分隔：开题报告、文献综述" /></el-form-item>
        <el-form-item label="开放时间"><el-date-picker v-model="form.opens_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" /></el-form-item>
        <el-form-item label="截止时间"><el-date-picker v-model="form.due_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" /></el-form-item>
        <el-form-item label="逾期补交">
          <el-radio-group v-model="form.late_policy">
            <el-radio value="ALLOW">允许补交</el-radio><el-radio value="DENY">禁止补交</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="!editing" label="状态">
          <el-radio-group v-model="form.status">
            <el-radio value="PUBLISHED">直接发布</el-radio><el-radio value="DRAFT">存为草稿</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="versionsVisible" title="修改历史" width="560px">
      <el-empty v-if="!versions.length" description="暂无修改记录" />
      <el-timeline v-else>
        <el-timeline-item v-for="v in versions" :key="v.version_no" :timestamp="`v${v.version_no}`">
          <b>{{ v.snapshot.title }}</b>
          <div class="muted">修改前：{{ (v.snapshot.due_at || '无截止时间').replace('T', ' ').slice(0, 16) }}</div>
        </el-timeline-item>
      </el-timeline>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-row { display: flex; gap: 12px; margin-bottom: 16px; }
.time-cell { font-size: 12px; color: #606266; line-height: 1.6; }
.muted { color: #909399; font-size: 12px; }
</style>
