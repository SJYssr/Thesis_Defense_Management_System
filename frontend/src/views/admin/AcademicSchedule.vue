<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'

interface Schedule {
  id: number
  academic_year: string
  is_active: boolean
  [key: string]: unknown
}

const STAGES = [
  { key: 'selection', label: '选题' },
  { key: 'opening', label: '开题' },
  { key: 'midterm', label: '中期' },
  { key: 'final', label: '终期' },
  { key: 'material', label: '毕业材料' },
]

const rows = ref<Schedule[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)

const form = reactive({
  academic_year: '',
  is_active: false,
  windows: {} as Record<string, string | null>,
})

// 日期区间选择器使用 [start, end] 数组, 提交时拆回 windows
const ranges = reactive({} as Record<string, [string, string] | null>)

function emptyWindows() {
  const w: Record<string, string | null> = {}
  for (const s of STAGES) {
    w[`${s.key}_start`] = null
    w[`${s.key}_end`] = null
  }
  return w
}

async function load() {
  loading.value = true
  try {
    const res = await request.get('/admin/academic-schedules')
    rows.value = res.data
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  form.academic_year = ''
  form.is_active = rows.value.length === 0
  form.windows = emptyWindows()
  for (const s of STAGES) ranges[s.key] = null
  dialogVisible.value = true
}

function openEdit(row: Schedule) {
  editingId.value = row.id
  form.academic_year = row.academic_year
  form.is_active = row.is_active
  const w: Record<string, string | null> = {}
  for (const s of STAGES) {
    w[`${s.key}_start`] = (row[`${s.key}_start`] as string) || null
    w[`${s.key}_end`] = (row[`${s.key}_end`] as string) || null
    const start = w[`${s.key}_start`]
    const end = w[`${s.key}_end`]
    ranges[s.key] = start && end ? [start, end] : null
  }
  form.windows = w
  dialogVisible.value = true
}

function onRangeChange(key: string, v: [string, string] | null) {
  ranges[key] = v
  form.windows[`${key}_start`] = v?.[0] ?? null
  form.windows[`${key}_end`] = v?.[1] ?? null
}

async function save() {
  if (!form.academic_year.trim()) {
    ElMessage.warning('请填写学年，如 2025-2026')
    return
  }
  saving.value = true
  try {
    const payload = {
      academic_year: form.academic_year.trim(),
      is_active: form.is_active,
      windows: Object.fromEntries(Object.entries(form.windows).filter(([, v]) => v !== null && v !== '')),
    }
    if (editingId.value === null) {
      await request.post('/admin/academic-schedules', payload)
      ElMessage.success('学年配置已创建')
    } else {
      await request.put(`/admin/academic-schedules/${editingId.value}`, payload)
      ElMessage.success('学年配置已更新')
    }
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function activate(row: Schedule) {
  if (row.is_active) return
  await ElMessageBox.confirm(
    `激活学年 ${row.academic_year} 后, 其他学年将自动停用; 选题时间窗与逾期风险判断立即按新学年执行。`,
    '切换激活学年', { confirmButtonText: '激活', cancelButtonText: '取消', type: 'warning' })
  await request.put(`/admin/academic-schedules/${row.id}`, {
    academic_year: row.academic_year,
    is_active: true,
    windows: Object.fromEntries(
      STAGES.flatMap(s => [`${s.key}_start`, `${s.key}_end`])
        .map(k => [k, (row[k] as string) || null] as [string, string | null])
        .filter(([, v]) => v !== null)
    ),
  })
  ElMessage.success(`学年 ${row.academic_year} 已激活`)
  await load()
}

function windowText(row: Schedule, key: string) {
  const start = row[`${key}_start`] as string | null
  const end = row[`${key}_end`] as string | null
  if (!start && !end) return '未设置'
  const fmt = (v: string | null) => (v ? v.replace('T', ' ').slice(0, 16) : '不限')
  return `${fmt(start)} ~ ${fmt(end)}`
}

onMounted(load)
</script>

<template>
  <div class="page-card schedule-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">学年与阶段时间配置</h1>
        <p class="hint">各学年选题、开题、中期、终期与毕业材料的时间窗口；激活学年的窗口用于选题校验与逾期风险判断</p>
      </div>
      <el-button type="primary" @click="openCreate">新建学年配置</el-button>
    </div>

    <el-table v-loading="loading" :data="rows" empty-text="暂无学年配置，点击右上角新建">
      <el-table-column prop="academic_year" label="学年" min-width="110" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.is_active" type="success" size="small">生效中</el-tag>
          <el-tag v-else type="info" size="small" effect="plain">已停用</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-for="s in STAGES" :key="s.key" :label="s.label" min-width="200">
        <template #default="{ row }">{{ windowText(row, s.key) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="!row.is_active" link type="success" size="small" @click="activate(row)">激活</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId === null ? '新建学年配置' : `编辑学年 ${form.academic_year}`"
      width="640px" destroy-on-close>
      <el-form label-width="150px" label-position="left">
        <el-form-item label="学年" required>
          <el-input v-model="form.academic_year" placeholder="如 2025-2026" maxlength="32" style="max-width: 260px" />
        </el-form-item>
        <el-form-item label="激活（立即生效）">
          <el-switch v-model="form.is_active" active-text="激活" inactive-text="停用" />
          <div class="field-hint">激活后其他学年自动停用；选题提交将按本学年的选题时间窗校验</div>
        </el-form-item>
        <el-form-item v-for="s in STAGES" :key="s.key" :label="`${s.label}时间`">
          <el-date-picker :model-value="ranges[s.key]" type="datetimerange"
            value-format="YYYY-MM-DDTHH:mm:ss"
            start-placeholder="开始" end-placeholder="结束" range-separator="至"
            style="width: 100%"
            @update:model-value="(v: any) => onRangeChange(s.key, v as [string, string] | null)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.hint { color: #718096; font-size: 13px; margin: 6px 0 0; }
.field-hint { width: 100%; color: #9aa2b0; font-size: 12px; line-height: 1.6; margin-top: 4px; }
@media (max-width: 900px) {
  .schedule-page :deep(.el-table) { font-size: 12px; }
  .schedule-page :deep(.el-dialog) { width: 94vw; }
}
</style>
