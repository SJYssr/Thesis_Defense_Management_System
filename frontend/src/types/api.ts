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
