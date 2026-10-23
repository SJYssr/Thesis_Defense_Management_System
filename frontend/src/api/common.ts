import request from './request'
import { clampPageSize, type AiDocument, type AiDocumentType, type ApiEnvelope, type PageResult, type UploadPolicy, type UploadResult } from '@/types/api'

export const aiApi = {
  /**
   * P3: GET /ai/documents 已改为 page/size 分页(size ≤ 100)，返回 { items, page, page_size, total }。
   * 注意后端这里的分页参数名是 size(不是 page_size)，且 size 上限 100。
   */
  listDocuments: (page = 1, size = 20) =>
    request.get<unknown, ApiEnvelope<PageResult<AiDocument>>>('/ai/documents', {
      params: { page: Math.max(1, Math.floor(page) || 1), size: clampPageSize(size) },
    }),
  ragConfig: () => request.get('/ai/rag/config'),
  indexDocument: (id: number) => request.post(`/ai/documents/${id}/index`),
  documentStatus: (id: number) => request.get(`/ai/documents/${id}/status`),
  indexReports: () => request.post('/ai/documents/index-reports'),
  /**
   * document_types 在后端是枚举强校验(list[AIDocumentType])，传非法值直接 42200，
   * 所以这里只允许枚举值；undefined 表示不过滤。
   */
  ragQuery: (data: { query: string; top_k?: number; document_types?: AiDocumentType[] }) =>
    request.post('/ai/rag/query', data),
  generateQuestions: () => request.post('/ai/defense/questions/generate'),
  createSession: (questionCount: number) => request.post(`/ai/defense/sessions?question_count=${questionCount}`),
  startSession: (id: number) => request.post(`/ai/defense/sessions/${id}/start`),
  answer: (id: number, data: { question: string; answer: string }) =>
    request.post(`/ai/defense/sessions/${id}/answer`, data),
  next: (id: number) => request.post(`/ai/defense/sessions/${id}/next`),
  sessions: () => request.get('/ai/defense/sessions'),
  sessionDetail: (id: number) => request.get(`/ai/defense/sessions/${id}`),
  report: (id: number) => request.get(`/ai/defense/sessions/${id}/report`),
  pptOutline: () => request.post('/ai/ppt-outline/generate'),
  exportPptx: (data: unknown) => request.post('/ai/ppt-outline/export', data, { responseType: 'blob' }),
  analyseProgress: (studentId: number) => request.post(`/ai/progress/student/${studentId}/analyse`),
}

export const dashboardApi = {
  home: () => request.get('/dashboard/home'),
  stats: () => request.get('/dashboard/stats'),
  analytics: () => request.get('/dashboard/analytics'),
  adminActivity: () => request.get('/dashboard/admin-activity'),
  teacherActivity: () => request.get('/dashboard/teacher-activity'),
}

/** P7-A: POST/PUT /admin/announcements 支持 status(DRAFT|PUBLISHED, 缺省 PUBLISHED) */
export interface AnnouncementPayload {
  title: string
  content: string
  priority?: 'NORMAL' | 'IMPORTANT' | 'URGENT'
  expire_time?: string | null
  status?: 'DRAFT' | 'PUBLISHED'
  [key: string]: unknown
}

export const announcementApi = {
  list: (params: Record<string, unknown>) => request.get('/announcements', { params }),
  detail: (id: number) => request.get(`/announcements/${id}`),
  adminList: (params: Record<string, unknown>) => request.get('/admin/announcements', { params }),
  /** 传 status: 'DRAFT' 保存为草稿, 缺省 = 直接发布 */
  create: (data: AnnouncementPayload) => request.post('/admin/announcements', data),
  /**
   * 注意: 已发布的公告不能改回草稿(后端 40003「公告已发布, 不能退回草稿」),
   * 下线请调用 remove() —— 它的语义是 transition(offline) 而非物理删除。
   */
  update: (id: number, data: AnnouncementPayload) => request.put(`/admin/announcements/${id}`, data),
  /** 语义 = 下线(OFFLINE), 不是物理删除 */
  remove: (id: number) => request.delete(`/admin/announcements/${id}`),
}

export const notificationApi = {
  list: (params: Record<string, unknown>) => request.get('/notifications', { params }),
  markRead: (id: number) => request.post(`/notifications/${id}/read`),
  markAllRead: () => request.post('/notifications/read-all'),
}

export const userAdminApi = {
  list: (role: string, params: Record<string, unknown>) => request.get(`/admin/users/${role}`, { params }),
  create: (role: string, data: Record<string, unknown>) => request.post(`/admin/users/${role}`, data),
  update: (role: string, id: number, data: Record<string, unknown>) => request.put(`/admin/users/${role}/${id}`, data),
  remove: (role: string, id: number) => request.delete(`/admin/users/${role}/${id}`),
  colleges: () => request.get('/org/colleges'),
  majors: (collegeId?: number) => request.get('/org/majors', { params: { college_id: collegeId } }),
}

export const operationLogApi = {
  list: (params: Record<string, unknown>) => request.get('/admin/operation-logs', { params }),
}

export const uploadApi = {
  /**
   * 上传策略(扩展名白名单 + 单文件上限 + 日配额), 与 /files/upload 的后端校验同源。
   * 前端据此生成 accept 与上传前预校验, 避免 accept 与后端白名单错配。
   */
  policy: () => request.get<unknown, ApiEnvelope<UploadPolicy>>('/files/upload-policy'),
  /**
   * P7-A: 新增可选业务挂载字段 business_type(≤32) / business_id(≥1),
   * 响应带 business_type/business_id/daily_used_bytes;
   * 当日配额用尽返回 40013(与单文件超限 40010 是不同错误, 文案不可合并)。
   */
  file: (file: File, options: { business_type?: string; business_id?: number | string } = {}) => {
    const form = new FormData()
    form.append('file', file)
    if (options.business_type) form.append('business_type', options.business_type.slice(0, 32))
    if (options.business_id !== undefined && options.business_id !== null && options.business_id !== '') {
      form.append('business_id', String(options.business_id))
    }
    return request.post<unknown, ApiEnvelope<UploadResult>>('/files/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
}

/**
 * 系统配置(P10): 管理员侧 `/admin/system-configs`。
 *
 * 关于 `has_keys`: 后端在返回配置列表时附带三组 AI 服务的"密钥是否已配置"
 * (`{"llm": bool, "embedding": bool, "rerank": bool}`), 密钥本身只回掩码
 * (`***abcd` 或空串)。两个形状都不加类型泛型 —— 后端正在把返回形态从
 * "纯数组" 调整为 "数组/对象 + has_keys"(与 process 无关的字段变化), 泛型写死
 * 只会让这里变成第二份错误的契约; 归一化统一走 `utils/aiService.ts`
 * 的 `normalizeConfigValues` / `extractHasKeys`。
 */
export const sysConfigApi = {
  /** GET /admin/system-configs: 密钥字段的值是掩码, 不是明文 */
  list: () => request.get('/admin/system-configs'),
  /**
   * PUT /admin/system-configs: 批量更新 `{items: {key: value}}`。
   * 后端 pydantic 限制 items 长度 1..20, 且**未知 key 整体拒绝(40001)** ——
   * 所以调用方只能提交真正变化的字段, 不能把整张表单全量塞进来。
   */
  update: (items: Record<string, string>) => request.put('/admin/system-configs', { items }),
  aiModelInfo: () => request.get('/admin/ai-model-info'),
}

/**
 * 健康检查(P10): `/health` 与 `/health/ai`.
 *
 * 这两个端点**直接返回裸对象**(backend/app/main.py), 不走 ResponseUtil 信封,
 * 所以 axios 拦截器不会改写它们, 也**不能**声明成 `ApiEnvelope<T>`(那会撒谎,
 * 并触发 check-api-contract 的"泛型必须 ApiEnvelope"断言的反向错误)。
 * 调用方用 `HealthSnapshot` / `AiHealthSnapshot` 自行断言类型。
 */
export const healthApi = {
  /** 顶层探活: status 可能是 ok / degraded / not_configured */
  status: () => request.get('/health'),
  /** 五段 AI 探针: llm / embedding / rerank / vector_store / ollama */
  ai: () => request.get('/health/ai'),
}

