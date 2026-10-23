<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/api/request'
import { refreshPublicConfig } from '@/stores/app'
import { healthApi, sysConfigApi } from '@/api/common'
import {
  AI_CONFIG_KEYS,
  AI_SERVICES,
  aiHealthFixHint,
  collectAiConfigChanges,
  describeRequestError,
  extractHasKeys,
  healthIssueDetail,
  healthRows,
  isHealthyStatus,
  normalizeConfigValues,
  resolveHasKeys,
} from '@/utils/aiService'
import type { AiHasKeys, AiHealthSnapshot, HealthSnapshot } from '@/types/api'

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)

/** 非 AI 的既有配置块(系统名称/上传上限等), 逻辑与改造前一致 */
const form = reactive({
  system_name: '',
  school_name: '',
  ai_defense_enabled: true,
  ai_rate_limit_per_minute: 10,
  max_upload_size_mb: 50,
  /** P7-A 新增配置项(max_upload_daily_mb, 默认 200, 整数, 已进后端 sysconfig FIELDS) */
  max_upload_daily_mb: 200,
})

const BOOL_KEYS = ['ai_defense_enabled']
const INT_KEYS = ['ai_rate_limit_per_minute', 'max_upload_size_mb', 'max_upload_daily_mb']

/** 后端 PUT /admin/system-configs 的 items 上限(pydantic max_length=20), 超出要分批 */
const MAX_ITEMS_PER_PUT = 20

/**
 * AI 三组服务的表单值。全部按字符串存: 后端 sysconfig 的值都是字符串
 * (bool 只接受 "true"/"false", int 是数字串), 统一用字符串可以避免
 * "false" 被 Boolean() 判真、空数字被转成 0 这类提交事故。
 */
const aiForm = reactive<Record<string, string>>(
  Object.fromEntries(AI_CONFIG_KEYS.map((key) => [key, ''])),
)
/** 服务端最近一次下发的值(密钥是掩码): 判断"是否改过"的唯一基线 */
const aiBaseline = ref<Record<string, string>>({})
/** 非 AI 字段的基线, 同样用于"只提交变化项" */
const legacyBaseline = ref<Record<string, string>>({})
/** 三组密钥是否已配置: 后端下发的 has_keys, 缺失时退回掩码判定 */
const hasKeys = ref<AiHasKeys>({ llm: false, embedding: false, rerank: false })

/** GET /health(裸对象, 非信封) */
const health = ref<HealthSnapshot | null>(null)
/** GET /health/ai 五段探针 */
const aiHealth = ref<AiHealthSnapshot | null>(null)

/**
 * 每组服务的 sysconfig 键名(显式列全, 不用字符串拼)。
 * 显式列出的好处: check-api-contract 能静态断言表单确实绑定了 ai_llm_base_url /
 * ai_llm_api_key 等 17 个键, 且新增字段时漏写会被类型检查发现。
 */
interface AiServiceFields {
  enabled: string
  provider: string
  baseUrl: string
  apiKey: string
  model: string
  /** 组内额外字段(向量模型=维度, 重排=本地模型目录) */
  extraLabel?: string
  extraKey?: string
  extraHint?: string
  extraPlaceholder?: string
}

const AI_SERVICE_FIELDS: Record<'llm' | 'embedding' | 'rerank', AiServiceFields> = {
  llm: {
    enabled: 'ai_llm_enabled',
    provider: 'ai_llm_provider',
    baseUrl: 'ai_llm_base_url',
    apiKey: 'ai_llm_api_key',
    model: 'ai_llm_model',
  },
  embedding: {
    enabled: 'ai_embedding_enabled',
    provider: 'ai_embedding_provider',
    baseUrl: 'ai_embedding_base_url',
    apiKey: 'ai_embedding_api_key',
    model: 'ai_embedding_model',
    extraLabel: '维度',
    extraKey: 'ai_embedding_dim',
    extraPlaceholder: '留空 = 按模型自动探测',
    extraHint: '修改维度需重建知识库, 否则既有向量与新向量不在同一空间',
  },
  rerank: {
    enabled: 'ai_rerank_enabled',
    provider: 'ai_rerank_provider',
    baseUrl: 'ai_rerank_base_url',
    apiKey: 'ai_rerank_api_key',
    model: 'ai_rerank_model',
    extraLabel: '模型目录',
    extraKey: 'ai_rerank_model_dir',
    extraPlaceholder: '本地 ONNX 模型目录(可选)',
    extraHint: '仅本地 ONNX 重排使用; 留空按默认目录查找',
  },
}

function fieldsOf(svc: 'llm' | 'embedding' | 'rerank'): AiServiceFields {
  return AI_SERVICE_FIELDS[svc]
}

/** 每组字段的示例与 provider 选项(与后端 sysconfig 注释的取值一致: auto/openai/ollama, 重排 auto/onnx/http/none) */
const AI_GROUP_META: Record<'llm' | 'embedding' | 'rerank', {
  baseUrlPlaceholder: string
  modelPlaceholder: string
  providers: { label: string; value: string }[]
}> = {
  llm: {
    baseUrlPlaceholder: 'https://api.deepseek.com/v1',
    modelPlaceholder: 'deepseek-chat',
    providers: [
      { label: '跟随环境变量(auto)', value: '' },
      { label: 'openai 兼容', value: 'openai' },
      { label: 'ollama', value: 'ollama' },
    ],
  },
  embedding: {
    baseUrlPlaceholder: 'https://api.openai.com/v1',
    modelPlaceholder: 'text-embedding-3-small',
    providers: [
      { label: '跟随环境变量(auto)', value: '' },
      { label: 'openai 兼容', value: 'openai' },
      { label: 'ollama', value: 'ollama' },
    ],
  },
  rerank: {
    baseUrlPlaceholder: 'http://127.0.0.1:8080/rerank',
    modelPlaceholder: 'bge-reranker-v2-m3',
    providers: [
      { label: '跟随环境变量(auto)', value: '' },
      { label: '本地 ONNX', value: 'onnx' },
      { label: '远端 HTTP', value: 'http' },
      { label: '关闭', value: 'none' },
    ],
  },
}

const modelInfo = ref<{
  llm_provider: string
  llm_openai_mode: boolean
  llm_model_env: string
  llm_model_effective: string
  embedding_provider: string
  embedding_dim: number
  embedding_model: string | null
} | null>(null)

/** 五段探针表格行(测试连接与状态带共用同一份判定) */
const healthRowsList = computed(() => healthRows(aiHealth.value))
const aiFixHint = computed(() => aiHealthFixHint(aiHealth.value))

/** 页头状态带: /health 的 status 不是 ok 就必须显示, 不允许静默 */
const healthAlert = computed<{ type: 'info' | 'warning'; title: string; desc: string } | null>(() => {
  const snapshot = health.value
  if (!snapshot) return null
  if (isHealthyStatus(snapshot.status)) return null
  if (String(snapshot.status).toLowerCase() === 'not_configured') {
    return {
      type: 'info',
      title: '尚未配置 AI 服务, AI 问答暂不可用',
      desc: '在下方「AI 服务」卡片填写 Base URL / API Key / 模型名后保存即可, 无需重启服务。',
    }
  }
  return {
    type: 'warning',
    title: `AI 服务异常: ${healthIssueDetail(aiHealth.value) || snapshot.status}`,
    desc: aiFixHint.value,
  }
})

/** 掩码/空串的密钥不写进表单: 已配置时输入框留空, 由 placeholder 说明 */
function applyConfigValues(values: Record<string, string>) {
  const baseline: Record<string, string> = {}
  for (const key of AI_CONFIG_KEYS) {
    const raw = values[key]
    const value = raw == null ? '' : String(raw)
    baseline[key] = value
    aiForm[key] = value.startsWith('*') ? '' : value
  }
  aiBaseline.value = baseline
}

async function load() {
  loading.value = true
  try {
    const body = await sysConfigApi.list()
    const values = normalizeConfigValues(body)
    const explicit = extractHasKeys(body)

    for (const key of Object.keys(form)) {
      if (!(key in values)) continue
      const raw = values[key]
      if (BOOL_KEYS.includes(key)) (form as any)[key] = raw === 'true'
      else if (INT_KEYS.includes(key)) (form as any)[key] = Number(raw) || 0
      else (form as any)[key] = raw ?? ''
    }
    legacyBaseline.value = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, String(value)]))

    applyConfigValues(values)
    hasKeys.value = resolveHasKeys(values, explicit)
    await loadModelInfo()
  } catch (e) {
    ElMessage.error(describeRequestError(e))
  } finally {
    loading.value = false
  }
}

async function loadModelInfo() {
  try {
    modelInfo.value = (await request.get('/admin/ai-model-info')).data
  } catch {
    modelInfo.value = null
  }
}

/** 非 AI 字段的差异(字符串逐项比较, 与后端存的口径一致) */
function legacyChanges(): Record<string, string> {
  const items: Record<string, string> = {}
  for (const [key, value] of Object.entries(form)) {
    const next = String(value)
    if (next === (legacyBaseline.value[key] ?? '')) continue
    items[key] = next
  }
  return items
}

function chunkKeys(keys: string[]): string[][] {
  const out: string[][] = []
  for (let i = 0; i < keys.length; i += MAX_ITEMS_PER_PUT) out.push(keys.slice(i, i + MAX_ITEMS_PER_PUT))
  return out
}

async function save() {
  saving.value = true
  try {
    // 只提交发生变化的字段: 未修改的 api_key 绝不会进请求体(空值/掩码/与基线相同都被跳过)
    const { items: aiItems } = collectAiConfigChanges(aiForm, aiBaseline.value)
    const merged: Record<string, string> = { ...legacyChanges(), ...aiItems }
    const keys = Object.keys(merged)
    if (!keys.length) {
      ElMessage.info('没有检测到改动')
      return
    }
    let lastBody: unknown = null
    for (const chunk of chunkKeys(keys)) {
      const payload: Record<string, string> = {}
      for (const key of chunk) payload[key] = merged[key]
      lastBody = await sysConfigApi.update(payload)
    }
    // 用后端返回的(掩码)值刷新表单, 再整体重载一次, 确保掩码与 has_keys 都是最新口径
    const returned = normalizeConfigValues(lastBody)
    if (Object.keys(returned).length) applyConfigValues({ ...aiBaseline.value, ...returned })
    await refreshPublicConfig()
    ElMessage.success('已保存, 立即生效')
    await load()
    await loadHealth()
  } catch (e) {
    ElMessage.error(describeRequestError(e))
  } finally {
    saving.value = false
  }
}

async function loadHealth() {
  try {
    // /health 与 /health/ai 直接返回裸对象(不带 code/message 信封)
    const snapshot = (await healthApi.status()) as unknown as HealthSnapshot
    health.value = snapshot ?? null
    aiHealth.value = (snapshot?.ai as AiHealthSnapshot) ?? null
  } catch {
    health.value = null
    aiHealth.value = null
  }
}

/** 测试连接: 重新拉一次五段探针, 结果逐段展示 */
async function testConnection() {
  testing.value = true
  try {
    const snapshot = (await healthApi.ai()) as unknown as AiHealthSnapshot
    aiHealth.value = snapshot ?? null
    const rows = healthRows(aiHealth.value)
    const bad = rows.filter((row) => row.type !== 'success')
    if (!bad.length) ElMessage.success('全部探针正常')
    else ElMessage.warning(`${bad.length} 段异常: ${bad.map((row) => row.label).join(' / ')}`)
    await loadHealth()
  } catch (e) {
    ElMessage.error(describeRequestError(e))
  } finally {
    testing.value = false
  }
}

onMounted(async () => {
  await load()
  await loadHealth()
})
</script>

<template>
  <div class="page-card settings-page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1 class="page-title">系统配置</h1>
        <p class="hint">系统名称、AI 服务、AI 功能开关与资源上限，保存后即时生效</p>
      </div>
      <div class="header-actions">
        <el-button :loading="testing" @click="testConnection">测试连接</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
      </div>
    </div>

    <!-- 状态带: /health 的 status 不是 ok 时必须有可见提示 -->
    <el-alert
      v-if="healthAlert"
      :type="healthAlert.type"
      :title="healthAlert.title"
      :description="healthAlert.desc"
      :closable="false"
      show-icon
      class="status-banner"
    />

    <section class="settings-block ai-service-block">
      <div class="block-head">
        <h3 class="block-title">AI 服务</h3>
        <span class="block-sub">第三方 OpenAI 兼容服务或本地 Ollama; 保存即生效, 无需重启</span>
      </div>

      <div class="ai-grid">
        <div v-for="svc in AI_SERVICES" :key="svc.id" class="ai-group">
          <div class="ai-group-head">
            <span class="ai-group-title">{{ svc.label }}</span>
            <el-switch
              v-model="aiForm[fieldsOf(svc.id).enabled]"
              active-value="true"
              inactive-value="false"
              size="small"
              active-text="启用"
              inactive-text="关闭"
            />
          </div>

          <el-form label-width="86px" label-position="left" size="small">
            <el-form-item label="提供方">
              <el-select v-model="aiForm[fieldsOf(svc.id).provider]" placeholder="跟随环境变量(auto)" clearable>
                <el-option
                  v-for="option in AI_GROUP_META[svc.id].providers"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="Base URL">
              <el-input
                v-model="aiForm[fieldsOf(svc.id).baseUrl]"
                :placeholder="AI_GROUP_META[svc.id].baseUrlPlaceholder"
                clearable
              />
            </el-form-item>

            <el-form-item label="API Key">
              <div class="key-row">
                <el-input
                  v-model="aiForm[fieldsOf(svc.id).apiKey]"
                  type="password"
                  show-password
                  autocomplete="new-password"
                  :placeholder="hasKeys[svc.id] ? '已配置(保存后覆盖)' : '未配置'"
                />
                <el-tag :type="hasKeys[svc.id] ? 'success' : 'info'" size="small" class="key-badge">
                  {{ hasKeys[svc.id] ? '已配置' : '未配置' }}
                </el-tag>
              </div>
              <div class="field-hint">留空表示不修改; 已配置的 Key 只回显掩码, 不会回传到浏览器</div>
            </el-form-item>

            <el-form-item label="模型名">
              <el-input
                v-model="aiForm[fieldsOf(svc.id).model]"
                :placeholder="AI_GROUP_META[svc.id].modelPlaceholder"
                clearable
              />
            </el-form-item>

            <!-- 组内额外字段: 向量=维度(空=自动探测), 重排=本地 ONNX 模型目录 -->
            <el-form-item v-if="fieldsOf(svc.id).extraKey" :label="fieldsOf(svc.id).extraLabel">
              <el-input
                v-model="aiForm[fieldsOf(svc.id).extraKey as string]"
                :placeholder="fieldsOf(svc.id).extraPlaceholder"
                clearable
              />
              <div class="field-hint">{{ fieldsOf(svc.id).extraHint }}</div>
            </el-form-item>
          </el-form>
        </div>
      </div>

      <el-descriptions v-if="healthRowsList.length" :column="1" border size="small" class="health-table">
        <el-descriptions-item v-for="row in healthRowsList" :key="row.key" :label="row.label">
          <el-tag :type="row.type" size="small">{{ row.tagLabel }}</el-tag>
          <span class="health-detail">{{ row.detail }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-if="aiFixHint"
        type="info"
        :closable="false"
        show-icon
        :title="aiFixHint"
        class="health-fix"
      />
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
          <el-form-item label="AI 功能开关">
            <el-switch v-model="form.ai_defense_enabled" active-text="开启" inactive-text="关闭" />
            <div class="field-hint">关闭后学生端 AI 模拟答辩、知识问答、PPT 大纲等接口将统一停用</div>
          </el-form-item>
          <el-form-item label="调用频率上限">
            <el-input-number v-model="form.ai_rate_limit_per_minute" :min="1" :max="999" />
            <span class="unit">次 / 分钟 / 用户</span>
          </el-form-item>
        </el-form>
      </section>

      <section class="settings-block">
        <h3 class="block-title">AI 模型（roadmap 八十五章）</h3>
        <el-form label-width="110px" label-position="left">
          <el-form-item label="LLM 模型覆盖">
            <div class="readonly-line">{{ modelInfo?.llm_model_effective || aiForm.ai_llm_model || '-' }}</div>
            <div class="field-hint">
              供应商 {{ modelInfo?.llm_provider || '-' }}；模型名与 API Key 统一在上方「AI 服务」卡片填写，
              当前生效值仅作展示{{ modelInfo && !modelInfo.llm_openai_mode ? '；当前为离线演示模式(mock)，配置 Key 后才会真正切换模型' : '' }}
            </div>
          </el-form-item>
          <el-form-item label="Embedding">
            <div class="readonly-line">
              {{ modelInfo?.embedding_model || modelInfo?.embedding_provider || '-' }}
              <span v-if="modelInfo?.embedding_model" class="dim-tag">{{ modelInfo.embedding_dim }} 维</span>
            </div>
            <div class="field-hint">Embedding 模型与维度在上方「AI 服务」卡片配置；切换后需重建知识库索引</div>
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
      title="配置修改即时生效（Redis 缓存在保存时失效）；未修改的 API Key 不会随保存提交，也不会回显明文。"
    class="footer-note" />
  </div>
</template>

<style scoped>
.settings-page { max-width: 980px; }
.header-actions { display: flex; gap: 10px; }
.status-banner { margin-bottom: 16px; }
.block-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.block-sub { color: #9aa2b0; font-size: 12px; }
.ai-service-block { margin-bottom: 22px; }
.ai-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; margin-top: 14px; }
.ai-group { border: 1px solid #eef0f5; border-radius: 8px; padding: 14px; }
.ai-group-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.ai-group-title { font-size: 14px; color: #2c3e50; }
.key-row { display: flex; align-items: center; gap: 8px; width: 100%; }
.key-badge { flex: none; }
.health-table { margin-top: 16px; }
.health-detail { margin-left: 10px; color: #606266; font-size: 12px; word-break: break-word; }
.health-fix { margin-top: 10px; }
.footer-note { margin-top: 4px; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 22px; margin-bottom: 20px; }
.settings-block { border: 1px solid #eef0f5; border-radius: 8px; padding: 18px; }
.unit { margin-left: 10px; color: #8a93a5; font-size: 12px; }
.field-hint { width: 100%; color: #9aa2b0; font-size: 12px; line-height: 1.6; margin-top: 4px; }
.readonly-line { color: #2c3e50; font-size: 14px; }
.dim-tag { margin-left: 8px; font-size: 12px; color: #93a1b4; }
@media (max-width: 800px) {
  .settings-grid { grid-template-columns: 1fr; }
  .ai-grid { grid-template-columns: 1fr; }
  .settings-page :deep(.el-form-item__label) { width: 96px !important; }
}
</style>
