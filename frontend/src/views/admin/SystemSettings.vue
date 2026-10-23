<script setup lang="ts">
/**
 * 系统设置(管理端)。
 *
 * 两套口径并存, 互不越界:
 * 1. 非 AI 配置(系统名称/上传上限/调用频率)仍走 GET+PUT /admin/system-configs;
 * 2. AI 配置只走供应商 + 能力绑定模型: /admin/ai/providers + /admin/ai/bindings
 *    (+ presets 取预设、overview 取一屏状态)。AI 的模型名/Base URL/Key 绝不再作为
 *    sysconfig 键提交 —— 后端已删除那 16 个 ai_ 前缀键, 两边各写一份必然分叉。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { refreshPublicConfig } from '@/stores/app'
import { sysConfigApi } from '@/api/common'
import {
  aiAdminApi,
  aiHealthFixHint,
  describeRequestError,
  healthRows,
  normalizeConfigValues,
  overviewProbe,
  providerKindLabel,
  providerStatusMeta,
  truncate,
} from '@/utils/aiService'
import type {
  AiBindings,
  AiBindingsUpdate,
  AiOverview,
  AiPreset,
  AiProvider,
  AiProviderCreate,
  AiProviderUpdate,
  AiSlotKey,
  TagType,
} from '@/utils/aiService'

const loading = ref(false)
const saving = ref(false)

/** 非 AI 的既有配置块(系统名称/上传上限/调用频率), 口径与改造前一致 */
const form = reactive({
  system_name: '',
  school_name: '',
  ai_rate_limit_per_minute: 10,
  max_upload_size_mb: 50,
  /** max_upload_daily_mb: 默认 200, 整数, 已进后端 sysconfig FIELDS */
  max_upload_daily_mb: 200,
})

const INT_KEYS = ['ai_rate_limit_per_minute', 'max_upload_size_mb', 'max_upload_daily_mb']

/** 后端 PUT /admin/system-configs 的 items 上限(pydantic max_length=20), 超出要分批 */
const MAX_ITEMS_PER_PUT = 20

/** 非 AI 字段的基线: 判断"是否改过"的唯一依据 */
const legacyBaseline = ref<Record<string, string>>({})

/** ------------------------------------------------------------ AI 供应商与绑定 */

const presets = ref<AiPreset[]>([])
const providers = ref<AiProvider[]>([])
const bindings = ref<AiBindings | null>(null)
const overview = ref<AiOverview | null>(null)

const aiLoading = ref(false)
const bindingSaving = ref(false)
const masterSaving = ref(false)
/** 行级动作(刷新模型/启停/删除)共用一个 loading, 避免整页转圈 */
const busyProviderId = ref<number | null>(null)

/** AI 功能总开关: 与后端 ai_defense_enabled 同源, 只经 PUT /admin/ai/bindings 写 */
const aiEnabled = ref(true)

/** 三个能力位(顺序即界面顺序); 与后端 SLOT_KEYS 的 key 一一对应 */
const SLOT_META: { key: AiSlotKey; label: string; hint: string }[] = [
  { key: 'chat', label: '会话模型', hint: 'AI 问答、模拟答辩、文本生成都用它' },
  { key: 'embedding', label: '向量模型', hint: '知识库入库与检索; 换供应商或换模型后必须重建索引' },
  { key: 'rerank', label: '精排模型', hint: '对召回结果精排; 留空表示跳过精排' },
]

interface SlotDraft {
  provider_id: number | null
  model: string
}

const draft = reactive<Record<AiSlotKey, SlotDraft>>({
  chat: { provider_id: null, model: '' },
  embedding: { provider_id: null, model: '' },
  rerank: { provider_id: null, model: '' },
})

const probe = computed(() => overviewProbe(overview.value))
/** 五盏灯: 会话/向量/重排 + 向量库 + 本地 ollama(顺序与 /health/ai 一致) */
const statusRows = computed(() => healthRows(probe.value))
const aiFixHint = computed(() => aiHealthFixHint(probe.value))
const needsReindex = computed(() => overview.value?.needs_reindex === true)
const embeddingDim = computed(() => bindings.value?.embedding_dim ?? null)
/** 三个能力位都配好了才算"AI 可用"; 否则页头给一条可操作提示 */
const unconfiguredCount = computed(() => SLOT_META.filter((meta) => !bindings.value?.slots?.[meta.key]?.configured).length)

function applyBindings(next: AiBindings | AiOverview | null): void {
  bindings.value = next
  if (!next) return
  aiEnabled.value = next.enabled !== false
  for (const meta of SLOT_META) {
    const slot = next.slots?.[meta.key]
    draft[meta.key].provider_id = slot?.provider_id ?? null
    draft[meta.key].model = slot?.model ?? ''
  }
}

/** 预设里该 kind 的推荐模型: 供应商还没"刷新模型"时用它兜底, 免得模型下拉是空的 */
function presetModelsFor(kind: string): string[] {
  return presets.value.find((item) => item.kind === kind)?.models ?? []
}

function presetOf(kind: string): AiPreset | null {
  return presets.value.find((item) => item.kind === kind) ?? null
}

/** 某一能力位的模型候选: 真实清单 + 预设推荐(去重) */
function modelOptions(key: AiSlotKey): string[] {
  const providerId = draft[key].provider_id
  const provider = providers.value.find((item) => item.id === providerId)
  if (!provider) return []
  const merged = provider.models.concat(presetModelsFor(provider.kind))
  return Array.from(new Set(merged.filter((name) => String(name ?? '').trim().length > 0)))
}

/** 当前生效的绑定状态(未配置/已生效/绑定对象已失效), 每个能力位末列显示 */
function slotState(key: AiSlotKey): { type: TagType; label: string } {
  const slot = bindings.value?.slots?.[key]
  if (!slot) return { type: 'info', label: '读取中' }
  if (slot.broken) return { type: 'danger', label: '供应商已删除或停用' }
  if (!slot.configured) return { type: 'info', label: '未配置' }
  return { type: 'success', label: '已生效' }
}

function onBindingProviderChange(key: AiSlotKey, value: number | string | boolean | null): void {
  const id = typeof value === 'number' && Number.isFinite(value) ? value : null
  draft[key].provider_id = id
  if (!id) {
    draft[key].model = ''
    return
  }
  // 换了供应商: 旧模型名基本不在新供应商的清单里, 先清掉避免提交一个不存在的模型
  const options = modelOptions(key)
  if (!options.includes(draft[key].model)) draft[key].model = options[0] ?? ''
}

function onBindingModelChange(key: AiSlotKey, value: number | string | boolean | null): void {
  draft[key].model = value == null ? '' : String(value)
}

async function loadAi(): Promise<void> {
  aiLoading.value = true
  try {
    const [presetBody, providerBody, bindingBody, overviewBody] = await Promise.all([
      aiAdminApi.presets(),
      aiAdminApi.providers(),
      aiAdminApi.bindings(),
      aiAdminApi.overview(),
    ])
    presets.value = presetBody.data ?? []
    providers.value = providerBody.data ?? []
    overview.value = overviewBody.data ?? null
    applyBindings(bindingBody.data ?? overviewBody.data ?? null)
  } catch (e) {
    ElMessage.error(describeRequestError(e))
  } finally {
    aiLoading.value = false
  }
}

/** AI 功能总开关: 只发 enabled, 不夹带任何能力位(否则会把现有绑定清空) */
async function onToggleAi(value: string | number | boolean): Promise<void> {
  const next = value === true
  masterSaving.value = true
  try {
    const res = await aiAdminApi.saveBindings({ enabled: next })
    applyBindings(res.data ?? null)
    ElMessage.success(next ? 'AI 功能已开启' : 'AI 功能已关闭, 学生端 AI 接口统一停用')
    await loadAi()
  } catch (e) {
    ElMessage.error(describeRequestError(e))
    await loadAi()
  } finally {
    masterSaving.value = false
  }
}

/** ------------------------------------------------------------ 供应商弹窗 */

const editor = reactive({
  visible: false,
  mode: 'create' as 'create' | 'edit',
  id: 0,
  name: '',
  kind: '',
  base_url: '',
  api_key: '',
  enabled: true,
  clear_key: false,
})
const savingProvider = ref(false)

const editingProvider = computed(() => providers.value.find((item) => item.id === editor.id) ?? null)
const editorPreset = computed(() => (editor.kind ? presetOf(editor.kind) : null))
/** 编辑态不回显明文, 用 key_hint 说明"已保存什么, 留空即不修改" */
const keyPlaceholder = computed(() => {
  if (editor.mode === 'edit') {
    return '已保存 ' + (editingProvider.value?.key_hint || '密钥') + ', 留空不修改'
  }
  return editorPreset.value && editorPreset.value.needs_key === false ? '该供应商不需要 Key' : '粘贴 API Key'
})

function openCreate(): void {
  const preset = presets.value[0] ?? null
  editor.mode = 'create'
  editor.id = 0
  editor.name = ''
  editor.kind = preset?.kind ?? 'custom'
  editor.base_url = preset?.base_url ?? ''
  editor.api_key = ''
  editor.enabled = true
  editor.clear_key = false
  editor.visible = true
}

function openEdit(row: AiProvider): void {
  editor.mode = 'edit'
  editor.id = row.id
  editor.name = row.name
  editor.kind = row.kind
  editor.base_url = row.base_url
  editor.api_key = ''
  editor.enabled = row.enabled
  editor.clear_key = false
  editor.visible = true
}

/** 选中预设: 自动填 Base URL(名称空着时顺手填标签, 重名后端会拒绝并说明) */
function onKindChange(value: number | string | boolean | null): void {
  const kind = value == null ? '' : String(value)
  editor.kind = kind
  const preset = presetOf(kind)
  if (!preset) return
  editor.base_url = preset.base_url
  if (editor.mode === 'create' && !editor.name.trim()) editor.name = preset.label
}

async function submitProvider(): Promise<void> {
  const name = editor.name.trim()
  const baseUrl = editor.base_url.trim()
  if (!name) {
    ElMessage.warning('请填写供应商名称')
    return
  }
  if (!baseUrl) {
    ElMessage.warning('请填写 Base URL')
    return
  }
  savingProvider.value = true
  try {
    const apiKey = editor.api_key.trim()
    if (editor.mode === 'create') {
      const payload: AiProviderCreate = { name, kind: editor.kind || 'custom', base_url: baseUrl, enabled: editor.enabled }
      if (apiKey) payload.api_key = apiKey
      await aiAdminApi.createProvider(payload)
      ElMessage.success('已添加供应商, 建议点「刷新模型」拉取真实模型清单')
    } else {
      const payload: AiProviderUpdate = { name, kind: editor.kind || 'custom', base_url: baseUrl, enabled: editor.enabled }
      // 密钥三态: 留空 = 不修改; 填了新值 = 覆盖; 勾"清空密钥" = 显式清空
      if (apiKey) payload.api_key = apiKey
      if (editor.clear_key) payload.clear_key = true
      await aiAdminApi.updateProvider(editor.id, payload)
      ElMessage.success('已保存供应商')
    }
    editor.visible = false
    await loadAi()
  } catch (e) {
    ElMessage.error(describeRequestError(e))
  } finally {
    savingProvider.value = false
  }
}

async function onRefreshModels(row: AiProvider): Promise<void> {
  busyProviderId.value = row.id
  try {
    const res = await aiAdminApi.refreshModels(row.id)
    const result = res.data
    if (result?.status === 'ok') {
      const latency = result.latency_ms == null ? '' : ' (' + result.latency_ms + 'ms)'
      ElMessage.success('已拉取 ' + (result.models?.length ?? 0) + ' 个模型' + latency)
    } else {
      // 连接失败也返回 code=0: 必须按 status 判成功与否, 否则"刷新成功 0 个模型"会误导
      ElMessage.warning('刷新失败(' + providerStatusMeta(result?.status).label + '): ' + (result?.detail || '后端未给原因'))
    }
    await loadAi()
  } catch (e) {
    ElMessage.error(describeRequestError(e))
  } finally {
    busyProviderId.value = null
  }
}

async function onToggleProvider(row: AiProvider, enabled: boolean): Promise<void> {
  busyProviderId.value = row.id
  try {
    await aiAdminApi.updateProvider(row.id, { enabled })
    ElMessage.success(enabled ? '已启用供应商' : '已停用供应商')
    await loadAi()
  } catch (e) {
    ElMessage.error(describeRequestError(e))
    await loadAi()
  } finally {
    busyProviderId.value = null
  }
}

async function onDeleteProvider(row: AiProvider): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '删除供应商「' + row.name + '」? 仍有能力位绑定它时后端会拒绝删除并给出原因。',
      '删除供应商',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  busyProviderId.value = row.id
  try {
    await aiAdminApi.deleteProvider(row.id)
    ElMessage.success('已删除供应商')
    await loadAi()
  } catch (e) {
    // 400 的中文原因(被绑定/不存在)原样展示, 不要吞成"操作失败"
    ElMessage.error(describeRequestError(e))
  } finally {
    busyProviderId.value = null
  }
}

/** ------------------------------------------------------------ 绑定保存 */

async function saveBindings(): Promise<void> {
  const payload: AiBindingsUpdate = {}
  for (const meta of SLOT_META) {
    const next = draft[meta.key]
    const providerId = next.provider_id ?? null
    const model = String(next.model ?? '').trim()
    if (providerId && !model) {
      ElMessage.warning('「' + meta.label + '」已选供应商, 请同时选择或填写模型名')
      return
    }
    const base = bindings.value?.slots?.[meta.key]
    const same = (base?.provider_id ?? null) === providerId && String(base?.model ?? '') === model
    if (same) continue
    payload[meta.key] = { provider_id: providerId, model: providerId ? model : null }
  }
  if (!Object.keys(payload).length) {
    ElMessage.info('绑定没有变化')
    return
  }
  bindingSaving.value = true
  try {
    const res = await aiAdminApi.saveBindings(payload)
    applyBindings(res.data ?? null)
    await loadAi()
    ElMessage.success('绑定已保存, 立即生效')
  } catch (e) {
    ElMessage.error(describeRequestError(e))
  } finally {
    bindingSaving.value = false
  }
}

/** ------------------------------------------------------------ 非 AI 配置 */

function chunkKeys(keys: string[]): string[][] {
  const out: string[][] = []
  for (let i = 0; i < keys.length; i += MAX_ITEMS_PER_PUT) out.push(keys.slice(i, i + MAX_ITEMS_PER_PUT))
  return out
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const body = await sysConfigApi.list()
    const values = normalizeConfigValues(body)
    for (const key of Object.keys(form)) {
      if (!(key in values)) continue
      const raw = values[key]
      if (INT_KEYS.includes(key)) (form as Record<string, string | number>)[key] = Number(raw) || 0
      else (form as Record<string, string | number>)[key] = raw ?? ''
    }
    legacyBaseline.value = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, String(value)]))
  } catch (e) {
    ElMessage.error(describeRequestError(e))
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  // 系统名称是侧栏/登录页/浏览器标题的唯一来源, 空值保存等于把界面清空(后端同样会 40001)
  if (!String(form.system_name ?? '').trim()) {
    ElMessage.warning('系统名称不能为空')
    return
  }
  saving.value = true
  try {
    // 只提交发生变化的字段: 后端对未知 key 整体拒绝(40001), 全量提交必然踩雷
    const items: Record<string, string> = {}
    for (const [key, value] of Object.entries(form)) {
      const next = String(value)
      if (next === (legacyBaseline.value[key] ?? '')) continue
      items[key] = next
    }
    const keys = Object.keys(items)
    if (!keys.length) {
      ElMessage.info('没有检测到改动')
      return
    }
    for (const chunk of chunkKeys(keys)) {
      const payload: Record<string, string> = {}
      for (const key of chunk) payload[key] = items[key]
      await sysConfigApi.update(payload)
    }
    await refreshPublicConfig()
    ElMessage.success('已保存, 立即生效')
    await load()
  } catch (e) {
    ElMessage.error(describeRequestError(e))
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await load()
  await loadAi()
})
</script>

<template>
  <div class="page-card settings-page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1 class="page-title">系统配置</h1>
        <p class="hint">AI 供应商与能力绑定、系统名称与资源上限，保存后即时生效</p>
      </div>
      <div class="header-actions">
        <el-button :loading="aiLoading" @click="loadAi">刷新状态</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
      </div>
    </div>

    <section class="settings-block ai-service-block" v-loading="aiLoading">
      <div class="block-head">
        <h3 class="block-title">AI 服务</h3>
        <span class="block-sub">供应商 = 一份 Base URL + API Key；三个能力位分别指向供应商与模型名; 保存即生效, 无需重启</span>
      </div>

      <!-- 状态带: AI 总开关 + 五盏灯(会话/向量/重排/向量库/ollama) -->
      <div class="ai-status-bar">
        <div class="ai-master">
          <span class="ai-master-label">AI 功能</span>
          <el-switch
            :model-value="aiEnabled"
            :loading="masterSaving"
            active-text="开启"
            inactive-text="关闭"
            @change="onToggleAi"
          />
          <el-tag v-if="unconfiguredCount" type="info" size="small" effect="plain">还有 {{ unconfiguredCount }} 个能力位未配置</el-tag>
          <el-tag v-else type="success" size="small" effect="plain">三个能力位均已配置</el-tag>
        </div>
        <div class="status-lights">
          <el-tooltip v-for="row in statusRows" :key="row.key" :content="row.detail" placement="top">
            <span class="light">
              <i class="dot" :class="'dot-' + row.type"></i>
              <span class="light-name">{{ row.label }}</span>
              <el-tag :type="row.type" size="small" effect="plain">{{ row.tagLabel }}</el-tag>
            </span>
          </el-tooltip>
        </div>
      </div>

      <el-alert
        v-if="!providers.length"
        type="info"
        :closable="false"
        show-icon
        title="还没有供应商: AI 能力必须先添加一个供应商(填 Base URL 与 API Key)才能被绑定"
        class="health-fix"
      >
        <el-button size="small" type="primary" @click="openCreate">去添加供应商</el-button>
      </el-alert>
      <el-alert v-if="aiFixHint" type="warning" :closable="false" show-icon :title="aiFixHint" class="health-fix" />
      <el-alert
        v-if="needsReindex"
        type="warning"
        :closable="false"
        show-icon
        title="向量模型或维度已变化: 既有索引与新向量不在同一空间, 请到知识库页重建索引"
        class="health-fix"
      />

      <div class="sub-head">
        <h4 class="sub-title">供应商</h4>
        <el-button size="small" type="primary" @click="openCreate">添加供应商</el-button>
      </div>

      <el-empty v-if="!providers.length" :image-size="70" description="还没有配置供应商" />

      <div v-else class="provider-list">
        <div v-for="row in providers" :key="row.id" class="provider-card">
          <div class="provider-main">
            <div class="provider-title">
              <span class="provider-name">{{ row.name }}</span>
              <el-tag size="small" effect="plain">{{ providerKindLabel(row.kind) }}</el-tag>
              <el-tag :type="providerStatusMeta(row.last_status).type" size="small" effect="plain">
                {{ providerStatusMeta(row.last_status).label }}
              </el-tag>
              <el-tag v-if="row.cipher_broken" type="danger" size="small">密钥不可解密</el-tag>
            </div>
            <div class="provider-url">{{ row.base_url }}</div>
            <div class="provider-meta">
              <span>模型 {{ row.models.length }} 个</span>
              <span v-if="row.models.length" class="provider-models">{{ truncate(row.models.join(', '), 60) }}</span>
              <span>密钥 {{ row.has_key ? (row.key_hint || '已保存') : '未配置' }}</span>
              <span v-if="row.last_probe_at">最近探测 {{ row.last_probe_at }}</span>
            </div>
            <div v-if="row.last_detail" class="provider-detail">{{ truncate(row.last_detail, 140) }}</div>
          </div>
          <div class="provider-actions">
            <el-switch
              :model-value="row.enabled"
              size="small"
              :loading="busyProviderId === row.id"
              active-text="启用"
              inactive-text="停用"
              @change="onToggleProvider(row, $event === true)"
            />
            <el-button size="small" :loading="busyProviderId === row.id" @click="onRefreshModels(row)">刷新模型</el-button>
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" plain @click="onDeleteProvider(row)">删除</el-button>
          </div>
        </div>
      </div>

      <div class="sub-head">
        <h4 class="sub-title">能力绑定</h4>
        <span class="block-sub">每个能力位 = 供应商 + 模型名; 两者缺一不可</span>
      </div>

      <div class="binding-list">
        <div v-for="meta in SLOT_META" :key="meta.key" class="binding-row">
          <div class="binding-label">
            <span class="binding-name">{{ meta.label }}</span>
            <span class="binding-hint">{{ meta.hint }}</span>
          </div>
          <el-select
            v-model="draft[meta.key].provider_id"
            class="binding-provider"
            placeholder="选择供应商"
            size="small"
            clearable
            @change="onBindingProviderChange(meta.key, $event)"
          >
            <el-option
              v-for="item in providers"
              :key="item.id"
              :label="item.enabled ? item.name : item.name + ' (已停用)'"
              :value="item.id"
              :disabled="!item.enabled"
            />
          </el-select>
          <el-select
            v-model="draft[meta.key].model"
            class="binding-model"
            size="small"
            filterable
            allow-create
            default-first-option
            :disabled="!draft[meta.key].provider_id"
            placeholder="选择或手填模型名"
            @change="onBindingModelChange(meta.key, $event)"
          >
            <el-option v-for="name in modelOptions(meta.key)" :key="name" :label="name" :value="name" />
          </el-select>
          <div class="binding-state">
            <el-tag :type="slotState(meta.key).type" size="small" effect="plain">{{ slotState(meta.key).label }}</el-tag>
            <span v-if="meta.key === 'embedding'" class="dim-text">
              {{ embeddingDim ? embeddingDim + ' 维' : '维度未记录' }}
            </span>
          </div>
        </div>
      </div>

      <div class="binding-footer">
        <el-button type="primary" size="small" :loading="bindingSaving" @click="saveBindings">保存绑定</el-button>
        <span class="field-hint">保存后后端会重置 AI 运行时配置, 下一次请求即按新绑定执行; 未改动的能力位不会提交</span>
      </div>
    </section>

    <div class="settings-grid">
      <section class="settings-block">
        <h3 class="block-title">基础信息</h3>
        <el-form label-width="110px" label-position="left">
          <el-form-item label="系统名称">
            <el-input v-model="form.system_name" maxlength="50" show-word-limit placeholder="登录页与侧栏展示的名称" />
          </el-form-item>
          <el-form-item label="学校名称">
            <el-input v-model="form.school_name" maxlength="50" show-word-limit placeholder="例如：XX大学（可选）" />
          </el-form-item>
        </el-form>
      </section>

      <section class="settings-block">
        <h3 class="block-title">AI 功能</h3>
        <el-form label-width="110px" label-position="left">
          <el-form-item label="调用频率上限">
            <el-input-number v-model="form.ai_rate_limit_per_minute" :min="1" :max="999" />
            <span class="unit">次 / 分钟 / 用户</span>
            <div class="field-hint">AI 总开关在上方状态带; 关闭后学生端 AI 模拟答辩、知识问答、PPT 大纲等接口统一停用</div>
          </el-form-item>
        </el-form>
      </section>

      <section class="settings-block">
        <h3 class="block-title">资源限制</h3>
        <el-form label-width="110px" label-position="left">
          <el-form-item label="上传大小上限">
            <el-input-number v-model="form.max_upload_size_mb" :min="1" :max="2048" />
            <span class="unit">MB / 单文件</span>
            <div class="field-hint">对论文、材料等所有上传文件生效</div>
          </el-form-item>
          <el-form-item label="每日上传上限">
            <el-input-number v-model="form.max_upload_daily_mb" :min="1" :max="102400" />
            <span class="unit">MB / 每人每日</span>
            <div class="field-hint">按人按自然日累计；用尽后上传返回 40013（与单文件超限 40010 是不同错误）</div>
          </el-form-item>
        </el-form>
      </section>
    </div>

    <el-alert type="info" :closable="false" show-icon
      title="配置修改即时生效（Redis 缓存在保存时失效）；API Key 只写不读，界面只回显后四位，未填写的 Key 不会随保存提交。"
      class="footer-note" />

    <!-- 供应商新增/编辑 -->
    <el-dialog v-model="editor.visible" :title="editor.mode === 'create' ? '添加供应商' : '编辑供应商'" width="540px" destroy-on-close>
      <el-form label-width="96px" label-position="left">
        <el-form-item label="供应商类型">
          <el-select :model-value="editor.kind" placeholder="选择预设" @change="onKindChange">
            <el-option
              v-for="preset in presets"
              :key="preset.kind"
              :label="preset.label "
              :value="preset.kind"
            />
          </el-select>
          <div v-if="editorPreset" class="field-hint">
            {{ editorPreset.note }}
            <span v-if="editorPreset.models.length">推荐模型: {{ editorPreset.models.join(' / ') }}</span>
          </div>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="editor.name" maxlength="64" :placeholder="editorPreset ? editorPreset.label : '例如: 校内 DeepSeek'" />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="editor.base_url" maxlength="255" placeholder="https://api.deepseek.com/v1" />
          <div class="field-hint">填供应商根地址, 后端会自行补 /chat/completions 与 /embeddings</div>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="editor.api_key"
            type="password"
            show-password
            autocomplete="new-password"
            :placeholder="keyPlaceholder"
          />
          <div class="field-hint">
            <el-checkbox v-if="editor.mode === 'edit'" v-model="editor.clear_key" size="small">清空密钥(保存后该供应商不再有 Key)</el-checkbox>
            <span v-else>密钥只写不读, 保存后界面只显示后四位</span>
          </div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="editor.enabled" active-text="启用" inactive-text="停用" />
          <div class="field-hint">停用后该供应商的模型不能再被绑定, 已绑定的能力位会显示"供应商已停用"</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editor.visible = false">取消</el-button>
        <el-button type="primary" :loading="savingProvider" @click="submitProvider">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.settings-page { max-width: 980px; }
.header-actions { display: flex; gap: 10px; }
.block-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.block-sub { color: #9aa2b0; font-size: 12px; }
.ai-service-block { margin-bottom: 22px; }
.ai-status-bar {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 12px; margin-top: 14px; padding: 12px 14px;
  border: 1px solid #eef0f5; border-radius: 8px; background: #fbfcfe;
}
.ai-master { display: flex; align-items: center; gap: 10px; }
.ai-master-label { font-size: 14px; color: #2c3e50; }
.status-lights { display: flex; flex-wrap: wrap; gap: 14px; }
.light { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: #606266; }
.light-name { color: #8a93a5; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: #c0c4cc; }
.dot-success { background: #67c23a; }
.dot-warning { background: #e6a23c; }
.dot-danger { background: #f56c6c; }
.dot-info { background: #b3bac7; }
.dot-primary { background: #409eff; }
.health-fix { margin-top: 10px; }
.sub-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 20px 0 10px; }
.sub-title { margin: 0; font-size: 14px; color: #2c3e50; }
.provider-list { display: flex; flex-direction: column; gap: 10px; }
.provider-card {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 14px;
  border: 1px solid #eef0f5; border-radius: 8px; padding: 12px 14px;
}
.provider-card:hover { border-color: #dbe3ef; }
.provider-main { min-width: 0; flex: 1; }
.provider-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.provider-name { font-size: 14px; color: #2c3e50; font-weight: 500; }
.provider-url { margin-top: 6px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; color: #5b6577; word-break: break-all; }
.provider-meta { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 6px; font-size: 12px; color: #9aa2b0; }
.provider-models { color: #b0b7c3; }
.provider-detail { margin-top: 4px; font-size: 12px; color: #8a93a5; word-break: break-word; }
.provider-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; flex: none; }
.binding-list { display: flex; flex-direction: column; gap: 10px; }
.binding-row {
  display: grid; grid-template-columns: minmax(180px, 220px) minmax(140px, 1fr) minmax(160px, 1fr) minmax(150px, auto);
  align-items: center; gap: 10px; padding: 10px 14px;
  border: 1px solid #eef0f5; border-radius: 8px;
}
.binding-label { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.binding-name { font-size: 13px; color: #2c3e50; }
.binding-hint { font-size: 12px; color: #9aa2b0; line-height: 1.5; }
.binding-provider, .binding-model { width: 100%; }
.binding-state { display: flex; align-items: center; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }
.dim-text { font-size: 12px; color: #93a1b4; }
.binding-footer { display: flex; align-items: center; gap: 12px; margin-top: 12px; flex-wrap: wrap; }
.footer-note { margin-top: 4px; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 22px; margin-bottom: 20px; }
.settings-block { border: 1px solid #eef0f5; border-radius: 8px; padding: 18px; }
.unit { margin-left: 10px; color: #8a93a5; font-size: 12px; }
.field-hint { width: 100%; color: #9aa2b0; font-size: 12px; line-height: 1.6; margin-top: 4px; }
@media (max-width: 900px) {
  .settings-grid { grid-template-columns: 1fr; }
  .provider-card { flex-direction: column; }
  .provider-actions { justify-content: flex-start; }
  .binding-row { grid-template-columns: 1fr; }
  .binding-state { justify-content: flex-start; }
  .settings-page :deep(.el-form-item__label) { width: 96px !important; }
}
</style>
