/**
 * AI 相关展示工具 + AI 管理端接口封装(单源, 避免各页面各写一套)。
 *
 * 四块内容:
 * 1. Agent 多步问答: step.kind 的中文徽标与配色、文案截断、来源文档类型中文名;
 * 2. 系统配置读取: /admin/system-configs 的裸值归一化(只管非 AI 字段, 如上传上限);
 * 3. 健康检查: /health 与 /health/ai 的状态着色、摘要、修复指引与可用性提示;
 * 4. AI 管理端: 供应商(预设/列表)与三个能力位绑定的类型 + api 封装。
 *
 * 为什么单独成文件: AiChat(学生端展示过程) 与 SystemSettings(管理员配置+健康检查)
 * 需要同一套判定; 写两份必然分叉 —— 例如把 ***abcd 当成已配置密钥再回传, 或把
 * not_configured 渲染成"正常"。
 *
 * 为什么没有"三组 AI 配置键"了: AI 配置的唯一来源是数据库里的 ai_provider 表 +
 * 能力绑定键, 唯一解析入口是后端 resolve_ai_config()。前端只经 /admin/ai/providers
 * 与 /admin/ai/bindings 读写 —— 不再用 PUT /admin/system-configs 写 ai_ 前缀的
 * 提供商/模型键, 那条口径连同 16 个旧键已被后端删除。
 */
import request from '@/api/request'
import { extractBackendMessage, resolveErrorMessage } from '@/utils/error'
import type {
  AiChatStep,
  AiCitationSource,
  AiHealthBlock,
  AiHealthSnapshot,
  ApiEnvelope,
} from '@/types/api'

/** 文档类型中文名(与后端 AIDocumentType 枚举一一对应; AiChat 引用来源展示用) */
export const DOCUMENT_TYPE_CN: Record<string, string> = {
  THESIS: '论文', OPENING_REPORT: '开题报告', MIDTERM_REPORT: '中期报告', FINAL_REPORT: '终期报告',
  TOPIC: '题目信息', TEACHER_COMMENT: '教师评语', DEFENSE_RULE: '答辩规范', REFERENCE: '参考资料',
}

export function documentTypeLabel(type?: string | null): string {
  const key = String(type ?? '').trim()
  if (!key) return '未知类型'
  return DOCUMENT_TYPE_CN[key] ?? key
}

export type TagType = 'success' | 'warning' | 'info' | 'danger' | 'primary'

/** step.kind 徽标: 文案与配色集中在这里, 页面只负责渲染 */
export const STEP_KIND_META: Record<string, { label: string; type: TagType }> = {
  plan: { label: '规划', type: 'info' },
  tool: { label: '查询', type: 'primary' },
  observe: { label: '观察', type: 'info' },
  answer: { label: '作答', type: 'success' },
  forced: { label: '强制收束', type: 'warning' },
}

export function stepKindMeta(kind?: string | null): { label: string; type: TagType } {
  const key = String(kind ?? '').trim()
  return STEP_KIND_META[key] ?? { label: key || '步骤', type: 'info' }
}

/** 超长截断: 折叠区只给摘要, 完整内容留给后端日志, 不把界面撑爆 */
export function truncate(text: string | null | undefined, max = 120): string {
  const value = String(text ?? '').replace(/\s+/g, ' ').trim()
  if (value.length <= max) return value
  return value.slice(0, max) + '…'
}

/** 一步的正文文案: forced 步后端把原因放在 observation 或 thought */
export function stepText(step: AiChatStep, max = 120): string {
  const primary = step.kind === 'forced' ? (step.observation || step.thought) : step.thought
  return truncate(primary, max)
}

/** 一条引用的摘要行: 编号 + 类型中文 + section + content 摘要 */
export function citationLine(source: AiCitationSource, max = 160): { meta: string; body: string } {
  const section = String(source?.section ?? '').trim()
  const meta = section ? documentTypeLabel(source?.document_type) + ' · ' + section : documentTypeLabel(source?.document_type)
  return { meta, body: truncate(source?.content, max) }
}

/** ------------------------------------------------------------ 系统配置(归一化) */

/**
 * 从 GET /admin/system-configs 的任意常见形状里抽出 key -> value。
 * 后端当前返回数组, 目标形态是数组 + 同级的 has_keys; 同时兼容 {items|configs|list|values}
 * 与 {code,message,data} 信封 —— 后端形态一旦再调整, 页面不需要跟着改。
 *
 * 只服务本页的非 AI 字段(系统名称/上传上限/调用频率)。AI 供应商与模型绑定不在这里,
 * 见文件末尾的 aiAdminApi。
 */
export function normalizeConfigValues(body: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  const pushItem = (item: unknown) => {
    if (!item || typeof item !== 'object') return
    const row = item as Record<string, unknown>
    const key = typeof row.key === 'string' ? row.key : ''
    if (!key) return
    out[key] = row.value == null ? '' : String(row.value)
  }
  const envelope = body && typeof body === 'object' && !Array.isArray(body) ? body as Record<string, unknown> : null
  const node = envelope && 'data' in envelope ? envelope.data : body
  if (Array.isArray(node)) {
    node.forEach(pushItem)
    return out
  }
  if (node && typeof node === 'object') {
    const obj = node as Record<string, unknown>
    for (const field of ['items', 'configs', 'list']) {
      const list = obj[field]
      if (Array.isArray(list)) {
        list.forEach(pushItem)
        return out
      }
    }
    const values = obj.values
    if (values && typeof values === 'object') {
      for (const [key, value] of Object.entries(values as Record<string, unknown>)) {
        out[key] = value == null ? '' : String(value)
      }
      return out
    }
    // PUT /admin/system-configs 的 data 直接就是 {key: value}(没有 items 包装),
    // 用"键名有已知前缀"判断, 避免把 {code, message} 这类信封误当配置值。
    const entries = Object.entries(obj)
    const looksLikeConfig = entries.length > 0 && entries.every(([key, value]) =>
      /^(system_|school_|ai_defense|ai_rate|max_)/.test(key) && (value === null || typeof value !== 'object'))
    if (looksLikeConfig) {
      for (const [key, value] of entries) out[key] = value == null ? '' : String(value)
      return out
    }
  }
  return out
}

/** ------------------------------------------------------------ 健康检查 */

export interface AiHealthSectionMeta {
  key: 'llm' | 'embedding' | 'rerank' | 'vector_store' | 'ollama'
  label: string
}

/** 五段探针的展示顺序与中文名(顺序与 GET /health/ai 的响应一致) */
export const AI_HEALTH_SECTIONS: AiHealthSectionMeta[] = [
  { key: 'llm', label: '大模型 (LLM)' },
  { key: 'embedding', label: '向量模型 (Embedding)' },
  { key: 'rerank', label: '重排模型 (Rerank)' },
  { key: 'vector_store', label: '向量库' },
  { key: 'ollama', label: '本地 Ollama' },
]

/** 状态 -> 文案与 el-tag 配色(ok=success / degraded=warning / not_configured=info / down=danger) */
export function healthStatusMeta(status?: string | null): { type: TagType; label: string } {
  switch (String(status ?? '').trim().toLowerCase()) {
    case 'ok': return { type: 'success', label: '正常' }
    case 'degraded': return { type: 'warning', label: '降级' }
    case 'not_configured': return { type: 'info', label: '未配置' }
    case 'down': return { type: 'danger', label: '不可用' }
    case 'disabled': return { type: 'info', label: '已关闭' }
    default: return { type: 'info', label: '未知' }
  }
}

export function isHealthyStatus(status?: string | null): boolean {
  return String(status ?? '').trim().toLowerCase() === 'ok'
}

/**
 * 单段探针的归一化状态。
 * vector_store 只给 {mode, detail}, ollama 只给 {reachable, ...} —— 没有 status 的段
 * 必须由这里换算, 页面不要各自猜。
 */
export function healthBlockStatus(key: string, block?: AiHealthBlock | null): string {
  if (!block) return 'unknown'
  if (key === 'vector_store') {
    const mode = String(block.mode ?? '').trim().toLowerCase()
    if (mode === 'redis') return 'ok'
    if (mode === 'mysql') return 'degraded'
    return 'unknown'
  }
  if (key === 'ollama') {
    return block.reachable === true ? 'ok' : 'not_configured'
  }
  const status = String(block.status ?? '').trim().toLowerCase()
  return status || 'unknown'
}

/** 单段详情文案: detail 优先, 其次 provider/mode, 都没有也要给一句人话 */
export function healthBlockDetail(key: string, block?: AiHealthBlock | null): string {
  if (!block) return '后端未返回该段探针'
  const detail = String(block.detail ?? '').trim()
  if (detail) return detail
  if (key === 'vector_store' && block.mode) return '向量库模式: ' + String(block.mode)
  const provider = String(block.provider ?? '').trim()
  if (provider) return 'provider: ' + provider
  return '无详情'
}

export interface AiHealthRow {
  key: string
  label: string
  status: string
  type: TagType
  tagLabel: string
  detail: string
}

/** 五段探针 -> 表格行(状态带与测试连接按钮共用) */
export function healthRows(health?: AiHealthSnapshot | null): AiHealthRow[] {
  return AI_HEALTH_SECTIONS.map((section) => {
    const block = health ? health[section.key] : null
    const status = healthBlockStatus(section.key, block)
    const meta = healthStatusMeta(status)
    return {
      key: section.key,
      label: section.label,
      status,
      type: meta.type,
      tagLabel: meta.label,
      detail: healthBlockDetail(section.key, block),
    }
  })
}

/** 第一条需要管理员处理的原因(降级详情), 供页头状态带使用 */
export function healthIssueDetail(health?: AiHealthSnapshot | null): string {
  const row = healthRows(health).find((item) => item.type === 'danger' || item.type === 'warning' || item.status === 'not_configured')
  return row ? row.label + ': ' + row.detail : ''
}

/**
 * 未配置/降级时的修复指引。not_configured 是"还没填", 不是故障 —— 文案要能直接照做:
 * 指到本页的供应商与绑定区, 并点明换向量模型要重建知识库。
 */
export function aiHealthFixHint(health?: AiHealthSnapshot | null): string {
  const rows = healthRows(health)
  const broken = rows.filter((row) => row.type === 'danger' || row.type === 'warning'
    || row.status === 'not_configured')
  if (!broken.length) return ''
  const names = broken.map((row) => row.label).join(' / ')
  const unconfigured = broken.some((row) => row.status === 'not_configured')
  if (unconfigured) {
    return '修复指引: 在下方「供应商」添加一个供应商(填 Base URL 与 API Key)并点「刷新模型」, '
      + '再到「能力绑定」把 ' + names + ' 指向该供应商与具体模型名; 保存即生效, 无需重启。'
      + '使用本地模型时确认 Ollama 端口可达; 更换向量模型或维度后需要在知识库页重建索引。'
  }
  return '排查指引: ' + names + ' 已绑定但探测失败, 先核对供应商的 Base URL 与 Key 是否有效、模型名是否存在, '
    + '再点该供应商的「刷新模型」重试。'
}

/** 学生端可用性提示: 未配置或降级必须可见, 不允许静默 */
export interface AiNotice {
  type: 'info' | 'warning'
  title: string
}

export function aiAvailabilityNotice(health?: AiHealthSnapshot | null): AiNotice | null {
  if (!health) return null
  const rows = healthRows(health).filter((row) => row.key === 'llm' || row.key === 'embedding')
  const notConfigured = rows.some((row) => row.status === 'not_configured')
  const broken = rows.filter((row) => !isHealthyStatus(row.status) && row.status !== 'disabled' && row.status !== 'not_configured')
  if (notConfigured) {
    return { type: 'info', title: '尚未配置 AI 服务, AI 问答暂不可用: ' + healthIssueDetail(health) }
  }
  if (broken.length) {
    return { type: 'warning', title: 'AI 服务异常: ' + broken[0].label + ' ' + broken[0].detail }
  }
  return null
}

/** ------------------------------------------------------------ 错误文案 */

/** 统一从请求异常里取 message: 后端 message > code 映射 > HTTP 状态 > 兜底(utils/error.ts 单源) */
export function describeRequestError(error: unknown): string {
  const err = error as { response?: { data?: unknown; status?: number }; message?: string } | null
  const data = err?.response?.data as Record<string, unknown> | undefined
  const code = typeof data?.code === 'number' ? data.code : null
  const backendMessage = extractBackendMessage(data)
  if (backendMessage || code != null) {
    return resolveErrorMessage(code, backendMessage, err?.response?.status ?? null)
  }
  return resolveErrorMessage(null, err?.message ?? null, err?.response?.status ?? null)
}

/** ------------------------------------------------------------ AI 管理端(供应商 + 能力绑定) */

/** GET /admin/ai/presets 一条: 代码内置预设(不是环境变量), 选中即填好 Base URL */
export interface AiPreset {
  kind: string
  label: string
  base_url: string
  /** false = 该供应商不需要 Key(本地 ollama) */
  needs_key: boolean
  /** 推荐模型名: 绑定区的模型下拉在"还没拉过真实清单"时用它兜底 */
  models: string[]
  note: string
}

/** kind -> 中文标签; 后端 KINDS 是封闭集合, 未知 kind 原样显示 */
export const PROVIDER_KIND_LABEL: Record<string, string> = {
  openai: 'OpenAI 兼容',
  deepseek: 'DeepSeek',
  zhipu: '智谱 GLM',
  dashscope: '阿里云百炼',
  moonshot: 'Moonshot',
  ollama: '本地 Ollama',
  custom: '自定义',
}

export function providerKindLabel(kind?: string | null): string {
  const key = String(kind ?? '').trim().toLowerCase()
  if (!key) return '未知类型'
  return PROVIDER_KIND_LABEL[key] ?? key
}

/**
 * GET /admin/ai/providers 一条。永不含明文密钥: 只有 has_key 与后四位 key_hint。
 * cipher_broken=true 表示库里存着密文但当前 secret_key 解不开(换过密钥) —— 必须显式提示,
 * 否则管理员会看到"有 Key 但一直 401"。
 */
export interface AiProvider {
  id: number
  name: string
  kind: string
  base_url: string
  openai_base_url: string
  enabled: boolean
  has_key: boolean
  key_hint: string
  cipher_broken: boolean
  models: string[]
  models_refreshed_at: string | null
  last_status: string | null
  last_detail: string | null
  last_probe_at: string | null
}

/** POST /providers/{id}/refresh 与 /test 的同形返回(连接失败也 code=0) */
export interface AiProbeResult {
  status: 'ok' | 'down' | 'unauthorized' | string
  detail: string
  models: string[]
  latency_ms: number | null
  provider: AiProvider
}

/** 探测状态 -> el-tag 配色: ok=success / unauthorized=warning / down=danger */
export function providerStatusMeta(status?: string | null): { type: TagType; label: string } {
  switch (String(status ?? '').trim().toLowerCase()) {
    case 'ok': return { type: 'success', label: '可用' }
    case 'unauthorized': return { type: 'warning', label: '鉴权失败' }
    case 'down': return { type: 'danger', label: '不可用' }
    default: return { type: 'info', label: '未探测' }
  }
}

/**
 * 一个能力位(会话/向量/精排)的绑定。
 * broken=true 表示供应商被删/停用 —— 页面必须显式提示, 不能静默当"未配置"。
 */
export interface AiBindingSlot {
  label: string
  provider_id: number | null
  provider_name: string
  model: string
  configured: boolean
  broken: boolean
  provider_enabled: boolean
}

export type AiSlotKey = 'chat' | 'embedding' | 'rerank'

/** GET /admin/ai/bindings 与 PUT 的同形返回 */
export interface AiBindings {
  enabled: boolean
  embedding_dim: number | null
  slots: Record<AiSlotKey, AiBindingSlot>
}

/** GET /admin/ai/overview: 供应商列表 + 绑定 + 三个能力位的真实探测结果 */
export interface AiOverview extends AiBindings {
  providers: AiProvider[]
  /** 后端当前字段名是 probe(probe_all 的输出); health 是同义的兼容名, 两者取其一 */
  probe?: AiHealthSnapshot
  health?: AiHealthSnapshot
  needs_reindex: boolean
}

/** overview 里探针段的取值(兼容 probe / health 两种字段名) */
export function overviewProbe(overview?: AiOverview | null): AiHealthSnapshot | null {
  if (!overview) return null
  return overview.probe ?? overview.health ?? null
}

/** POST /admin/ai/providers 的请求体 */
export interface AiProviderCreate {
  name: string
  kind: string
  base_url: string
  api_key?: string
  enabled: boolean
}

/**
 * PUT /admin/ai/providers/{id} 的请求体(任意子集)。
 * 密钥三态: 不传/空串 = 不修改; 填新值 = 覆盖; clear_key=true = 清空。
 */
export interface AiProviderUpdate {
  name?: string
  kind?: string
  base_url?: string
  api_key?: string
  clear_key?: boolean
  enabled?: boolean
}

/**
 * PUT /admin/ai/bindings 的请求体。
 * provider_id 与 model 缺一不可(只传一个后端直接 400); provider_id=null 表示解除该能力位绑定。
 */
export interface AiBindingsUpdate {
  enabled?: boolean
  chat?: { provider_id: number | null; model?: string | null } | null
  embedding?: { provider_id: number | null; model?: string | null } | null
  rerank?: { provider_id: number | null; model?: string | null } | null
}

/**
 * AI 管理端接口(全部需要管理员令牌, /api/v1 前缀已由 request.ts 带上)。
 * 只走这四个端点: presets / providers / bindings / overview —— 页面不再拼任何
 * ai_ 前缀的配置键, 也不再 PUT /admin/system-configs。
 */
export const aiAdminApi = {
  presets: () => request.get<unknown, ApiEnvelope<AiPreset[]>>('/admin/ai/presets'),
  providers: () => request.get<unknown, ApiEnvelope<AiProvider[]>>('/admin/ai/providers'),
  createProvider: (data: AiProviderCreate) =>
    request.post<unknown, ApiEnvelope<AiProvider>>('/admin/ai/providers', data),
  updateProvider: (id: number, data: AiProviderUpdate) =>
    request.put<unknown, ApiEnvelope<AiProvider>>(`/admin/ai/providers/${id}`, data),
  /** 被绑定或不存在时后端 400, message 是中文原因 —— 直接展示给管理员 */
  deleteProvider: (id: number) =>
    request.delete<unknown, ApiEnvelope<{ deleted: number }>>(`/admin/ai/providers/${id}`),
  /** 拉模型清单: 同时验证 Base URL / Key / 网络, 结果落库 */
  refreshModels: (id: number) =>
    request.post<unknown, ApiEnvelope<AiProbeResult>>(`/admin/ai/providers/${id}/refresh`),
  testProvider: (id: number) =>
    request.post<unknown, ApiEnvelope<AiProbeResult>>(`/admin/ai/providers/${id}/test`),
  bindings: () => request.get<unknown, ApiEnvelope<AiBindings>>('/admin/ai/bindings'),
  saveBindings: (data: AiBindingsUpdate) =>
    request.put<unknown, ApiEnvelope<AiBindings>>('/admin/ai/bindings', data),
  overview: () => request.get<unknown, ApiEnvelope<AiOverview>>('/admin/ai/overview'),
}
