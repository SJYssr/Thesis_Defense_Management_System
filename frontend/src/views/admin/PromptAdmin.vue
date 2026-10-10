<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'

interface PromptRow {
  code: string
  name: string
  description: string
  untrusted: boolean
  customized: boolean
  enabled: boolean
  version: number
  updated_at: string | null
  system_prompt: string
  source: 'DB' | 'BUILT_IN'
}

interface PromptVersion {
  version: number
  action: string
  system_prompt: string | null
  operator_id: number | null
  created_at: string | null
}

const rows = ref<PromptRow[]>([])
const loading = ref(false)

const editVisible = ref(false)
const editing = ref<PromptRow | null>(null)
const editContent = ref('')
const saving = ref(false)

const historyVisible = ref(false)
const historyCode = ref('')
const historyRows = ref<PromptVersion[]>([])
const historyLoading = ref(false)

const ACTION_LABELS: Record<string, string> = { UPDATE: '修改', RESET: '重置', STATUS: '启停' }

async function load() {
  loading.value = true
  try {
    rows.value = (await request.get('/admin/ai-prompts')).data
  } finally {
    loading.value = false
  }
}

function openEdit(row: PromptRow) {
  editing.value = row
  editContent.value = row.system_prompt
  editVisible.value = true
}

async function saveEdit() {
  if (!editing.value) return
  const content = editContent.value.trim()
  if (content.length < 20) {
    ElMessage.warning('Prompt 内容至少 20 个字符')
    return
  }
  saving.value = true
  try {
    await request.put(`/admin/ai-prompts/${editing.value.code}`, { system_prompt: content })
    ElMessage.success('Prompt 已更新并即时生效')
    editVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row: PromptRow, enabled: boolean) {
  await request.post(`/admin/ai-prompts/${row.code}/status`, { enabled })
  ElMessage.success(enabled ? '已启用自定义内容' : '已停用自定义内容, 回退内置默认')
  await load()
}

async function resetPrompt(row: PromptRow) {
  await ElMessageBox.confirm(
    `将「${row.name}」的内容恢复为系统内置默认？当前自定义内容会保留在版本历史中。`, '恢复默认',
    { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' },
  )
  await request.post(`/admin/ai-prompts/${row.code}/reset`)
  ElMessage.success('已恢复内置默认内容')
  await load()
}

async function openHistory(row: PromptRow) {
  historyCode.value = row.code
  historyVisible.value = true
  historyLoading.value = true
  try {
    historyRows.value = (await request.get(`/admin/ai-prompts/${row.code}/versions`)).data
  } finally {
    historyLoading.value = false
  }
}

function fmtTime(t: string | null) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

onMounted(load)
</script>

<template>
  <div class="page-card prompt-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Prompt 配置</h1>
        <p class="hint">AI 各场景的 System Prompt 模板管理（roadmap 七十三章），修改产生新版本并留痕、即时生效</p>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="tip"
      title="标注「安全声明」的场景会在运行时由系统强制追加防注入与防幻觉约束，不在可编辑内容中，无法删改"
      description="自定义内容请保留原有 JSON 输出格式要求（离线演示模式依赖其中的关键字识别场景）；停用自定义后场景回退内置默认。" />

    <el-table v-loading="loading" :data="rows" empty-text="暂无 Prompt 场景">
      <el-table-column label="场景" min-width="180">
        <template #default="{ row }">
          <div class="scene-name">{{ row.name }}
            <el-tag v-if="row.untrusted" size="small" type="warning" effect="plain" class="safe-tag">安全声明</el-tag>
          </div>
          <div class="scene-code">{{ row.code }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="说明" min-width="220" show-overflow-tooltip />
      <el-table-column label="来源" width="90">
        <template #default="{ row }">
          <el-tag :type="row.source === 'DB' ? 'primary' : 'info'" size="small" effect="plain">
            {{ row.source === 'DB' ? '自定义' : '内置' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="自定义生效" width="110">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" :disabled="!row.customized"
            @change="(v: any) => toggleStatus(row, !!v)" />
        </template>
      </el-table-column>
      <el-table-column label="版本" width="70" align="center">
        <template #default="{ row }">{{ row.customized ? `v${row.version}` : '-' }}</template>
      </el-table-column>
      <el-table-column label="更新时间" min-width="150">
        <template #default="{ row }">{{ row.customized ? fmtTime(row.updated_at) : '-' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="primary" @click="openHistory(row)">历史</el-button>
          <el-button link type="danger" :disabled="!row.customized" @click="resetPrompt(row)">恢复默认</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="editVisible" :title="`编辑 Prompt · ${editing?.name || ''}`" width="720px" top="6vh">
      <el-alert type="warning" :closable="false" show-icon class="dialog-tip"
        :title="`场景说明：${editing?.description || ''}`"
        :description="editing?.untrusted
          ? '该场景运行时会自动追加防注入安全声明（不可通过编辑移除）。'
          : '该场景不涉及检索上下文，无附加安全声明。'" />
      <el-input v-model="editContent" type="textarea" :rows="14" maxlength="8000" show-word-limit
        class="prompt-input" spellcheck="false" />
      <div class="length-hint">{{ editContent.trim().length }} / 20~8000 字符</div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存并生效</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="historyVisible" :title="`版本历史 · ${historyCode}`" width="680px" top="8vh">
      <div v-loading="historyLoading" class="history-list">
        <el-empty v-if="!historyLoading && !historyRows.length" description="暂无版本记录" />
        <div v-for="(v, i) in historyRows" :key="i" class="history-item">
          <div class="history-head">
            <el-tag size="small" :type="v.action === 'RESET' ? 'warning' : v.action === 'STATUS' ? 'info' : 'primary'"
              effect="plain">{{ ACTION_LABELS[v.action] || v.action }}</el-tag>
            <span class="history-version">v{{ v.version }}</span>
            <span class="history-time">{{ fmtTime(v.created_at) }}</span>
          </div>
          <el-collapse class="history-collapse">
            <el-collapse-item title="内容快照">
              <pre class="history-content">{{ v.system_prompt || '(空)' }}</pre>
            </el-collapse-item>
          </el-collapse>
        </div>
      </div>
      <template #footer>
        <el-button @click="historyVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.hint { color: #718096; font-size: 13px; margin: 6px 0 0; }
.tip { margin-top: 18px; }
.scene-name { font-weight: 600; color: #2c3e50; }
.safe-tag { margin-left: 6px; }
.scene-code { font-size: 12px; color: #93a1b4; margin-top: 2px; }
.dialog-tip { margin-bottom: 14px; }
.prompt-input :deep(textarea) { font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 12.5px; line-height: 1.7; }
.length-hint { text-align: right; font-size: 12px; color: #93a1b4; margin-top: 6px; }
.history-list { max-height: 56vh; overflow-y: auto; }
.history-item { border-bottom: 1px dashed #e6ebf2; padding: 10px 2px; }
.history-head { display: flex; align-items: center; gap: 10px; }
.history-version { font-weight: 600; font-size: 13px; color: #2c3e50; }
.history-time { font-size: 12px; color: #93a1b4; }
.history-collapse { border: none; margin-top: 6px; }
.history-content { white-space: pre-wrap; word-break: break-all; font-size: 12px; color: #5a6a80;
  background: #f7f9fc; border-radius: 6px; padding: 10px; margin: 0; max-height: 200px; overflow-y: auto; }
@media (max-width: 700px) {
  .prompt-page :deep(.el-dialog) { width: 94% !important; }
}
</style>
