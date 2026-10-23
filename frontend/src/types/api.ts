/**
 * 后端统一响应信封。响应拦截器(api/request.ts:61)返回的是整个 {code,message,data}，
 * 所以凡是要写 axios 泛型 R 的地方都必须用 ApiEnvelope<T>，取值走 .data，
 * 不能把 R 直接写成 T（那只是把类型骗过去，运行期 .items 会是 undefined）。
 */
export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

/** 后端 ResponseUtil.page / page_dict 的统一分页结构 */
export interface PageResult<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

/** backend/app/modules/ai 的 AIDocumentType 枚举(强校验, 非法值 → 42200) */
export type AiDocumentType =
  | 'THESIS'
  | 'OPENING_REPORT'
  | 'MIDTERM_REPORT'
  | 'FINAL_REPORT'
  | 'TOPIC'
  | 'TEACHER_COMMENT'
  | 'DEFENSE_RULE'
  | 'REFERENCE'

export interface AiDocument {
  id: number
  document_name: string
  document_type: string
  index_status: string
  status_cn?: string
  chunk_count?: number
  version?: number
  error_message?: string | null
  file_size?: number
  file_id?: number | null
  indexed_at?: string | null
  created_at?: string
  [key: string]: unknown
}

/** 分页查询通用参数: 后端一律 page(从 1 开始, ge=1) + page_size(ge=1, le=100) */
export interface PageQuery {
  page?: number
  page_size?: number
  [key: string]: unknown
}

/** 后端分页上限: page_size / size 都不得超过 100 */
export const PAGE_SIZE_MAX = 100

/** 状态机可执行动作: 后端 _task_item() 的 allowed_actions, 按钮必须据此渲染而非推导 status */
export type StageTaskAction = 'publish' | 'close'

/** GET /stage-tasks 列表项(P7-A: allowed_actions 由后端状态机给出) */
export interface StageTask {
  id: number
  academic_year: string
  stage: string
  title: string
  description?: string | null
  requirements?: string[]
  opens_at?: string | null
  due_at?: string | null
  late_policy?: string
  status: 'DRAFT' | 'PUBLISHED' | 'CLOSED' | string
  /** 当前状态下允许的动作, 如 DRAFT → ['publish'], PUBLISHED → ['close'], CLOSED → [] */
  allowed_actions?: StageTaskAction[]
  [key: string]: unknown
}

/** GET /stage-tasks/{id}/versions 单项(P7-A: 该接口已由数组改为分页对象) */
export interface StageTaskVersion {
  version_no: number
  /** 后端返回的任务快照(旧值), 前端只展示 title 与 due_at */
  snapshot: { title?: string; due_at?: string | null; [key: string]: unknown }
  created_at?: string
}

/** POST /files/upload 响应(P7-A: 新增业务挂载与配额字段) */
export interface UploadResult {
  id: number
  original_name: string
  size: number
  extension?: string
  url?: string
  business_type?: string | null
  business_id?: number | null
  /** 当日已用字节数(含本次), 用于配额提示 */
  daily_used_bytes?: number
  [key: string]: unknown
}

/** 扩展名分组: 与后端 storage.py 的分组保持一致, 前端不要另立清单 */
export type UploadKind = 'DOCUMENT' | 'ARCHIVE' | 'IMAGE' | 'MEDIA'

/**
 * GET /files/upload-policy 响应。
 * 扩展名白名单与大小上限由后端单源给出: 此前前端 accept 写 .mp4 而后端白名单没有 .mp4,
 * 演示视频必然上传失败。前端一律据此生成 accept 并做上传前预校验。
 */
export interface UploadPolicy {
  allowed_extensions: string[]
  by_kind: Record<UploadKind, string[]>
  max_upload_size_mb: number
  /** <=0 表示不限制 */
  max_upload_daily_mb: number
}

/**
 * GET /dashboard/stats 的 pass_counts 行。
 * P7-B(D63/D64) 起每行由裸数字改为三元组, 比率必须用 value/total 自行计算;
 * 最后一行为 name='已毕业'(value=当前学年 completed_at 非空流程数)。
 */
export interface PassCountRow { name: string; value: number; total: number }

/** P8: dashboard 三接口(/stats /analytics /home)统一新增的口径声明值 */
export type DashboardScope = 'current_year' | 'all_years'

/**
 * P8: 同一个 dashboard 响应里混着两种口径, scope_map 逐字段声明该字段是「当前学年」还是
 * 「全部学年」。键可以是嵌套路径(如 'cards.risk_count'), 未声明的字段回退响应级 scope。
 * 页面标注必须读这里, 不要写死「当前学年」。
 */
export interface DashboardScopeMeta {
  scope?: DashboardScope
  /** 当前学年标识, 如 '2025-2026' */
  academic_year?: string
  scope_map?: Record<string, DashboardScope>
}

/** GET /dashboard/stats 关键字段(P8 起附带 scope/scope_map, 口径逐字段读 scope_map) */
export interface DashboardStats extends DashboardScopeMeta {
  stage_distribution?: { name: string; value: number }[]
  pass_counts?: PassCountRow[]
  /** 学生总数(原为算而未用, P7-B 起返回) */
  student_total?: number
  /** 已选题学生数(原为算而未用, P7-B 起返回) */
  selected_total?: number
  risk_distribution?: { name: string; value: number }[]
  [key: string]: unknown
}

/** POST /admin/progress/overdue-scan 响应(P7-B: 新增 mode/queued/batches, 入队路径 scanned=0) */
export interface OverdueScanResult {
  /** celery=已入队后台扫描(scanned 恒 0), sync=同步扫描完毕(scanned/batches 为真实值) */
  mode?: 'celery' | 'sync'
  queued?: boolean
  scanned?: number
  batches?: number
  [key: string]: unknown
}

export function clampPageSize(size?: number, fallback = 20): number {
  const value = Number(size)
  if (!Number.isFinite(value) || value < 1) return fallback
  return Math.min(Math.floor(value), PAGE_SIZE_MAX)
}

/** P9: 归档文件的业务用途(与后端 archive 的 file_role 取值一一对应, 前端不另立清单) */
export type ArchiveFileRole = 'THESIS' | 'DESIGN' | 'SOURCE_CODE' | 'MANUAL' | 'VIDEO' | 'OTHER'

/**
 * P9: GET /student/archive/eligibility 的 files[] —— 该流程下「可以被挂进归档包」的已上传文件。
 * file_id 是 sys_file.id, 提交时原样回填到 POST /student/archive/packages 的 files[].file_id。
 */
export interface ArchiveEligibleFile {
  file_id: number
  original_name: string
  size: number
  extension?: string | null
  uploaded_at?: string | null
}

/**
 * P9: GET /student/archive/eligibility 响应(P9-A 后端契约)。
 * eligible=false 时 reason 是人话的缺口说明(如「毕业材料尚未审核通过」), 页面必须原样展示,
 * 不要自己另拼一套条件文案 —— 判定口径只由后端 progress/material 汇总给出。
 */
export interface ArchiveEligibility {
  eligible: boolean
  reason: string | null
  cycle_id: number | null
  academic_year: string | null
  /** 该流程已存在的归档包; 无则 null */
  package_id: number | null
  package_status: 'PENDING' | 'ARCHIVED' | 'REVOKED' | null
  package_status_cn: string | null
  files: ArchiveEligibleFile[]
}

/** P9: POST /student/archive/packages 请求体(至少 1 个文件, 由后端 min_length=1 兜底) */
export interface ArchiveSubmitPayload {
  title?: string
  abstract?: string
  keywords?: string
  files: { file_id: number; file_role: ArchiveFileRole }[]
}

/** ---------------------------------------------------------------- P10: AI 过程可见 + 运行时配置 */

/**
 * POST /ai/chat/stream 的 `step` 事件(Agent 多步问答)。
 * 旧单轮路径不产生该事件, 前端必须按"没有 step 就不渲染思考区块"处理。
 * 顺序: meta -> step(0..N) -> delta(0..N) -> citations -> done。
 */
export interface AiChatStep {
  index: number
  /** plan=规划 / tool=调用工具 / observe=观察结果 / answer=作答 / forced=强制收束 */
  kind: 'plan' | 'tool' | 'observe' | 'answer' | 'forced' | string
  thought: string
  tool: string | null
  args: Record<string, unknown> | null
  observation: string | null
  elapsed_ms: number
}

/** citations 事件的单条来源 */
export interface AiCitationSource {
  ref: string | number | null
  document_type: string
  section: string
  content: string
}

/** citations 事件: checked=false 表示核验未执行, 前端不要渲染来源/重写/移除任何块 */
export interface AiCitationsPayload {
  sources: AiCitationSource[]
  checked: boolean
  /** 回答是否因引用核验不通过被重写 */
  regenerated: boolean
  /** 被判定无法核验的引用编号 */
  invalid: number[]
}

/** done 事件的附加字段(degraded 由后端在降级作答时给出) */
export interface AiChatDonePayload {
  degraded?: boolean
  status?: string
  invalid_citations?: number[]
  steps?: number
}

/** GET /health/ai 单段探针。status 取值见 backend/app/ai/health.py */
export interface AiHealthBlock {
  status?: 'ok' | 'degraded' | 'down' | 'not_configured' | 'disabled' | 'unknown' | string
  detail?: string
  provider?: string
  /** 该段是否有可用凭据/来源(false 时 status 通常是 not_configured) */
  configured?: boolean
  [key: string]: unknown
}

/** GET /health/ai: 五段探针裸结构(backend/app/main.py 直接返回, 不经 ResponseUtil 信封) */
export interface AiHealthSnapshot {
  llm?: AiHealthBlock
  embedding?: AiHealthBlock
  rerank?: AiHealthBlock
  vector_store?: AiHealthBlock
  ollama?: AiHealthBlock
  [key: string]: unknown
}

/** GET /health 顶层探活(裸结构): status 只在 llm 与 embedding 都 ok 时才是 ok */
export interface HealthSnapshot {
  status: 'ok' | 'degraded' | 'not_configured' | string
  app?: string
  redis?: boolean
  llm_provider?: string
  ai?: AiHealthSnapshot
  [key: string]: unknown
}

/** GET /admin/system-configs 单项(密钥类字段的 value 是掩码, 形如 ***abcd 或空串) */
export interface SysConfigItem {
  key: string
  value: string
  description?: string
  type?: 'str' | 'bool' | 'int' | string
}

/** AI 三组服务的凭据"是否已配置", 后端随配置一起下发(前端不要用掩码自己猜) */
export interface AiHasKeys {
  llm: boolean
  embedding: boolean
  rerank: boolean
}

