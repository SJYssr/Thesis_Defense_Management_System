/**
 * AI 服务与 Agent 问答的展示工具(单源, 避免各页面各写一套)。
 *
 * 三块内容:
 * 1. Agent 多步问答: `step.kind` 的中文徽标与配色、文案截断、来源文档类型中文名;
 * 2. 系统配置: `/admin/system-configs` 的裸值归一化 + "只提交变化字段"的差异计算。
 *    密钥类字段后端只回掩码(`***abcd` 或空串)并额外给 `has_keys`, 前端绝不回传掩码 ——
 *    判定逻辑集中在这里, 页面不重复实现;
 * 3. 健康检查: `/health` 与 `/health/ai` 的状态着色、摘要、修复指引与可用性提示。
 *
 * 为什么单独成文件: AiChat(学生端展示过程) 与 SystemSettings(管理员配置+健康检查)
 * 需要同一套判定; 写两份必然分叉 —— 例如把 `***abcd` 当成新 API Key 回传, 或把
 * `not_configured` 渲染成"正常"。
 */
import { extractBackendMessage, resolveErrorMessage } from '@/utils/error'
import type { AiChatStep, AiCitationSource, AiHasKeys, AiHealthBlock, AiHealthSnapshot } from '@/types/api'

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

/** ------------------------------------------------------------ 系统配置(归一化 + 差异) */

export interface AiServiceMeta {
  id: 'llm' | 'embedding' | 'rerank'
  label: string
  /** 该组密钥的 sysconfig 键名(后端 SECRET_FIELDS) */
  secretKey: string
}

/** 三组 AI 服务: 与后端 sysconfig FIELDS 的 ai_ 前缀项一一对应 */
export const AI_SERVICES: AiServiceMeta[] = [
  { id: 'llm', label: '大模型 (LLM)', secretKey: 'ai_llm_api_key' },
  { id: 'embedding', label: '向量模型 (Embedding)', secretKey: 'ai_embedding_api_key' },
  { id: 'rerank', label: '重排模型 (Rerank)', secretKey: 'ai_rerank_api_key' },
]

/** 界面会写回的 ai_ 配置键(顺序即表单顺序; 新增字段先加后端 FIELDS 再加这里) */
export const AI_CONFIG_KEYS: string[] = [
  'ai_llm_enabled', 'ai_llm_provider', 'ai_llm_base_url', 'ai_llm_api_key', 'ai_llm_model',
  'ai_embedding_enabled', 'ai_embedding_provider', 'ai_embedding_base_url', 'ai_embedding_api_key',
  'ai_embedding_model', 'ai_embedding_dim',
  'ai_rerank_enabled', 'ai_rerank_provider', 'ai_rerank_base_url', 'ai_rerank_api_key',
  'ai_rerank_model', 'ai_rerank_model_dir',
]

export function isSecretKey(key: string): boolean {
  return AI_SERVICES.some((svc) => svc.secretKey === key)
}

/**
 * 后端 mask_secret 的输出: 空串=未配置, `***xxxx`/`***`=已配置但没有明文。
 * 只要以 `*` 开头就按掩码处理 —— 任何情况下都不把它当新密钥提交。
 */
export function isMaskedValue(value: unknown): boolean {
  return /^\*+/.test(String(value ?? '').trim())
}

/** 该段是否已配置密钥(掩码本身即表示"有值") */
export function looksConfigured(value: unknown): boolean {
  return String(value ?? '').trim().length > 0
}

/**
 * 从 GET /admin/system-configs 的任意常见形状里抽出 key -> value。
 * 后端当前返回数组, 目标形态是数组 + 同级的 has_keys; 同时兼容 {items|configs|list|values}
 * 与 `{code,message,data}` 信封 —— 后端形态一旦再调整, 页面不需要跟着改。
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
      /^(system_|school_|ai_|max_)/.test(key) && (value === null || typeof value !== 'object'))
    if (looksLikeConfig) {
      for (const [key, value] of entries) out[key] = value == null ? '' : String(value)
      return out
    }
  }
  return out
}

/** 读出 has_keys(键名可能是服务名 llm/embedding/rerank, 也可能是 api_key 字段名) */
export function extractHasKeys(body: unknown): Partial<AiHasKeys> {
  const found: Partial<AiHasKeys> = {}
  const candidates: unknown[] = [body]
  if (body && typeof body === 'object' && !Array.isArray(body)) {
    const obj = body as Record<string, unknown>
    if (obj.data) candidates.push(obj.data)
  }
  for (const node of candidates) {
    if (!node || typeof node !== 'object' || Array.isArray(node)) continue
    const raw = (node as Record<string, unknown>).has_keys
    if (!raw || typeof raw !== 'object') continue
    const source = raw as Record<string, unknown>
    for (const svc of AI_SERVICES) {
      if (typeof found[svc.id] === 'boolean') continue
      const short = source[svc.id]
      const long = source[svc.secretKey]
      if (typeof short === 'boolean') found[svc.id] = short
      else if (typeof long === 'boolean') found[svc.id] = long
    }
  }
  return found
}

/**
 * 三组"是否已配置密钥": 优先用后端下发的 has_keys, 缺失时退回掩码/非空判定。
 * 两者都要, 因为后端尚未返回 has_keys 时页面不能把"已配置"显示成"未配置"。
 */
export function resolveHasKeys(values: Record<string, string>, explicit?: Partial<AiHasKeys> | null): AiHasKeys {
  const out: AiHasKeys = { llm: false, embedding: false, rerank: false }
  for (const svc of AI_SERVICES) {
    const flag = explicit?.[svc.id]
    out[svc.id] = typeof flag === 'boolean' ? flag : looksConfigured(values[svc.secretKey])
  }
  return out
}

export interface AiConfigChangeSet {
  /** 只有真正变化的字段(未修改的 api_key 绝不出现) */
  items: Record<string, string>
  /** 被跳过的密钥字段: 空值(未修改)或掩码(后端回显) */
  skippedSecrets: string[]
}

/**
 * 只提交发生变化的字段。
 * 密钥三类跳过: 空值 = 不修改; 掩码 = 后端回显; 与基线相同 = 没动过。
 * 清空密钥不通过前端提交(那会把"未配置"和"没改"混成一次误操作)。
 */
export function collectAiConfigChanges(
  current: Record<string, string>,
  baseline: Record<string, string>,
): AiConfigChangeSet {
  const items: Record<string, string> = {}
  const skippedSecrets: string[] = []
  for (const key of AI_CONFIG_KEYS) {
    const next = String(current[key] ?? '').trim()
    const before = String(baseline[key] ?? '').trim()
    if (isSecretKey(key)) {
      if (!next || isMaskedValue(next) || next === before) {
        skippedSecrets.push(key)
        continue
      }
      items[key] = next
      continue
    }
    if (next === before) continue
    items[key] = next
  }
  return { items, skippedSecrets }
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

/** 五段探针 -> 表格行(测试连接按钮与状态带共用) */
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
 * 指到本页的 AI 服务卡片, 并点明换向量模型要重建知识库。
 */
export function aiHealthFixHint(health?: AiHealthSnapshot | null): string {
  const rows = healthRows(health)
  const broken = rows.filter((row) => row.type === 'danger' || row.type === 'warning'
    || row.status === 'not_configured')
  if (!broken.length) return ''
  const names = broken.map((row) => row.label).join(' / ')
  const unconfigured = broken.some((row) => row.status === 'not_configured')
  if (unconfigured) {
    return '修复指引: 在下方「AI 服务」卡片为 ' + names
      + ' 填写 Base URL / API Key / 模型名后保存(保存即生效, 无需重启); '
      + '使用本地模型时确认 Ollama 端口可达; 更换向量模型或维度后需要在知识库页重建索引。'
  }
  return '排查指引: ' + names + ' 已配置但探测失败, 先核对 Base URL 与 Key 是否有效、模型名是否存在, 再保存重试。'
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
